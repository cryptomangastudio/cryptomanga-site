"""過去のOHLCVデータをbitbank(ccxt)からダウンロードし、backtest.py用のCSVに保存する。

bitbankのcandlestick APIはURLに日付を含む「バケット」方式で、1分足〜1時間足は
1リクエスト=1日分(YYYYMMDD)、4時間足以上は1リクエスト=1年分(YYYY)しか
返さない(公式ドキュメント rest-api_JP.md / public-api_JP.md で確認)。
1時間足を2年分取りたい場合は約730リクエストになるため、レート制限
(bitbank公式: 読み取り系10回/秒)に従いccxtの自動スロットリングに任せる。

使い方:
    python fetch_history.py --symbol BTC/JPY --timeframe 1h --years 2
    python fetch_history.py --symbol ETH/JPY --timeframe 1d --years 3 \
        --out data/ETH_JPY_1d.csv
"""
from __future__ import annotations

import argparse
import csv
import time
from pathlib import Path

import ccxt


def fetch_all(exchange, symbol: str, timeframe: str, since_ms: int, until_ms: int) -> list[list]:
    """sinceを進めながら重複排除して全期間のOHLCVを集める。"""
    out: dict[int, list] = {}
    cursor = since_ms
    duration_ms = exchange.parse_timeframe(timeframe) * 1000
    stall_guard = 0
    while cursor < until_ms:
        batch = exchange.fetch_ohlcv(symbol, timeframe, since=cursor, limit=1000)
        if not batch:
            cursor += duration_ms * 300  # データ空白期間を飛ばす(bitbankの日次バケット対策)
            stall_guard += 1
            if stall_guard > 20:
                break
            continue
        stall_guard = 0
        new_max = cursor
        for c in batch:
            if c[0] is None:
                continue
            out[int(c[0])] = c
            new_max = max(new_max, int(c[0]))
        if new_max <= cursor:  # 前進しない場合は無限ループ防止で打ち切り
            break
        cursor = new_max + duration_ms
        # ccxtのenableRateLimit=Trueが公式レート制限(読み取り10回/秒)に
        # 合わせて自動でスロットリングするため、ここでの追加sleepは不要
    return [out[k] for k in sorted(out) if k <= until_ms]


def main() -> None:
    parser = argparse.ArgumentParser(description="bitbankから過去OHLCVを取得してCSV保存")
    parser.add_argument("--symbol", default="BTC/JPY")
    parser.add_argument("--timeframe", default="1h", help="1h | 1d など")
    parser.add_argument("--years", type=float, default=2.0)
    parser.add_argument("--out", default="", help="出力CSVパス(省略時は自動命名)")
    parser.add_argument("--exchange", default="bitbank")
    args = parser.parse_args()

    if not hasattr(ccxt, args.exchange):
        raise SystemExit(f"未知の取引所ID: {args.exchange}")
    exchange = getattr(ccxt, args.exchange)({"enableRateLimit": True})
    # bitbank公式の読み取り制限(10回/秒=100ms間隔)ちょうどだと余裕がないため、
    # 数百回連続で叩くこのツールでは少し安全マージンを取る(実運転には影響しない)
    exchange.rateLimit = max(exchange.rateLimit, 150)
    exchange.load_markets()
    if not exchange.has.get("fetchOHLCV"):
        raise SystemExit(
            f"{args.exchange} はOHLCV取得に対応していません(例: bitFlyerは非対応。bitbankを使ってください)"
        )

    now_ms = exchange.milliseconds()
    since_ms = now_ms - int(args.years * 365 * 24 * 3600 * 1000)

    print(f"取得中: {args.symbol} {args.timeframe} ({args.years}年分)…")
    rows = fetch_all(exchange, args.symbol, args.timeframe, since_ms, now_ms)
    if not rows:
        raise SystemExit("データが取得できませんでした(銘柄・足種を確認してください)")

    slug = args.symbol.replace("/", "_")
    out_path = Path(args.out) if args.out else Path("data") / f"{slug}_{args.timeframe}.csv"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        for ts, o, h, l, c, v in rows:
            w.writerow([ts, o, h, l, c, v])

    span_days = (rows[-1][0] - rows[0][0]) / 1000 / 86400
    print(f"{len(rows)}本を取得(実質期間 約{span_days:.0f}日)")
    print(f"保存: {out_path}")
    print(f"\nバックテスト: python backtest.py --data {out_path} --walk-forward 5 --trials <試行数>")


if __name__ == "__main__":
    main()
