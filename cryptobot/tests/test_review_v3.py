"""夜間ブラッシュアップの推敲レビューで見つけたバグの回帰テスト。"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fetch_history import bucket_gap_ms, fetch_all


class FakeExchangeAllEmpty:
    """全期間データが無い(未上場前など)偽取引所。全体が打ち切られないことを確認する。"""

    def __init__(self):
        self.calls = 0

    def parse_timeframe(self, tf):
        return 3600  # 1h

    def fetch_ohlcv(self, symbol, timeframe, since=None, limit=1000):
        self.calls += 1
        return []


class TestFetchAllNoAbort(unittest.TestCase):
    def test_all_empty_does_not_abort_early_and_terminates(self):
        # 1時間足・30日ぶんを全て空バケットとして走らせる。
        # 以前の実装は20回で全体を打ち切っていたが、新実装は
        # 「1日ぶんずつ前進」なので30回強で正常に終了するはず
        hour_ms = 3600_000
        day_ms = 24 * hour_ms
        base = 1_700_000_000_000
        fake = FakeExchangeAllEmpty()
        result = fetch_all(fake, "BTC/JPY", "1h", base, base + 30 * day_ms)
        self.assertEqual(result, [])
        # 30日ぶんを1日単位で進むので、30〜35回程度で完走する(20回で打ち切られない)
        self.assertGreater(fake.calls, 25)
        self.assertLess(fake.calls, 50)

    def test_bucket_gap_matches_bitbank_bucket_size(self):
        self.assertEqual(bucket_gap_ms("1h"), 24 * 3600 * 1000)  # 日次バケット
        self.assertEqual(bucket_gap_ms("1d"), 365 * 24 * 3600 * 1000)  # 年次バケット

    def test_gap_skip_does_not_overshoot_real_data(self):
        # 空白期間の直後に本物のデータがある場合、飛び越して取りこぼさないこと
        hour_ms = 3600_000
        day_ms = 24 * hour_ms
        base = 1_700_000_000_000
        real_candle_ts = base + 5 * day_ms + 3 * hour_ms  # 5日と3時間後

        class FakeExchangeWithDataAfterGap:
            def parse_timeframe(self, tf):
                return 3600

            def fetch_ohlcv(self, symbol, timeframe, since=None, limit=1000):
                if since <= real_candle_ts < since + day_ms:
                    return [[real_candle_ts, 1, 1, 1, 1, 1]]
                return []

        result = fetch_all(
            FakeExchangeWithDataAfterGap(), "BTC/JPY", "1h", base, base + 10 * day_ms
        )
        self.assertEqual([c[0] for c in result], [real_candle_ts])


if __name__ == "__main__":
    unittest.main(verbosity=2)
