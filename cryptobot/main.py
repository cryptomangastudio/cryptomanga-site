"""CryptoBot エントリポイント。

使い方:
    python main.py --config config.yaml          # 常駐実行
    python main.py --config config.yaml --once   # 1サイクルだけ(動作確認)
"""
from __future__ import annotations

import argparse
import logging
from datetime import datetime

from bot.config import load_config
from bot.exchange import SpotOnlyExchange
from bot.runner import BotRunner


def main() -> None:
    parser = argparse.ArgumentParser(description="現物のみ・少額運用CryptoBot")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--once", action="store_true", help="1サイクルだけ実行して終了")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    cfg = load_config(args.config)
    if cfg.mode == "live":
        print("⚠️  LIVEモード: 実際のお金で発注します。停止は Ctrl+C。")
    exchange = SpotOnlyExchange(cfg)
    runner = BotRunner(cfg, exchange)

    if args.once:
        price = exchange.fetch_price(cfg.symbol)
        closes = [
            c[4]
            for c in exchange.fetch_ohlcv(
                cfg.symbol, cfg.ma_cross.timeframe, limit=cfg.ma_cross.slow + 5
            )
        ]
        print(runner.step(datetime.now(), price, closes))
    else:
        runner.run()


if __name__ == "__main__":
    main()
