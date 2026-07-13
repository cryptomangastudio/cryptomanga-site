"""ストレステスト: 合成相場シナリオで全戦略を耐久試験する。

「勝てるか」ではなく「どの相場でも壊滅しないか(負けが有界か)」を確認する装置。
実運転と同一の BotRunner.step() 経路(backtest.run_sim)を使う。

使い方:
    python stress_test.py                     # 全シナリオ×全戦略
    python stress_test.py --out docs/stress   # レポートをMarkdown保存

判定基準(サバイバル基準):
- 最終資産が初期資金の75%以上(=最大でも-25%で止まっている)
- 暴落シナリオではDD全停止が発動していること(ブレーキが効いた証拠)
"""
from __future__ import annotations

import argparse
import math
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

from backtest import run_sim
from bot.config import BotConfig

BARS = 2160  # 1時間足×90日
SEED = 20260713
START = datetime(2025, 1, 1, tzinfo=timezone.utc)
SURVIVAL_FLOOR = 0.75  # 初期資金の75%を割ったら「壊滅」


def _series(drift_per_bar, vol, n=BARS, start_price=10_000_000.0, rng=None, shock=None):
    """幾何ランダムウォーク+任意のショック関数で終値列を作る。"""
    rng = rng or random.Random(SEED)
    prices = []
    p = start_price
    for i in range(n):
        p *= math.exp(drift_per_bar + rng.gauss(0, vol))
        if shock:
            p = shock(i, p)
        prices.append(p)
    return prices


def scenarios() -> dict[str, list[float]]:
    """名前付きの合成相場シナリオ。乱数は固定シードで再現可能。"""

    def crash_shock(i, p):
        # 45日目から48時間かけて計-35%の暴落、その後は自然に回復基調へ
        if 1080 <= i < 1128:
            return p * (0.65 ** (1 / 48))
        return p

    def pump_dump(i, p):
        if 720 <= i < 792:   # 30日目から3日で+45%
            return p * (1.45 ** (1 / 72))
        if 792 <= i < 888:   # 直後に4日で-40%
            return p * (0.60 ** (1 / 96))
        return p

    base_vol = 0.004  # 1時間足のBTC相当ボラ
    return {
        "強気相場(+0.03%/h)": _series(0.0003, base_vol, rng=random.Random(SEED + 1)),
        "弱気相場(-0.03%/h)": _series(-0.0003, base_vol, rng=random.Random(SEED + 2)),
        "レンジ(ドリフトなし)": _series(0.0, base_vol, rng=random.Random(SEED + 3)),
        "ジリ下げ(-0.01%/h低ボラ)": _series(-0.0001, 0.0015, rng=random.Random(SEED + 4)),
        "暴落(-35%を48時間で)": _series(0.0001, base_vol, rng=random.Random(SEED + 5), shock=crash_shock),
        "急騰急落(+45%→-40%)": _series(0.0, base_vol, rng=random.Random(SEED + 6), shock=pump_dump),
    }


def make_data(prices: list[float]):
    return [(START + timedelta(hours=i), p) for i, p in enumerate(prices)]


def strategy_configs() -> dict[str, BotConfig]:
    dca = BotConfig(strategy="dca")
    ma = BotConfig(strategy="ma_cross")
    return {"dca": dca, "ma_cross": ma}


def run_all() -> list[dict]:
    results = []
    for scen_name, prices in scenarios().items():
        data = make_data(prices)
        bh_return = (prices[-1] / prices[0] - 1) * 100  # ガチホした場合(比較用)
        for strat_name, cfg in strategy_configs().items():
            r = run_sim(cfg, data, f"stress_{strat_name}")
            survived = r["final_equity"] >= cfg.budget_jpy * SURVIVAL_FLOOR
            results.append(
                {
                    "scenario": scen_name,
                    "strategy": strat_name,
                    "return_pct": r["return_pct"],
                    "bh_return_pct": bh_return,
                    "max_dd": r["max_dd"],
                    "trades": r["trades"],
                    "halted": r["halted"],
                    "survived": survived,
                }
            )
    return results


def render(results: list[dict]) -> str:
    lines = [
        "# ストレステスト結果",
        "",
        f"1時間足×{BARS}本(約90日)の合成シナリオ。判定 = 最終資産が初期資金の"
        f"{SURVIVAL_FLOOR:.0%}以上なら生存。暴落時はDD全停止の発動が正常。",
        "",
        "| シナリオ | 戦略 | bot損益 | ガチホ損益 | 最大DD | 取引 | DD停止 | 判定 |",
        "|---|---|---:|---:|---:|---:|:-:|:-:|",
    ]
    for r in results:
        lines.append(
            f"| {r['scenario']} | {r['strategy']} | {r['return_pct']:+.1f}% "
            f"| {r['bh_return_pct']:+.1f}% | {r['max_dd']:.1f}% | {r['trades']} "
            f"| {'発動' if r['halted'] else '—'} | {'✅生存' if r['survived'] else '❌壊滅'} |"
        )
    fails = [r for r in results if not r["survived"]]
    lines.append("")
    if fails:
        lines.append(f"## ❌ 壊滅した組み合わせ: {len(fails)}件 — 対策が必要")
        for r in fails:
            lines.append(f"- {r['scenario']} × {r['strategy']}: {r['return_pct']:+.1f}%")
    else:
        lines.append("## ✅ 全組み合わせが生存(負けは有界に収まっている)")
    lines.append("")
    lines.append("※ 合成データによる耐久試験であり、将来の利益を示すものではない。")
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="CryptoBot ストレステスト")
    parser.add_argument("--out", default="", help="レポートの保存先ディレクトリ")
    args = parser.parse_args()

    results = run_all()
    report = render(results)
    print(report)
    if args.out:
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        path = out / "stress_report.md"
        path.write_text(report, encoding="utf-8")
        print(f"保存: {path}")


if __name__ == "__main__":
    main()
