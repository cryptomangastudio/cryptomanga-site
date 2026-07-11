"""簡易バックテスター。

OHLCVのCSV(timestamp,open,high,low,close,volume)に対して戦略を回し、
手数料込みの成績を表示する。実弾投入前に必ずここで検証すること。

実運転と同じ RiskManager を通すため、注文上限・保有上限・クールダウン・
日次損失停止・ドローダウン停止がバックテストでもそのまま効く。

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
from bot.risk import RiskManager
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
    risk = RiskManager(cfg.risk, cfg.budget_jpy)  # 停止ファイルは使わない(毎回まっさら)
    journal_path = Path("data") / "backtest_trades.csv"
    journal_path.unlink(missing_ok=True)
    journal = TradeJournal(journal_path)

    max_dd = 0.0
    peak = float(cfg.budget_jpy)
    trades = rejected = 0

    closes: list[float] = []
    for ts, close in data:
        closes.append(close)
        risk.update_equity(broker.equity(close))
        if risk.halted:
            continue  # 実運転と同じく、DD停止後は何もしない
        market = MarketSnapshot(
            price=close,
            closes=closes,
            position_amount=journal.position_amount,
            position_cost_jpy=journal.position_cost_jpy,
        )
        signal = strategy.decide(market)
        if signal.action == Action.HOLD:
            continue

        if signal.action == Action.BUY:
            buy_jpy = min(signal.jpy_amount, broker.jpy)
            if buy_jpy < 1_000:
                continue
            if not risk.check_order("buy", buy_jpy, market.position_cost_jpy, ts).approved:
                rejected += 1
                continue
            amount, fee = broker.market_buy(close, buy_jpy)
            realized = journal.record(
                Fill(ts, cfg.exchange, cfg.symbol, "buy", amount, close, fee, signal.reason)
            )
            risk.record_fill(ts, "buy", realized)
            trades += 1
        else:
            amount = journal.position_amount
            if amount <= 0:
                continue
            if not risk.check_order("sell", amount * close, market.position_cost_jpy, ts).approved:
                rejected += 1
                continue
            _, fee = broker.market_sell(close, amount)
            realized = journal.record(
                Fill(ts, cfg.exchange, cfg.symbol, "sell", amount, close, fee, signal.reason)
            )
            risk.record_fill(ts, "sell", realized)
            trades += 1

        equity = broker.equity(close)
        peak = max(peak, equity)
        max_dd = max(max_dd, (1 - equity / peak) * 100)

    final_price = data[-1][1]
    final_equity = broker.equity(final_price)
    print("=== バックテスト結果 ===")
    print(f"期間        : {data[0][0]:%Y-%m-%d} 〜 {data[-1][0]:%Y-%m-%d}({len(data)}本)")
    print(f"戦略        : {cfg.strategy}")
    print(f"初期資金    : {cfg.budget_jpy:,}円")
    print(f"最終資産    : {final_equity:,.0f}円({(final_equity / cfg.budget_jpy - 1) * 100:+.2f}%)")
    print(f"実現損益    : {journal.total_realized_pnl:+,.0f}円(課税対象の目安)")
    print(f"取引回数    : {trades}回(リスク管理による却下 {rejected}回)")
    print(f"最大DD      : {max_dd:.2f}%")
    if risk.halted:
        print(f"⚠️  途中でDD停止が発動: {risk.halt_reason}")
    print(f"取引明細    : {journal_path}")


if __name__ == "__main__":
    main()
