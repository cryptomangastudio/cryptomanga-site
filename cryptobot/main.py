"""CryptoBot エントリポイント。

使い方:
    python main.py --config config.yaml           # 常駐実行
    python main.py --config config.yaml --once    # 1サイクルだけ(動作確認)
    python main.py --config config.yaml --check   # 実弾前の適合性チェック
"""
from __future__ import annotations

import argparse
import logging
from datetime import datetime

from bot.config import load_config
from bot.exchange import SpotOnlyExchange
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
    runner = PortfolioRunner(cfg, exchange)

    if args.once:
        for sym, result in runner.step_all(datetime.now()).items():
            print(f"{sym} | {result}")
    else:
        runner.run()


if __name__ == "__main__":
    main()
