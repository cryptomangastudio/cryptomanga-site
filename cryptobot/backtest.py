"""簡易バックテスター+過学習検出ゲート(リサーチ#6)。

実運転と同一の BotRunner.step() を1バーずつ駆動するため、コストゲート・
リスク上限・クールダウン・各種ブレーカーがそのまま効き、実運転との挙動乖離が
構造的に起きない。

使い方:
    python backtest.py --config config.yaml --data data/BTC_JPY_1h.csv
    python backtest.py --config config.yaml --data data/BTC_JPY_1h.csv \
        --walk-forward 5 --trials 20
        # --trials: この戦略・パラメータに至るまでに試した設定の総数(正直に申告する。
        #   多く試したほど「まぐれ好成績」の可能性が上がり、DSRが厳しくなる)

昇格ゲート(推奨): ウォークフォワード各区間の6割以上がプラス、かつ DSR >= 0.95。
通らない戦略は実弾に投入しない。
"""
from __future__ import annotations

import argparse
import csv
import dataclasses
from datetime import datetime, timezone
from pathlib import Path

from bot.config import load_config
from bot.runner import BotRunner, closes_needed
from bot.validate import deflated_sharpe, sharpe_ratio, walk_forward_segments


class BacktestDataFeed:
    """バックテスト用の擬似取引所。

    レジームフィルター(200日MA)が実運転と同じ経路で働くよう、入力OHLCVを
    日足にリサンプルして fetch_daily_closes を提供する。now_ts より未来の
    日足は返さない(ルックアヘッド防止)。
    """

    def __init__(self, data: list[tuple[datetime, float]]):
        self.daily: list[tuple[str, float]] = []  # (日付, その日の最終終値)
        for ts, close in data:
            day = ts.strftime("%Y-%m-%d")
            if self.daily and self.daily[-1][0] == day:
                self.daily[-1] = (day, close)
            else:
                self.daily.append((day, close))
        self.now_day = ""

    def set_now(self, ts: datetime) -> None:
        self.now_day = ts.strftime("%Y-%m-%d")

    def fetch_daily_closes(self, symbol: str, days: int) -> list[float]:
        closes = [c for day, c in self.daily if day < self.now_day]  # 当日は未確定なので除外
        return closes[-days:]

    def min_order_amount(self, symbol: str):
        return None


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


def sim_config(cfg, journal_name: str):
    """バックテスト専用に隔離した設定(実運転のdata/やHALTEDに触らない)。"""
    return dataclasses.replace(
        cfg,
        mode="paper",
        symbols=[cfg.symbol],
        notify=dataclasses.replace(cfg.notify, format="none"),
        halt_file="",
        paper_state_path="",
        journal_path=f"data/{journal_name}.csv",
        shortfall_path=f"data/{journal_name}_exec.csv",
        price_sanity_pct=0.0,  # 歴史データのギャップで止まらないように
    )


def run_sim(cfg, data: list[tuple[datetime, float]], journal_name: str) -> dict:
    """1系列ぶんのシミュレーション。実運転と同じBotRunner.stepを駆動する。"""
    scfg = sim_config(cfg, journal_name)
    for p in (scfg.journal_path, scfg.shortfall_path):
        Path(p).unlink(missing_ok=True)
    feed = BacktestDataFeed(data)  # レジームフィルターを実運転と同じ経路で効かせる
    runner = BotRunner(scfg, exchange=feed)

    window = closes_needed(scfg)  # 実運転(fetch_closes)と同じ本数を渡す
    peak = float(scfg.budget_jpy)
    max_dd = 0.0
    trades = skipped = 0
    equity_curve = [float(scfg.budget_jpy)]

    closes: list[float] = []
    for ts, close in data:
        closes.append(close)
        feed.set_now(ts)
        result = runner.step(ts, close, closes[-window:])
        if result.startswith(("BUY ", "SELL ")):
            trades += 1
        elif "却下" in result or "スキップ" in result or "見送り" in result:
            skipped += 1
        equity = runner.paper.equity(close)
        equity_curve.append(equity)
        peak = max(peak, equity)
        max_dd = max(max_dd, (1 - equity / peak) * 100)

    returns = [
        equity_curve[i] / equity_curve[i - 1] - 1
        for i in range(1, len(equity_curve))
        if equity_curve[i - 1] > 0
    ]
    return {
        "final_equity": equity_curve[-1],
        "return_pct": (equity_curve[-1] / scfg.budget_jpy - 1) * 100,
        "returns": returns,
        "trades": trades,
        "skipped": skipped,
        "max_dd": max_dd,
        "realized": runner.journal.total_realized_pnl,
        "halted": runner.risk.halted,
        "halt_reason": runner.risk.halt_reason,
        "journal_path": scfg.journal_path,
    }


