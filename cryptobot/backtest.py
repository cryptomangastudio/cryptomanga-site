"""簡易バックテスター。

OHLCVのCSV(timestamp,open,high,low,close,volume)に対して戦略を回し、
手数料込みの成績を表示する。実弾投入前に必ずここで検証すること。

実運転と同一の BotRunner.step() を1バーずつ駆動するため、最低買付額・
リスク上限・クールダウン・日次損失停止・ドローダウン停止がそのまま効き、
実運転との挙動乖離が構造的に起きない。

使い方:
    python backtest.py --config config.yaml --data data/BTC_JPY_1h.csv
"""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
from pathlib import Path

from bot.config import load_config
from bot.runner import BotRunner


def load_ohlcv(path: str) -> list[tuple[datetime, float]]:
    """(日時, 終値) のリストを返す。timestampは秒/ミリ秒のUNIX時刻またはISO形式。"""
    rows = []
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        for row in reader:
            if not row or not row[0].strip():
                continue
            ts_raw = row[0].strip()
            try:
                ts_num = float(ts_raw)
                if ts_num > 1e12:  # ミリ秒
                    ts_num /= 1000
                ts = datetime.fromtimestamp(ts_num, tz=timezone.utc)
            except ValueError:
                try:
                    ts = datetime.fromisoformat(ts_raw)
                except ValueError:
                    continue  # ヘッダー行など
            rows.append((ts, float(row[4])))
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description="CryptoBot バックテスター")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--data", required=True, help="OHLCVのCSVファイル")
    args = parser.parse_args()

    cfg = load_config(args.config)
    data = load_ohlcv(args.data)
    if len(data) < cfg.ma_cross.slow + 2:
        raise SystemExit(f"データ不足: {len(data)}行(最低{cfg.ma_cross.slow + 2}行)")

    # 実運転と同じBotRunnerを、バックテスト専用の隔離された状態で駆動する
    cfg.mode = "paper"
    cfg.notify.format = "none"          # バックテストで通知を飛ばさない
    cfg.halt_file = ""                  # 停止を永続化しない(実運転のHALTEDに触らない)
    cfg.paper_state_path = ""           # ペーパー残高を永続化しない
    cfg.journal_path = "data/backtest_trades.csv"
    Path(cfg.journal_path).unlink(missing_ok=True)
    runner = BotRunner(cfg)

    window = cfg.ma_cross.slow + 2  # 戦略が必要とする本数だけ渡す(全履歴を渡すとO(n²))
    peak = equity = float(cfg.budget_jpy)
    max_dd = 0.0
    trades = skipped = 0

    closes: list[float] = []
    for ts, close in data:
        closes.append(close)
        result = runner.step(ts, close, closes[-window:])
        if result.startswith(("BUY ", "SELL ")):
            trades += 1
        elif "却下" in result or "スキップ" in result:
            skipped += 1
        # ドローダウンは毎バー評価する(取引のないバーの暴落も最大DDに反映)
        equity = runner.paper.equity(close)
        peak = max(peak, equity)
        max_dd = max(max_dd, (1 - equity / peak) * 100)

    final_equity = runner.paper.equity(data[-1][1])
    journal = runner.journal
    print("=== バックテスト結果 ===")
    print(f"期間        : {data[0][0]:%Y-%m-%d} 〜 {data[-1][0]:%Y-%m-%d}({len(data)}本)")
    print(f"戦略        : {cfg.strategy}")
    print(f"初期資金    : {cfg.budget_jpy:,}円")
    print(f"最終資産    : {final_equity:,.0f}円({(final_equity / cfg.budget_jpy - 1) * 100:+.2f}%)")
    print(f"実現損益    : {journal.total_realized_pnl:+,.0f}円(課税対象の目安)")
    print(f"取引回数    : {trades}回(却下・スキップ {skipped}回)")
    print(f"最大DD      : {max_dd:.2f}%")
    if runner.risk.halted:
        print(f"⚠️  途中でDD停止が発動: {runner.risk.halt_reason}")
    print(f"取引明細    : {cfg.journal_path}")


if __name__ == "__main__":
    main()
