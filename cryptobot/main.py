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
from bot.runner import BotRunner, fetch_closes


def run_check(cfg, exchange: SpotOnlyExchange) -> None:
    """設定と取引所仕様の整合性を確認する(発注はしない)。"""
    print(f"=== 適合性チェック: {cfg.exchange} {cfg.symbol} ===")
    price = exchange.fetch_price(cfg.symbol)
    print(f"現在価格          : {price:,.0f}円")
    min_amount = exchange.min_order_amount(cfg.symbol)
    if min_amount is None:
        print("最低注文数量      : 取引所情報から取得できず(要手動確認)")
    else:
        min_jpy = min_amount * price
        print(f"最低注文数量      : {min_amount}(約 {min_jpy:,.0f}円)")
        problems = []
        if cfg.strategy == "dca" and cfg.dca.buy_amount_jpy < min_jpy:
            problems.append(
                f"DCAの積立額 {cfg.dca.buy_amount_jpy:,}円 が最低注文額を下回っています"
            )
        if cfg.risk.max_order_jpy < min_jpy:
            problems.append(
                f"1回の注文上限 {cfg.risk.max_order_jpy:,}円 が最低注文額を下回っています"
            )
        if problems:
            print("\n⚠️  問題あり:")
            for p in problems:
                print(f"  - {p}")
            print("  → 積立額を増やすか、最低注文数量の小さい取引所(例: bitbank)を検討")
        else:
            print("\n✅ 設定は取引所の最低注文数量と整合しています")
    print(f"モード            : {cfg.mode}")
    print(f"総予算            : {cfg.budget_jpy:,}円")


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
    runner = BotRunner(cfg, exchange)

    if args.once:
        price = exchange.fetch_price(cfg.symbol)
        print(runner.step(datetime.now(), price, fetch_closes(exchange, cfg)))
    else:
        runner.run()


if __name__ == "__main__":
    main()
