"""取引所ラッパー。現物(スポット)以外の取引をコードレベルで禁止する。

liveモードではccxt経由で実注文を出すが、以下を強制する:
- スポット市場のみ(margin/future/swap/optionの市場は拒否)
- 注文パラメータにレバレッジ・信用系の指定があれば拒否
- APIキーは環境変数からのみ読む(設定ファイルに書かせない)
"""
from __future__ import annotations

import os
from typing import Any

from .config import BotConfig

FORBIDDEN_PARAM_KEYS = frozenset(
    {"leverage", "margin", "marginMode", "reduceOnly", "positionSide"}
)


class SpotOnlyViolation(RuntimeError):
    """現物以外の取引を試みたときに送出。botはこれを握りつぶしてはならない。"""


class SpotOnlyExchange:
    def __init__(self, cfg: BotConfig):
        import ccxt  # liveや価格取得時のみ必要なので遅延import

        if not hasattr(ccxt, cfg.exchange):
            raise ValueError(f"未知の取引所ID: {cfg.exchange}")
        params: dict[str, Any] = {"enableRateLimit": True}
        if cfg.mode == "live":
            if os.environ.get("CRYPTOBOT_LIVE") != "YES":
                raise SpotOnlyViolation("live実行には CRYPTOBOT_LIVE=YES が必要")
            params["apiKey"] = os.environ["CRYPTOBOT_API_KEY"]
            params["secret"] = os.environ["CRYPTOBOT_API_SECRET"]
        self.cfg = cfg
        self.client = getattr(ccxt, cfg.exchange)(params)
        self._markets_loaded = False

    def _assert_spot(self, symbol: str, params: dict | None) -> None:
        if params:
            bad = FORBIDDEN_PARAM_KEYS.intersection(params)
            if bad:
                raise SpotOnlyViolation(f"現物以外のパラメータは禁止: {sorted(bad)}")
        if not self._markets_loaded:
            self.client.load_markets()
            self._markets_loaded = True
        market = self.client.market(symbol)
        if not market.get("spot", False):
            raise SpotOnlyViolation(f"{symbol} はスポット市場ではありません")

    def fetch_price(self, symbol: str) -> float:
        return float(self.client.fetch_ticker(symbol)["last"])

    def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int = 100) -> list[list[float]]:
        return self.client.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)

    def market_buy(self, symbol: str, amount: float, params: dict | None = None) -> dict:
        self._assert_spot(symbol, params)
        return self.client.create_order(symbol, "market", "buy", amount)

    def market_sell(self, symbol: str, amount: float, params: dict | None = None) -> dict:
        self._assert_spot(symbol, params)
        return self.client.create_order(symbol, "market", "sell", amount)
