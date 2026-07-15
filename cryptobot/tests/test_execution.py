"""MakerExecutorの二重発注ガードのテスト(ネットワーク不要)。

推敲レビューで見つけたバグの回帰テスト: 未知の注文ステータス(取得失敗や
想定外の文字列)を「安全(終端)」と誤判定してキャンセルを試みずに次の
指値へ進んでしまうと、古い注文が板に残ったまま新しい注文が重なる
(二重発注)。修正後は「終端状態と確認できたリストに載っているか」だけで
判定し、未知のステータスは必ずキャンセルを試みて確認するまで先に進まない。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot.config import BotConfig
from bot.execution import MakerExecutor


class FakeClient:
    def __init__(self, statuses: list[str]):
        self.statuses = list(statuses)  # fetch_orderが順に返すステータス列
        self.cancel_calls = 0
        self.fetch_order_calls = 0

    def fetch_ticker(self, symbol):
        return {"bid": 10_000_000, "ask": 10_001_000}

    def fetch_order(self, order_id, symbol):
        self.fetch_order_calls += 1
        status = self.statuses[min(self.fetch_order_calls - 1, len(self.statuses) - 1)]
        return {"id": order_id, "status": status, "filled": 0.0}

    def cancel_order(self, order_id, symbol):
        self.cancel_calls += 1


class FakeExchange:
    def __init__(self, statuses: list[str]):
        self.client = FakeClient(statuses)

    def limit_post_only(self, symbol, side, amount, price):
        return {"id": "order-1", "status": "open"}

    def base_currency(self, symbol):
        return "BTC"


class TestMakerExecutorDoubleOrderGuard(unittest.TestCase):
    def _run(self, statuses: list[str]):
        cfg = BotConfig()
        cfg.execution.requote_seconds = 0  # デッドラインを即座に迎え、テストを高速化
        cfg.execution.max_requotes = 1
        exchange = FakeExchange(statuses)
        executor = MakerExecutor(exchange, cfg)
        with patch("bot.execution.time.sleep"):  # 確認ループの待機を無効化
            result = executor.execute("BTC/JPY", "buy", 0.001)
        return result, exchange.client

    def test_unknown_status_still_attempts_cancel(self):
        # 「open」→ずっと未知のステータス「mystery」(取得エラー等を想定)。
        # 修正前は「_LIVE_STATUSESに無い=安全」と誤判定しキャンセルもせず
        # 先に進んでいた。修正後は必ずキャンセルを試みる
        result, client = self._run(["open", "mystery", "mystery", "mystery"])
        self.assertGreaterEqual(client.cancel_calls, 1)
        self.assertEqual(result.amount, 0.0)  # 終端確認できず見送り

    def test_confirmed_canceled_status_does_not_attempt_redundant_cancel(self):
        # requote_seconds=0だと最初のfetch_orderの結果でループが抜けるため、
        # 最初から「canceled」を返せば「既に終端」ケースを再現できる
        result, client = self._run(["canceled"])
        self.assertEqual(client.cancel_calls, 0)

    def test_partial_fill_on_terminal_status_is_booked(self):
        class PartialFillClient(FakeClient):
            def fetch_order(self, order_id, symbol):
                self.fetch_order_calls += 1
                return {"id": order_id, "status": "canceled", "filled": 0.0005, "average": 10_000_000}

        cfg = BotConfig()
        cfg.execution.requote_seconds = 0
        cfg.execution.max_requotes = 0  # 1回の部分約定だけを見る(再指値させない)
        exchange = FakeExchange([])
        exchange.client = PartialFillClient([])
        executor = MakerExecutor(exchange, cfg)
        with patch("bot.execution.time.sleep"):
            result = executor.execute("BTC/JPY", "buy", 0.001)
        self.assertAlmostEqual(result.amount, 0.0005)


if __name__ == "__main__":
    unittest.main(verbosity=2)
