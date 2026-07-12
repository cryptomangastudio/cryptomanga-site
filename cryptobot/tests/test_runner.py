"""BotRunnerの結合テスト(ペーパーモード・偽の取引所で完結、ネットワーク不要)。"""
from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot.config import BotConfig
from bot.runner import BotRunner


class FakeExchange:
    """min_order_amountだけ返す偽の取引所(発注メソッドは意図的に持たない)。"""

    def __init__(self, min_amount: float | None):
        self._min_amount = min_amount

    def min_order_amount(self, symbol: str) -> float | None:
        return self._min_amount


def make_config(tmp: str) -> BotConfig:
    cfg = BotConfig()  # paper / dca / 10万円
    cfg.journal_path = str(Path(tmp) / "trades.csv")
    cfg.paper_state_path = str(Path(tmp) / "paper_state.json")
    cfg.halt_file = str(Path(tmp) / "HALTED")
    return cfg


class TestRunnerPaper(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = make_config(self.tmp.name)
        self.now = datetime(2026, 7, 10, 9, 0)

    def tearDown(self):
        self.tmp.cleanup()

    def test_dca_buy_and_cooldown(self):
        runner = BotRunner(self.cfg)
        result = runner.step(self.now, 10_000_000, [])
        self.assertIn("BUY", result)
        self.assertAlmostEqual(runner.paper.jpy, 100_000 - self.cfg.dca.buy_amount_jpy)
        result = runner.step(self.now + timedelta(minutes=10), 10_000_000, [])
        self.assertIn("クールダウン", result)

    def test_min_order_amount_skip(self):
        # bitFlyer相当: 最低0.001 BTC ≈ 1万円 > DCA積立3000円 → スキップして知らせる
        runner = BotRunner(self.cfg, exchange=FakeExchange(0.001))
        result = runner.step(self.now, 10_000_000, [])
        self.assertIn("最低数量", result)
        self.assertAlmostEqual(runner.paper.jpy, 100_000)  # 発注されていない

    def test_restart_keeps_books_consistent(self):
        runner = BotRunner(self.cfg)
        runner.step(self.now, 10_000_000, [])
        # 再起動: ペーパー残高(JSON)と記帳簿(CSV)の両方が復元され一致する
        runner2 = BotRunner(self.cfg)
        self.assertAlmostEqual(
            runner2.paper.base_amount, runner2.journal.position_amount, places=10
        )
        self.assertLess(runner2.paper.jpy, 100_000)

    def test_drawdown_halt_stops_and_persists(self):
        # 保有上限(5万円)まで積んでから価格が半減 → 資産全体で約25%のDD → 全停止
        self.cfg.dca.buy_amount_jpy = 10_000
        self.cfg.price_sanity_pct = 0  # このテストでは急変スキップを無効化してDD停止を検証
        runner = BotRunner(self.cfg)
        for i in range(5):
            result = runner.step(self.now + timedelta(hours=i), 10_000_000, [])
            self.assertIn("BUY", result)
        result = runner.step(self.now + timedelta(hours=6), 5_000_000, [])
        self.assertIn("停止中", result)
        self.assertTrue(Path(self.cfg.halt_file).exists())
        # 再起動しても停止のまま
        runner2 = BotRunner(self.cfg)
        self.assertIn("停止中", runner2.step(self.now + timedelta(hours=7), 5_000_000, []))


if __name__ == "__main__":
    unittest.main(verbosity=2)
