"""ペーパー→実弾の昇格チェッカー。

「好成績だから」で実弾に進むのを防ぐための、機械的なチェックリスト。
プロップファームの評価基準(最低取引日数・最大DD制限・段階的な資金投入)を
10万円個人運用向けに簡略化したもの。全項目を満たさない限り「進めない」判定。

昇格基準の既定値は、プロップファーム(FTMO/Topstep)の評価基準とアルゴ
トレード実務(Walk-Forward Analysis、統計的有意性の下限)を参考に設定:
- ペーパー運用が90日(3ヶ月)以上 — FX EA運用実務での「実弾検討可能」最短ライン
  (理想は180日=6ヶ月、可能ならさらにもう1周期繰り返すのが望ましい)
- 売却が30回以上 — 中心極限定理に基づく統計的有意性の絶対下限
  (理想は200回。バックテストの母数としてよく引用される目安)
- バックテストの過学習ゲート(--walk-forward, DSR>=0.95)は自動確認できないため
  手動実行を促す

使い方:
    python promote.py --config config.yaml
"""
from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime
from pathlib import Path

from bot.config import load_config
from bot.journal import COL_REALIZED, COL_SIDE, COL_TS, HEADER
from bot.portfolio import sub_config

PROMOTION_STATUS_PATH = "data/promotion_status.json"

DEFAULT_MIN_DAYS = 90     # 3ヶ月。FX EA運用実務での「実弾検討可能」最短ライン
IDEAL_MIN_DAYS = 180      # 6ヶ月。理想はこちらで、さらに2周期繰り返すのが望ましい
DEFAULT_MIN_TRADES = 30   # 中心極限定理に基づく統計的有意性の絶対下限
IDEAL_MIN_TRADES = 200    # バックテストの母数としてよく引用される目安
FIRST_LIVE_BUDGET_RATIO = 0.2   # 昇格直後は総予算の20%程度から(段階投入の第一歩)
SCALING_INTERVAL_MONTHS = 2     # 増額を検討する間隔の目安(プロップファーム基準を圧縮)
SCALING_INCREMENT_RATIO = 0.25  # 条件クリアごとの増額幅の目安


def check_symbol(journal_path: Path, min_days: int, min_trades: int) -> dict:
    if not journal_path.exists():
        return {"ok": False, "reason": "取引記録がまだありません(ペーパー運用を開始してください)"}
    with journal_path.open(newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        header = next(reader, None)
        if header != HEADER:
            return {"ok": False, "reason": f"{journal_path} のヘッダーが想定と異なります"}
        rows = [r for r in reader if r]
    if not rows:
        return {"ok": False, "reason": "取引記録が0件です"}

    first_ts = datetime.strptime(rows[0][COL_TS], "%Y-%m-%d %H:%M:%S")
    days_running = (datetime.now() - first_ts).days
    sells = sum(1 for r in rows if r[COL_SIDE] == "売")
    realized = [float(r[COL_REALIZED]) for r in rows if r[COL_SIDE] == "売"]
    win_rate = (sum(1 for r in realized if r > 0) / len(realized) * 100) if realized else None

    checks = {
        f"ペーパー運用日数 >= {min_days}日(理想は{IDEAL_MIN_DAYS}日)":
            (days_running >= min_days, f"{days_running}日"),
        f"売却回数 >= {min_trades}回(理想は{IDEAL_MIN_TRADES}回)":
            (sells >= min_trades, f"{sells}回"),
    }
    return {
        "ok": all(c[0] for c in checks.values()),
        "days_running": days_running,
        "sells": sells,
        "win_rate": win_rate,
        "checks": checks,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="ペーパー→実弾の昇格チェッカー")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--min-days", type=int, default=DEFAULT_MIN_DAYS)
    parser.add_argument("--min-trades", type=int, default=DEFAULT_MIN_TRADES)
    args = parser.parse_args()

    cfg = load_config(args.config)
    if cfg.mode != "paper":
        raise SystemExit("このチェックはペーパー運用中の設定ファイルに対して実行してください")

    print("=== 実弾昇格チェック ===")
    print(f"対象: {', '.join(cfg.symbols)}\n")

    all_ok = True
    for sym in cfg.symbols:
        sub = sub_config(cfg, sym)
        result = check_symbol(Path(sub.journal_path), args.min_days, args.min_trades)
        print(f"[{sym}]")
        if "checks" not in result:
            print(f"  ❌ {result['reason']}")
            all_ok = False
            continue
        for label, (ok, detail) in result["checks"].items():
            print(f"  {'✅' if ok else '❌'} {label}(現在: {detail})")
        if result["win_rate"] is not None:
            print(f"  参考: 勝率 {result['win_rate']:.0f}%")
        all_ok = all_ok and result["ok"]
        print()

    print("=== バックテスト検証ゲート ===")
    print("  ⚠️  自動確認できません。以下を必ず手動で実施してください:")
    print(f"     python fetch_history.py --symbol <銘柄> --timeframe 1h --years 2")
    print(f"     python backtest.py --data data/<銘柄>_1h.csv --walk-forward 5 --trials <試行総数>")
    print("     → 「✅ PASS」が出ていること(FAILなら実弾投入しない)")
    print()

    first_live = int(cfg.budget_jpy * FIRST_LIVE_BUDGET_RATIO)
    print("=== 総合判定 ===")
    if all_ok:
        print("✅ 機械的なチェックはクリアしています。")
        print("   ただし上記バックテストゲートのPASSを必ず目視確認してから進めてください。")
        print(f"\n   段階投入プラン(プロップファームのスケーリング方式を参考に簡略化):")
        print(f"   1. 予算全額(budget_jpy: {cfg.budget_jpy:,}円)ではなく、まず"
              f"{first_live:,}円({FIRST_LIVE_BUDGET_RATIO:.0%})から開始")
        print(f"   2. 実効コスト台帳(data/execution_*.csv)でバックテスト想定との乖離を測定")
        print(f"   3. 約{SCALING_INTERVAL_MONTHS}ヶ月ごとに、直近の半分以上の期間がプラス収支かつ"
              f"最大DDが設定上限内なら、投入額を+{SCALING_INCREMENT_RATIO:.0%}ずつ増やす")
        print(f"   4. 悪化したら増額を止め、原因(戦略かコストか)を切り分ける")
    else:
        print("❌ まだ実弾に進める段階ではありません。上記の❌項目を満たすまで")
        print("   ペーパートレードを継続してください。")

    # 判定結果をファイルに残す。main.pyがlive起動時にこれを読み、
    # 実行を止めはしないが「チェックを通っていない/古い」ことを必ず警告する
    # (このスクリプト自体は印字するだけなので、何もしないと誰にも強制されない)
    status_path = Path(PROMOTION_STATUS_PATH)
    status_path.parent.mkdir(parents=True, exist_ok=True)
    status_path.write_text(
        json.dumps(
            {"ok": all_ok, "checked_at": datetime.now().isoformat(), "symbols": cfg.symbols},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
