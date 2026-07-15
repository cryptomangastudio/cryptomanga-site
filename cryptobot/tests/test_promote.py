"""promote.py の判定ロジックのテスト。"""
from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot.journal import Fill, TradeJournal
from promote import check_symbol


class TestPromote(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def test_missing_journal_fails(self):
        result = check_symbol(Path(self.tmp.name) / "nope.csv", 30, 20)
        self.assertFalse(result["ok"])

    def test_insufficient_history_fails(self):
        path = Path(self.tmp.name) / "t.csv"
        journal = TradeJournal(path)
        now = datetime.now()
        journal.record(Fill(now, "x", "BTC/JPY", "buy", 1, 100, 0))
        journal.record(Fill(now, "x", "BTC/JPY", "sell", 1, 110, 0))
        result = check_symbol(path, min_days=30, min_trades=20)
        self.assertFalse(result["ok"])  # 運用日数も売却回数も足りない

    def test_sufficient_history_passes(self):
        path = Path(self.tmp.name) / "t.csv"
        journal = TradeJournal(path)
        start = datetime.now() - timedelta(days=40)
        for i in range(25):
            ts = start + timedelta(days=i)
            journal.record(Fill(ts, "x", "BTC/JPY", "buy", 1, 100, 0))
            journal.record(Fill(ts, "x", "BTC/JPY", "sell", 1, 105, 0))
        result = check_symbol(path, min_days=30, min_trades=20)
        self.assertTrue(result["ok"])
        self.assertEqual(result["sells"], 25)
        self.assertAlmostEqual(result["win_rate"], 100.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
