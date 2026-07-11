"""土台の安全装置と損益計算のテスト。

実行: cd cryptobot && python -m unittest discover tests -v
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot.config import BotConfig, ConfigError, RiskConfig, validate
from bot.journal import Fill, TradeJournal
from bot.notify import Notifier
from bot.paper import PaperBroker
from bot.risk import RiskManager
from bot.strategy import Action, MACrossStrategy, MarketSnapshot


def _fill(side: str, amount: float, price: float, fee: float = 0.0) -> Fill:
    return Fill(datetime(2026, 7, 10), "test", "BTC/JPY", side, amount, price, fee)


class TestJournal(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "trades.csv"
        self.journal = TradeJournal(self.path)

    def tearDown(self):
        self.tmp.cleanup()

    def test_moving_average_cost(self):
        # 100円で1個、200円で1個買う → 移動平均取得単価150円
        self.journal.record(_fill("buy", 1, 100))
        self.journal.record(_fill("buy", 1, 200))
        self.assertAlmostEqual(self.journal.avg_cost, 150)

    def test_realized_pnl_on_sell(self):
        self.journal.record(_fill("buy", 1, 100))
        realized = self.journal.record(_fill("sell", 1, 150))
        # 円に出金しなくても売却時点で50円の実現益(=課税対象)
        self.assertAlmostEqual(realized, 50)
        self.assertAlmostEqual(self.journal.total_realized_pnl, 50)

    def test_fee_included_in_cost(self):
        self.journal.record(_fill("buy", 1, 100, fee=10))
        self.assertAlmostEqual(self.journal.avg_cost, 110)

    def test_cannot_sell_more_than_held(self):
        self.journal.record(_fill("buy", 1, 100))
        with self.assertRaises(ValueError):
            self.journal.record(_fill("sell", 2, 100))

    def test_restart_replays_csv(self):
        # 再起動(=新しいインスタンス)しても建玉・単価・累計損益が復元される
        self.journal.record(_fill("buy", 2, 100, fee=20))
        self.journal.record(_fill("sell", 1, 150))
        reloaded = TradeJournal(self.path)
        self.assertAlmostEqual(reloaded.position_amount, self.journal.position_amount)
        self.assertAlmostEqual(reloaded.avg_cost, self.journal.avg_cost)
        self.assertAlmostEqual(reloaded.total_realized_pnl, self.journal.total_realized_pnl)

    def test_broken_csv_rejected(self):
        self.path.write_text("勝手な,ヘッダー\n", encoding="utf-8")
        with self.assertRaises(ValueError):
            TradeJournal(self.path)


class TestRiskManager(unittest.TestCase):
    def setUp(self):
        self.cfg = RiskConfig(
            max_order_jpy=10_000,
            max_position_jpy=50_000,
            max_daily_loss_jpy=3_000,
            max_drawdown_pct=15,
            cooldown_minutes=60,
        )
        self.risk = RiskManager(self.cfg, budget_jpy=100_000)
        self.now = datetime(2026, 7, 10, 9, 0)

    def test_order_size_limit(self):
        d = self.risk.check_order("buy", 10_001, 0, self.now)
        self.assertFalse(d.approved)

    def test_position_limit(self):
        d = self.risk.check_order("buy", 10_000, 45_000, self.now)
        self.assertFalse(d.approved)

    def test_cooldown_blocks_buy_but_not_sell(self):
        self.assertTrue(self.risk.check_order("buy", 5_000, 0, self.now).approved)
        self.risk.record_fill(self.now, "buy")
        later = self.now + timedelta(minutes=30)
        self.assertFalse(self.risk.check_order("buy", 5_000, 0, later).approved)
        # 売り(リスク削減)はクールダウン中でも通す
        self.assertTrue(self.risk.check_order("sell", 5_000, 5_000, later).approved)
        self.assertTrue(
            self.risk.check_order("buy", 5_000, 0, self.now + timedelta(minutes=61)).approved
        )

    def test_daily_loss_stops_buys_and_resets_next_day(self):
        self.risk.record_fill(self.now, "sell", realized_pnl_jpy=-3_000)
        later = self.now + timedelta(minutes=61)
        self.assertFalse(self.risk.check_order("buy", 5_000, 0, later).approved)
        self.assertTrue(self.risk.check_order("sell", 5_000, 5_000, later).approved)
        tomorrow = self.now + timedelta(days=1, minutes=61)
        self.assertTrue(self.risk.check_order("buy", 5_000, 0, tomorrow).approved)

    def test_drawdown_halts_bot(self):
        self.risk.update_equity(100_000)
        self.risk.update_equity(84_000)  # -16% > 上限15%
        self.assertTrue(self.risk.halted)
        self.assertFalse(self.risk.check_order("buy", 5_000, 0, self.now).approved)
        self.assertFalse(self.risk.check_order("sell", 5_000, 0, self.now).approved)

    def test_halt_persists_via_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            halt_file = Path(tmp) / "HALTED"
            risk = RiskManager(self.cfg, 100_000, halt_file)
            risk.update_equity(100_000)
            risk.update_equity(80_000)
            self.assertTrue(risk.halted)
            self.assertTrue(halt_file.exists())
            # 再起動しても停止が維持される
            risk2 = RiskManager(self.cfg, 100_000, halt_file)
            self.assertTrue(risk2.halted)
            # 人間がファイルを消せば再開できる
            halt_file.unlink()
            risk3 = RiskManager(self.cfg, 100_000, halt_file)
            self.assertFalse(risk3.halted)


class TestPaperBroker(unittest.TestCase):
    def test_buy_sell_with_fees(self):
        broker = PaperBroker(100_000, fee_rate=0.001)
        amount, fee = broker.market_buy(price=10_000_000, jpy_amount=10_000)
        self.assertAlmostEqual(fee, 10)
        self.assertAlmostEqual(broker.jpy, 90_000)
        received, _ = broker.market_sell(price=10_000_000, amount=amount)
        self.assertLess(received, 10_000)  # 手数料で往復ぶん目減りする

    def test_insufficient_balance(self):
        broker = PaperBroker(1_000, fee_rate=0.001)
        with self.assertRaises(ValueError):
            broker.market_buy(price=10_000_000, jpy_amount=2_000)


class TestMACross(unittest.TestCase):
    def _market(self, closes, position=0.0):
        return MarketSnapshot(
            price=closes[-1], closes=closes, position_amount=position, position_cost_jpy=0
        )

    def test_golden_cross_buys(self):
        strategy = MACrossStrategy(fast=2, slow=3, buy_amount_jpy=5_000)
        closes = [100.0, 90.0, 80.0, 70.0, 60.0, 100.0]  # 下落後に急騰→上抜け
        signal = strategy.decide(self._market(closes))
        self.assertEqual(signal.action, Action.BUY)

    def test_insufficient_data_holds(self):
        strategy = MACrossStrategy(fast=9, slow=26, buy_amount_jpy=5_000)
        signal = strategy.decide(self._market([100.0] * 10))
        self.assertEqual(signal.action, Action.HOLD)


class TestNotifier(unittest.TestCase):
    def test_payload_formats(self):
        self.assertEqual(
            Notifier("discord", url="http://example.invalid").build_payload("hi"),
            {"content": "hi"},
        )
        self.assertEqual(
            Notifier("slack", url="http://example.invalid").build_payload("hi"),
            {"text": "hi"},
        )

    def test_none_format_never_sends(self):
        self.assertFalse(Notifier("none", url="http://example.invalid").send("hi"))

    def test_invalid_format_rejected(self):
        with self.assertRaises(ValueError):
            Notifier("line")


class TestConfigGuards(unittest.TestCase):
    def test_budget_cap_100k(self):
        cfg = BotConfig(budget_jpy=200_000)
        with self.assertRaises(ConfigError):
            validate(cfg)

    def test_live_requires_env_lock(self):
        cfg = BotConfig(mode="live")
        with self.assertRaises(ConfigError):
            validate(cfg)  # CRYPTOBOT_LIVE=YES がない


if __name__ == "__main__":
    unittest.main(verbosity=2)
