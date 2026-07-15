"""SpotOnlyExchange.fee_rates() のテスト(ネットワーク不要)。"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot.config import BotConfig
from bot.exchange import SpotOnlyExchange


class FakeCcxtClient:
    def __init__(self, markets: dict):
        self._markets = markets

    def load_markets(self):
        return self._markets

    def market(self, symbol):
        return self._markets[symbol]


class FakeDayBucketClient:
    """bitbankを模す: sinceなし=当日ぶんのみ、since指定=その日ぶんを返す。

    1時間足で、当日は数本しか無い(午前中の状態)。過去日を遡ると各日24本ある。
    """

    def __init__(self, now_ms: int, hours_today: int = 5):
        self.now_ms = now_ms
        self.hours_today = hours_today
        self.day_ms = 24 * 3600 * 1000
        self.hour_ms = 3600 * 1000
        self.calls = 0

    def milliseconds(self):
        return self.now_ms

    def parse_timeframe(self, tf):
        return 3600  # 1h

    def _day_start(self, ms):
        return ms - (ms % self.day_ms)

    def fetch_ohlcv(self, symbol, timeframe=None, since=None, limit=1000):
        self.calls += 1
        anchor = self.now_ms if since is None else since
        day0 = self._day_start(anchor)
        # 当日は hours_today 本、過去日は24本
        n = self.hours_today if day0 == self._day_start(self.now_ms) else 24
        return [[day0 + i * self.hour_ms, 1, 2, 0.5, 1.5, 10] for i in range(n)]


class TestBitbankOhlcvPagination(unittest.TestCase):
    def _exchange(self, client):
        ex = SpotOnlyExchange.__new__(SpotOnlyExchange)
        ex.cfg = BotConfig(exchange="bitbank", symbol="BTC/JPY")
        ex.client = client
        ex._markets_loaded = True
        return ex

    def test_paginates_past_days_to_reach_limit(self):
        # 当日5本しか無くても、過去日を遡って31本を満たすこと(ma_crossのデータ不足解消)
        now = 1_700_000_000_000
        client = FakeDayBucketClient(now, hours_today=5)
        ex = self._exchange(client)
        rows = ex.fetch_ohlcv("BTC/JPY", "1h", limit=31)
        self.assertGreaterEqual(len(rows), 31)
        # 時系列が昇順で重複なし
        ts = [r[0] for r in rows]
        self.assertEqual(ts, sorted(ts))
        self.assertEqual(len(ts), len(set(ts)))

    def test_single_call_enough_does_not_paginate(self):
        now = 1_700_000_000_000
        client = FakeDayBucketClient(now, hours_today=50)  # 当日で足りる想定
        ex = self._exchange(client)
        rows = ex.fetch_ohlcv("BTC/JPY", "1h", limit=31)
        self.assertEqual(client.calls, 1)  # 追加取得は走らない
        self.assertEqual(len(rows), 31)

    def test_non_bitbank_uses_single_call(self):
        now = 1_700_000_000_000
        client = FakeDayBucketClient(now, hours_today=5)
        ex = self._exchange(client)
        ex.cfg = BotConfig(exchange="bitflyer", symbol="BTC/JPY")
        rows = ex.fetch_ohlcv("BTC/JPY", "1h", limit=31)
        self.assertEqual(client.calls, 1)  # bitbank以外は遡らない


class TestFeeRates(unittest.TestCase):
    def test_returns_market_maker_taker(self):
        cfg = BotConfig(symbol="BTC/JPY")
        ex = SpotOnlyExchange.__new__(SpotOnlyExchange)  # __init__のccxt生成を避ける
        ex.cfg = cfg
        ex.client = FakeCcxtClient({"BTC/JPY": {"spot": True, "maker": -0.0002, "taker": 0.0012}})
        ex._markets_loaded = False
        maker, taker = ex.fee_rates("BTC/JPY")
        self.assertAlmostEqual(maker, -0.0002)
        self.assertAlmostEqual(taker, 0.0012)

    def test_missing_fee_returns_none(self):
        cfg = BotConfig(symbol="XRP/JPY")
        ex = SpotOnlyExchange.__new__(SpotOnlyExchange)
        ex.cfg = cfg
        ex.client = FakeCcxtClient({"XRP/JPY": {"spot": True}})
        ex._markets_loaded = False
        self.assertEqual(ex.fee_rates("XRP/JPY"), (None, None))


if __name__ == "__main__":
    unittest.main(verbosity=2)
