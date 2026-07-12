"""リサーチ8機能(多層ブレーカー・コストゲート・ケリー・レジーム・DSR等)のテスト。"""
from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot.config import BotConfig, RiskConfig
from bot.journal import Fill, TradeJournal
from bot.paper import PaperBroker
from bot.risk import RiskManager
from bot.runner import BotRunner, atr_estimate
from bot.shortfall import ShortfallLedger
from bot.validate import deflated_sharpe, sharpe_ratio, walk_forward_segments

NOW = datetime(2026, 7, 13, 9, 0)  # 月曜日


def _fill(side: str, amount: float, price: float, fee: float = 0.0, ts: datetime = NOW) -> Fill:
    return Fill(ts, "test", "BTC/JPY", side, amount, price, fee)


class FakeExchange:
    """min_order_amount と日足OHLCVだけ返す偽の取引所。"""

    def __init__(self, daily_close: float = 10_000_000, days: int = 205):
        self.daily_close = daily_close
        self.days = days

    def min_order_amount(self, symbol):
        return None

    def fetch_ohlcv(self, symbol, timeframe, limit=100):
        assert timeframe == "1d"
        n = min(limit, self.days)
        return [[i, 0, 0, 0, self.daily_close, 0] for i in range(n)]


def make_config(tmp: str, **overrides) -> BotConfig:
    cfg = BotConfig()
    cfg.journal_path = str(Path(tmp) / "trades.csv")
    cfg.paper_state_path = str(Path(tmp) / "paper.json")
    cfg.halt_file = str(Path(tmp) / "HALTED")
    cfg.shortfall_path = str(Path(tmp) / "exec.csv")
    for k, v in overrides.items():
        setattr(cfg, k, v)
    return cfg


class TestMultiLayerBreakers(unittest.TestCase):
    def setUp(self):
        self.cfg = RiskConfig(
            max_daily_loss_jpy=3_000, max_weekly_loss_jpy=7_000, max_consecutive_losses=5
        )
        self.risk = RiskManager(self.cfg, budget_jpy=100_000)

    def test_weekly_loss_blocks_buys_and_resets_next_week(self):
        # 日次上限(3,000円)には触れずに、同一週の合計で週次上限を超える
        for day in range(3):
            self.risk.record_fill(NOW + timedelta(days=day), "sell", realized_pnl_jpy=-2_500)
        blocked = self.risk.check_order("buy", 1_000, 0, NOW + timedelta(days=3))
        self.assertFalse(blocked.approved)
        self.assertIn("今週", blocked.reason)
        next_week = NOW + timedelta(days=7)
        self.assertTrue(self.risk.check_order("buy", 1_000, 0, next_week).approved)

    def test_consecutive_losses_block_and_reset_on_win(self):
        for i in range(5):
            self.risk.record_fill(NOW + timedelta(minutes=i), "sell", realized_pnl_jpy=-100)
        blocked = self.risk.check_order("buy", 1_000, 0, NOW + timedelta(hours=2))
        self.assertFalse(blocked.approved)
        self.assertIn("連敗", blocked.reason)
        self.risk.record_fill(NOW + timedelta(hours=3), "sell", realized_pnl_jpy=+50)
        self.assertTrue(self.risk.check_order("buy", 1_000, 0, NOW + timedelta(hours=4)).approved)

    def test_monthly_buy_governor(self):
        risk = RiskManager(self.cfg, 100_000, max_buys_per_month=2)
        t = NOW
        for i in range(2):
            self.assertTrue(risk.check_order("buy", 1_000, 0, t).approved)
            risk.record_fill(t, "buy")
            t += timedelta(hours=2)
        blocked = risk.check_order("buy", 1_000, 0, t)
        self.assertFalse(blocked.approved)
        self.assertIn("ガバナー", blocked.reason)
        # 翌月はリセット
        self.assertTrue(risk.check_order("buy", 1_000, 0, NOW + timedelta(days=25)).approved)


