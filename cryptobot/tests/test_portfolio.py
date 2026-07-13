"""複数銘柄ポートフォリオのテスト。"""
from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot.config import BotConfig, ConfigError, validate
from bot.portfolio import PortfolioRunner, sub_config


def make_config(tmp: str, symbols: list[str]) -> BotConfig:
    cfg = BotConfig(symbols=symbols)
    cfg.journal_path = str(Path(tmp) / "trades.csv")
    cfg.paper_state_path = str(Path(tmp) / "paper.json")
    cfg.halt_file = str(Path(tmp) / "HALTED")
    cfg.shortfall_path = str(Path(tmp) / "exec.csv")  # 本番のdata/を汚さない
    cfg.risk_state_path = str(Path(tmp) / "risk_state.json")
    return cfg


class TestSubConfig(unittest.TestCase):
    def test_budget_and_risk_split(self):
        cfg = make_config(tempfile.mkdtemp(), ["BTC/JPY", "ETH/JPY", "XRP/JPY"])
        sub = sub_config(cfg, "ETH/JPY")
        self.assertEqual(sub.symbol, "ETH/JPY")
        self.assertEqual(sub.budget_jpy, 100_000 // 3)
        # リスク上限は銘柄予算を超えない
        self.assertLessEqual(sub.risk.max_position_jpy, sub.budget_jpy)
        self.assertLessEqual(sub.risk.max_order_jpy, sub.budget_jpy)
        self.assertLessEqual(sub.dca.buy_amount_jpy, sub.risk.max_order_jpy)

    def test_paths_are_isolated_per_symbol(self):
        cfg = make_config(tempfile.mkdtemp(), ["BTC/JPY", "ETH/JPY"])
        a, b = sub_config(cfg, "BTC/JPY"), sub_config(cfg, "ETH/JPY")
        self.assertNotEqual(a.journal_path, b.journal_path)
        self.assertNotEqual(a.paper_state_path, b.paper_state_path)
        self.assertNotEqual(a.halt_file, b.halt_file)

    def test_single_symbol_keeps_legacy_paths(self):
        tmp = tempfile.mkdtemp()
        cfg = make_config(tmp, ["BTC/JPY"])
        sub = sub_config(cfg, "BTC/JPY")
        self.assertEqual(sub.journal_path, cfg.journal_path)


class TestPortfolioRunner(unittest.TestCase):
    def test_symbols_trade_independently(self):
        tmp = tempfile.mkdtemp()
        cfg = make_config(tmp, ["BTC/JPY", "ETH/JPY"])
        pr = PortfolioRunner(cfg)
        now = datetime(2026, 7, 12, 9, 0)
        r1 = pr.step_symbol("BTC/JPY", now, 10_000_000, [])
        r2 = pr.step_symbol("ETH/JPY", now, 500_000, [])
        self.assertIn("BUY", r1)
        self.assertIn("BUY", r2)
        btc, eth = pr.runners["BTC/JPY"], pr.runners["ETH/JPY"]
        self.assertGreater(btc.paper.base_amount, 0)
        self.assertGreater(eth.paper.base_amount, 0)
        # 帳簿・残高は銘柄ごとに独立している
        self.assertNotAlmostEqual(btc.paper.base_amount, eth.paper.base_amount)
        self.assertEqual(btc.paper.jpy, eth.paper.jpy)  # 同額の積立なら現金も同額減る


class TestConfigSymbols(unittest.TestCase):
    def test_post_init_syncs_symbol(self):
        cfg = BotConfig(symbols=["ETH/JPY", "XRP/JPY"])
        self.assertEqual(cfg.symbol, "ETH/JPY")
        cfg2 = BotConfig(symbol="XRP/JPY")
        self.assertEqual(cfg2.symbols, ["XRP/JPY"])

    def test_duplicate_symbols_rejected(self):
        with self.assertRaises(ConfigError):
            validate(BotConfig(symbols=["BTC/JPY", "BTC/JPY"]))

    def test_too_many_symbols_for_budget(self):
        symbols = [f"C{i}/JPY" for i in range(25)]  # 10万円÷25銘柄 = 4,000円 < 5,000円
        with self.assertRaises(ConfigError):
            validate(BotConfig(symbols=symbols))


if __name__ == "__main__":
    unittest.main(verbosity=2)
