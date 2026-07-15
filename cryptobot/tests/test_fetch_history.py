"""fetch_history.py のページング/重複排除ロジックのテスト(ネットワーク不要)。"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fetch_history import fetch_all

HOUR_MS = 3600_000


class FakeExchange:
    """bitbankの「日次バケットに空白がある」挙動を模した偽取引所。"""

    def __init__(self, candles: list[list], gap_start: int, gap_end: int):
        self.candles = candles
        self.gap_start = gap_start
        self.gap_end = gap_end
        self.rateLimit = 0
        self.calls = 0

    def parse_timeframe(self, tf: str) -> int:
        return 3600  # 1h

    def fetch_ohlcv(self, symbol, timeframe, since=None, limit=1000):
        self.calls += 1
        if self.gap_start <= since < self.gap_end:
            return []  # データ空白期間(bitbankの日次バケット境界などを模す)
        return [c for c in self.candles if c[0] >= since][:limit]


class TestFetchAll(unittest.TestCase):
    def test_dedup_and_gap_skip(self):
        base = 1_700_000_000_000
        candles = [[base + i * HOUR_MS, 1, 1, 1, 1, 1] for i in range(50)]
        # 途中10本ぶんを「空白期間」として抜く(取得はできるが結果セットには無い)
        gap_start = candles[20][0]
        gap_end = candles[30][0]
        candles = [c for c in candles if not (gap_start <= c[0] < gap_end)]
        fake = FakeExchange(candles, gap_start, gap_end)

        result = fetch_all(fake, "BTC/JPY", "1h", base, base + 50 * HOUR_MS)
        timestamps = [c[0] for c in result]
        self.assertEqual(timestamps, sorted(set(timestamps)))  # 重複なし
        self.assertEqual(len(result), len(candles))
        self.assertLess(fake.calls, 100)  # 空白を無限ポーリングしていない

    def test_empty_source_terminates(self):
        fake = FakeExchange([], 0, 0)
        result = fetch_all(fake, "BTC/JPY", "1h", 0, 10 * HOUR_MS)
        self.assertEqual(result, [])
        self.assertLess(fake.calls, 100)  # stall_guardで打ち切られる


if __name__ == "__main__":
    unittest.main(verbosity=2)
