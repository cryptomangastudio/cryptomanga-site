"""CryptoBot エントリポイント。

使い方:
    python main.py --config config.yaml           # 常駐実行
    python main.py --config config.yaml --once    # 1サイクルだけ(動作確認)
    python main.py --config config.yaml --check   # 実弾前の適合性チェック
"""
from __future__ import annotations

import argparse
import json
import logging
from datetime import datetime
from pathlib import Path

from bot.config import load_config
from bot.exchange import SpotOnlyExchange
from bot.lock import acquire_singleton_lock
from bot.portfolio import PortfolioRunner, sub_config


def run_check(cfg, exchange: SpotOnlyExchange) -> None:
    """設定と取引所仕様の整合性を確認する(発注はしない)。"""
    print(f"=== 適合性チェック: {cfg.exchange} / モード: {cfg.mode} / 総予算: {cfg.budget_jpy:,}円 ===")
    any_problem = False
    for sym in cfg.symbols:
        sub = sub_config(cfg, sym)
        price = exchange.fetch_price(sym)
        print(f"\n[{sym}] 現在価格 {price:,.0f}円 / この銘柄の予算 {sub.budget_jpy:,}円")
        min_amount = exchange.min_order_amount(sym)
        if min_amount is None:
            print("  最低注文数量: 取引所情報から取得できず(要手動確認)")
            continue
        min_jpy = min_amount * price
        print(f"  最低注文数量: {min_amount}(約 {min_jpy:,.0f}円)")

        # 手数料率は銘柄ごとに異なりうる(altcoinはBTCと違うことがある)ため、
        # config.yamlの固定値と取引所の実値を突き合わせる。
        # ただしccxtが実際の手数料APIから値を取るのはbitbank(GET /spot/pairs)のみ
        # 確認済みで、bitFlyer/coincheck/zaif等はccxt側の静的なプレースホルダーが
        # 返ることがあり、それと比較すると誤った警告になる。bitbank以外では省略する
        if cfg.exchange == "bitbank":
            maker, taker = exchange.fee_rates(sym)
            if maker is not None and abs(maker - cfg.execution.maker_fee_rate) > 1e-6:
                print(
                    f"  ⚠️  メイカー手数料が設定と不一致: 設定{cfg.execution.maker_fee_rate:.4%} "
                    f"/ 取引所{maker:.4%}(config.yamlのexecution.maker_fee_rateを修正してください)"
                )
            if taker is not None and abs(taker - cfg.execution.taker_fee_rate) > 1e-6:
                print(
                    f"  ⚠️  テイカー手数料が設定と不一致: 設定{cfg.execution.taker_fee_rate:.4%} "
                    f"/ 取引所{taker:.4%}(config.yamlのexecution.taker_fee_rateを修正してください)"
                )

        problems = []
        if cfg.strategy == "dca" and sub.dca.buy_amount_jpy < min_jpy:
            problems.append(f"DCAの積立額 {sub.dca.buy_amount_jpy:,}円 が最低注文額を下回る")
        if sub.risk.max_order_jpy < min_jpy:
            problems.append(f"1回の注文上限 {sub.risk.max_order_jpy:,}円 が最低注文額を下回る")
        for p in problems:
            print(f"  ⚠️  {p}")
        any_problem = any_problem or bool(problems)
    print()
    if any_problem:
        print("⚠️  問題のある銘柄があります。積立額を増やす・銘柄を絞る・最低数量の小さい取引所(例: bitbank)を検討してください")
    else:
        print("✅ すべての銘柄が取引所の最低注文数量と整合しています")


def warn_if_not_promoted() -> None:
    """`promote.py`の判定記録が無い/古い/FAILならlive起動前に警告する(起動は止めない)。

    promote.pyは印字するだけなので、これが無いと誰も強制されない。
    ここでも止めない(既定値の変更や緊急の手動判断を妨げないため)が、
    見落としを防ぐため必ず目に入る場所に警告を出す。
    """
    path = Path("data/promotion_status.json")
    if not path.exists():
        print("⚠️  実弾昇格チェックが未実施です。先に `python promote.py` の実行を推奨します。")
        return
    try:
        status = json.loads(path.read_text(encoding="utf-8"))
        checked_at = datetime.fromisoformat(status["checked_at"])
        age_days = (datetime.now() - checked_at).days
    except (json.JSONDecodeError, KeyError, ValueError, OSError):
        print("⚠️  実弾昇格チェックの記録が壊れています。`python promote.py` を再実行してください。")
        return
    if not status.get("ok"):
        print("⚠️  直近の実弾昇格チェックは❌でした(`python promote.py` で確認してください)。")
    elif age_days > 14:
        print(f"⚠️  実弾昇格チェックが{age_days}日前と古いです。`python promote.py` の再実行を推奨します。")


def main() -> None:
    parser = argparse.ArgumentParser(description="現物のみ・少額運用CryptoBot")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--once", action="store_true", help="1サイクルだけ実行して終了")
    parser.add_argument("--check", action="store_true", help="実弾前の適合性チェック")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    cfg = load_config(args.config)
    exchange = SpotOnlyExchange(cfg)

    if args.check:
        run_check(cfg, exchange)
        return

    if cfg.mode == "live":
        print("⚠️  LIVEモード: 実際のお金で発注します。停止は Ctrl+C。")
        warn_if_not_promoted()
    _lock = acquire_singleton_lock()  # 二重起動防止。プロセス終了まで保持
    runner = PortfolioRunner(cfg, exchange)

    if args.once:
        for sym, result in runner.step_all(datetime.now()).items():
            print(f"{sym} | {result}")
    else:
        runner.run()


if __name__ == "__main__":
    main()