def periods_per_year(data: list[tuple[datetime, float]]) -> int:
    if len(data) < 2:
        return 365
    total_s = (data[-1][0] - data[0][0]).total_seconds()
    bar_s = max(1.0, total_s / (len(data) - 1))
    return max(1, int(365 * 24 * 3600 / bar_s))


def main() -> None:
    parser = argparse.ArgumentParser(description="CryptoBot バックテスター+検証ゲート")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--data", required=True, help="OHLCVのCSVファイル")
    parser.add_argument("--walk-forward", type=int, default=0, metavar="N",
                        help="データをN区間に分けて区間ごとの成績を検証")
    parser.add_argument("--trials", type=int, default=1,
                        help="この設定に至るまでに試したパラメータ組の総数(DSR計算用)")
    args = parser.parse_args()

    cfg = load_config(args.config)
    data = load_ohlcv(args.data)
    if len(data) < cfg.ma_cross.slow + 2:
        raise SystemExit(f"データ不足: {len(data)}行(最低{cfg.ma_cross.slow + 2}行)")

    result = run_sim(cfg, data, "backtest_trades")
    ppy = periods_per_year(data)
    sr = sharpe_ratio(result["returns"], ppy)
    dsr = deflated_sharpe(result["returns"], args.trials)

    print("=== バックテスト結果 ===")
    print(f"期間        : {data[0][0]:%Y-%m-%d} 〜 {data[-1][0]:%Y-%m-%d}({len(data)}本)")
    print(f"戦略        : {cfg.strategy} / 執行: {cfg.execution.style}")
    print(f"初期資金    : {cfg.budget_jpy:,}円")
    print(f"最終資産    : {result['final_equity']:,.0f}円({result['return_pct']:+.2f}%)")
    print(f"実現損益    : {result['realized']:+,.0f}円(課税対象の目安)")
    print(f"取引回数    : {result['trades']}回(却下・スキップ {result['skipped']}回)")
    print(f"最大DD      : {result['max_dd']:.2f}%")
    print(f"シャープ(年率): {sr:.2f}")
    print(f"DSR         : {dsr:.3f}(試行{args.trials}回を考慮した「本物である確率」)")
    if cfg.execution.style == "maker":
        print("※ メイカー執行は「常に約定する」仮定のシミュレーション(実運用より楽観的)。"
              "実弾では実効コスト台帳との乖離を必ず確認すること")
    if result["halted"]:
        print(f"⚠️  途中でDD停止が発動: {result['halt_reason']}")
    print(f"取引明細    : {result['journal_path']}")

    gate_ok = dsr >= 0.95
    if args.walk_forward >= 2:
        print(f"\n=== ウォークフォワード({args.walk_forward}区間) ===")
        segments = walk_forward_segments(len(data), args.walk_forward)
        positives = 0
        for i, (s, e) in enumerate(segments, 1):
            seg = run_sim(cfg, data[s:e], f"backtest_wf{i}")
            mark = "+" if seg["return_pct"] >= 0 else "-"
            positives += seg["return_pct"] >= 0
            print(f"区間{i}: {data[s][0]:%Y-%m-%d}〜{data[e-1][0]:%Y-%m-%d} "
                  f"{seg['return_pct']:+.2f}% 取引{seg['trades']}回 [{mark}]")
        ratio = positives / len(segments)
        print(f"プラス区間: {positives}/{len(segments)}({ratio:.0%})")
        gate_ok = gate_ok and ratio >= 0.6

    print("\n=== 昇格ゲート判定 ===")
    if gate_ok and args.walk_forward >= 2:
        print("✅ PASS — 次の段階(最小額の実弾で乖離測定)に進んでよい水準")
    elif args.walk_forward < 2:
        print("⚠️  未判定 — --walk-forward 5 --trials <試行総数> を付けて検証すること")
    else:
        print("❌ FAIL — この戦略・パラメータを実弾に投入してはいけない"
              "(過学習またはエッジ不足の可能性)")


if __name__ == "__main__":
    main()
