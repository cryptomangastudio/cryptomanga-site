"""簡易バックテスター。

OHLCVのCSV(timestamp,open,high,low,close,volume)に対して戦略を回し、
手数料込みの成績を表示する。実弾投入前に必ずここで検証すること。

使い方:
    python backtest.py --config config.yaml --data data/BTC_JPY_1h.csv
"""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
from pathlib import Path

from bot.config import load_config
from bot.journal import Fill, TradeJournal
from bot.paper import PaperBroker
from bot.strategy import Action, MarketSnapshot, build_strategy


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

    strategy = build_strategy(cfg)
    broker = PaperBroker(cfg.budget_jpy, cfg.fee_rate)
    journal_path = Path("data") / "backtest_trades.csv"
    journal_path.unlink(missing_ok=True)
    journal = TradeJournal(journal_path)

    peak = equity = float(cfg.budget_jpy)
    max_dd = 0.0
    trades = 0

    closes: list[float] = []
    for ts, close in data:
        closes.append(close)
        market = MarketSnapshot(
            price=close,
            closes=closes,
            position_amount=journal.position_amount,
            position_cost_jpy=journal.position_cost_jpy,
        )
        signal = strategy.decide(market)
        try:
            if signal.action == Action.BUY and signal.jpy_amount <= broker.jpy:
                amount, fee = broker.market_buy(close, signal.jpy_amount)
                journal.record(Fill(ts, cfg.exchange, cfg.symbol, "buy", amount, close, fee, signal.reason))
                trades += 1
            elif signal.action == Action.SELL and journal.position_amount > 0:
                amount = journal.position_amount
                _, fee = broker.market_sell(close, amount)
                journal.record(Fill(ts, cfg.exchange, cfg.symbol, "sell", amount, close, fee, signal.reason))
                trades += 1
        except ValueError:
            pass  # 残高不足はスキップ(記録上は買えなかっただけ)
        equity = broker.equity(close)
        peak = max(peak, equity)
        max_dd = max(max_dd, (1 - equity / peak) * 100)

    final_price = data[-1][1]
    print("=== バックテスト結果 ===")
    print(f"期間        : {data[0][0]:%Y-%m-%d} 〜 {data[-1][0]:%Y-%m-%d}({len(data)}本)")
    print(f"戦略        : {cfg.strategy}")
    print(f"初期資金    : {cfg.budget_jpy:,}円")
    print(f"最終資産    : {broker.equity(final_price):,.0f}円 "
          f"({(broker.equity(final_price) / cfg.budget_jpy - 1) * 100:+.2f}%)")
    print(f"実現損益    : {journal.total_realized_pnl:+,.0f}円(課税対象の目安)")
    print(f"取引回数    : {trades}回")
    print(f"最大DD      : {max_dd:.2f}%")
    print(f"取引明細    : {journal_path}")


if __name__ == "__main__":
    main()
