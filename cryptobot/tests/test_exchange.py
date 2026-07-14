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