class TestKelly(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.journal = TradeJournal(Path(self.tmp.name) / "t.csv")

    def tearDown(self):
        self.tmp.cleanup()

    def _round_trip(self, buy_price, sell_price):
        self.journal.record(_fill("buy", 1, buy_price))
        self.journal.record(_fill("sell", 1, sell_price))

    def test_kelly_positive_when_winning(self):
        for _ in range(6):
            self._round_trip(100, 120)  # +20
        for _ in range(4):
            self._round_trip(100, 90)  # -10
        k = self.journal.kelly_fraction()
        # W=0.6, R=2 → f = 0.6 - 0.4/2 = 0.4
        self.assertAlmostEqual(k, 0.4, places=6)

    def test_kelly_negative_when_losing(self):
        for _ in range(8):
            self._round_trip(100, 95)
        for _ in range(2):
            self._round_trip(100, 102)
        self.assertLess(self.journal.kelly_fraction(), 0)

    def test_stats_survive_restart(self):
        self._round_trip(100, 120)
        self._round_trip(100, 90)
        reloaded = TradeJournal(self.journal.path)
        self.assertEqual(reloaded.sell_count, 2)
        self.assertEqual(reloaded.win_count, 1)


class TestRunnerGates(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def test_price_sanity_skip(self):
        cfg = make_config(self.tmp.name)
        runner = BotRunner(cfg)
        runner.step(NOW, 10_000_000, [])
        result = runner.step(NOW + timedelta(hours=2), 12_000_000, [])  # +20% > 10%
        self.assertIn("異常値防御", result)
        # 次の周期は新価格基準で再開できる
        result = runner.step(NOW + timedelta(hours=4), 12_100_000, [])
        self.assertNotIn("異常値防御", result)

    def test_dca_tilt_buys_more_below_ma(self):
        cfg = make_config(self.tmp.name)
        runner = BotRunner(cfg, exchange=FakeExchange(daily_close=10_000_000))
        result = runner.step(NOW, 9_000_000, [])  # 200日MAより10%安い
        self.assertIn("BUY", result)
        self.assertIn("積立×1.10", result)
        # 取得原価 = 3000×1.1 = 3300円
        self.assertAlmostEqual(runner.journal.position_cost_jpy, 3_300, delta=1)

    def test_regime_filter_blocks_ma_cross_below_ma(self):
        cfg = make_config(self.tmp.name, strategy="ma_cross")
        cfg.ma_cross.fast, cfg.ma_cross.slow = 2, 3
        runner = BotRunner(cfg, exchange=FakeExchange(daily_close=10_000_000))
        closes = [10_000_000.0, 9_000_000, 8_000_000, 7_000_000, 6_000_000, 9_900_000]
        result = runner.step(NOW, 9_900_000, closes)  # ゴールデンクロスだがMA未満
        self.assertIn("レジームフィルター", result)

    def test_cost_gate_blocks_tiny_edge(self):
        cfg = make_config(self.tmp.name, strategy="ma_cross")
        cfg.ma_cross.fast, cfg.ma_cross.slow = 2, 3
        cfg.regime.enabled = False
        runner = BotRunner(cfg)
        closes = [10_000.0] * 14 + [9_990, 9_980, 9_970, 9_960, 10_000]
        result = runner.step(NOW, 10_000, closes)
        self.assertIn("コストゲート", result)

    def test_kelly_negative_blocks_new_entries(self):
        cfg = make_config(self.tmp.name, strategy="ma_cross")
        cfg.ma_cross.fast, cfg.ma_cross.slow = 2, 3
        cfg.regime.enabled = False
        cfg.cost_gate.enabled = False
        # 事前に負け続きの実績を帳簿に作っておく(再起動でリプレイされる)
        journal = TradeJournal(cfg.journal_path)
        for _ in range(10):
            journal.record(_fill("buy", 1, 100))
            journal.record(_fill("sell", 1, 95))
        runner = BotRunner(cfg)
        closes = [100.0, 90, 80, 70, 60, 100]  # ゴールデンクロス
        result = runner.step(NOW, 100, closes)
        self.assertIn("ケリー推定が負", result)


class TestMakerPaperFee(unittest.TestCase):
    def test_negative_fee_is_rebate(self):
        broker = PaperBroker(100_000, fee_rate=-0.0002)
        amount, fee = broker.market_buy(price=10_000, jpy_amount=10_000)
        self.assertLess(fee, 0)
        self.assertGreater(amount, 1.0)  # リベートぶんわずかに多く買える
        received, fee2 = broker.market_sell(price=10_000, amount=amount)
        self.assertGreater(received, amount * 10_000)  # 売りでもリベート


class TestShortfall(unittest.TestCase):
    def test_slippage_sign(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = ShortfallLedger(Path(tmp) / "e.csv")
            # 買い: シグナルより高く約定 → 正(損)
            cost = ledger.record(NOW, "BTC/JPY", "buy", 100, 101, 2, fee_jpy=1)
            self.assertAlmostEqual(cost, 3)  # (101-100)*2 + 1
            # 売り: シグナルより高く約定 → 負(得)
            cost = ledger.record(NOW, "BTC/JPY", "sell", 100, 101, 2, fee_jpy=1)
            self.assertAlmostEqual(cost, -1)  # (100-101)*2 + 1


class TestValidate(unittest.TestCase):
    def test_sharpe_and_dsr(self):
        good = [0.01, 0.012, 0.008, 0.011, 0.009] * 20  # 安定して正
        self.assertGreater(sharpe_ratio(good, 365), 0)
        dsr_few = deflated_sharpe(good, n_trials=1)
        dsr_many = deflated_sharpe(good, n_trials=10_000)
        self.assertGreater(dsr_few, 0.95)
        self.assertLessEqual(dsr_many, dsr_few)  # 試行が多いほど信頼は下がる

    def test_noise_fails_dsr(self):
        noise = [0.01 if i % 2 else -0.01 for i in range(100)]
        self.assertLess(deflated_sharpe(noise, n_trials=100), 0.95)

    def test_walk_forward_segments(self):
        segs = walk_forward_segments(100, 5)
        self.assertEqual(len(segs), 5)
        self.assertEqual(segs[0], (0, 20))
        self.assertEqual(segs[-1][1], 100)


class TestAtr(unittest.TestCase):
    def test_atr_estimate(self):
        closes = [100.0 + (i % 2) for i in range(20)]  # 毎バー±1
        self.assertAlmostEqual(atr_estimate(closes), 1.0)
        self.assertIsNone(atr_estimate([100.0] * 5))


if __name__ == "__main__":
    unittest.main(verbosity=2)
