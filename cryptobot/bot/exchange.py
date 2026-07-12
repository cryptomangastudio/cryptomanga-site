"""取引所ラッパー。現物(スポット)以外の取引をコードレベルで禁止する。

liveモードではccxt経由で実注文を出すが、以下を強制する:
- スポット市場のみ(margin/future/swap/optionの市場は拒否)
- 注文パラメータにレバレッジ・信用系の指定があれば拒否
- APIキーは環境変数からのみ読む(設定ファイルに書かせない)

対応取引所はccxtに実装がある金融庁登録業者(bitflyer / coincheck / bitbank / zaif)。
GMOコインはccxt未対応のため、使う場合は専用アダプタの追加実装が必要。
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


def fee_to_jpy(fee: dict | None, price: float, base_currency: str) -> float:
    """ccxtのfeeオブジェクトをJPY換算する。手数料がBTC建ての取引所があるため。"""
    if not fee or fee.get("cost") is None:
        return 0.0
    cost = float(fee["cost"])
    currency = fee.get("currency")
    if currency == base_currency:
        return cost * price
    return cost  # JPY建て、または通貨不明ならそのまま扱う


def normalize_order_fill(
    order: dict, base_currency: str, fallback_amount: float, fallback_price: float
) -> tuple[float, float, float]:
    """ccxtの注文結果を (実受渡数量, 約定価格, 手数料JPY) に正規化する。

    - 部分約定: filled を使う(要求数量を記帳すると帳簿が実保有と乖離する)
    - 基軸通貨建て手数料(bitFlyerはBTCで徴収): 受渡数量から差し引く。
      取得原価は (数量×価格 + 手数料JPY) で支払総額と一致する
    """
    filled = float(order.get("filled") or fallback_amount)
    price = float(order.get("average") or fallback_price)
    fee = order.get("fee")
    fee_jpy = fee_to_jpy(fee, price, base_currency)
    if fee and fee.get("currency") == base_currency and fee.get("cost"):
        filled -= float(fee["cost"])
    return filled, price, fee_jpy


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
        self._apply_network_env()
        self._markets_loaded = False

    def _apply_network_env(self) -> None:
        """ccxtは環境変数のCA/プロキシ設定を無視する(trust_env=False)ため明示的に反映する。

        TLS検査型プロキシのある環境(社内ネットワーク等)向け。検証の無効化はしない。
        """
        ca = (
            os.environ.get("CRYPTOBOT_CA_BUNDLE")
            or os.environ.get("REQUESTS_CA_BUNDLE")
            or os.environ.get("SSL_CERT_FILE")
        )
        if ca:
            self.client.verify = ca
        proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
        if proxy:
            self.client.session.proxies = {"https": proxy, "http": proxy}

    def _market(self, symbol: str) -> dict:
        if not self._markets_loaded:
            self.client.load_markets()
            self._markets_loaded = True
        return self.client.market(symbol)

    def _assert_spot(self, symbol: str, params: dict | None) -> None:
        if params:
            bad = FORBIDDEN_PARAM_KEYS.intersection(params)
            if bad:
                raise SpotOnlyViolation(f"現物以外のパラメータは禁止: {sorted(bad)}")
        if not self._market(symbol).get("spot", False):
            raise SpotOnlyViolation(f"{symbol} はスポット市場ではありません")

    def base_currency(self, symbol: str) -> str:
        return self._market(symbol)["base"]

    def min_order_amount(self, symbol: str) -> float | None:
        """取引所の最低注文数量(BTC等)。不明ならNone。"""
        limits = self._market(symbol).get("limits") or {}
        return (limits.get("amount") or {}).get("min")

    def fetch_price(self, symbol: str) -> float:
        return float(self.client.fetch_ticker(symbol)["last"])

    def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int = 100) -> list[list[float]]:
        return self.client.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)

    def fetch_jpy_balance(self) -> float:
        """発注に使えるJPY残高(liveモード用)。"""
        balance = self.client.fetch_balance()
        return float((balance.get("JPY") or {}).get("free") or 0.0)

    def fetch_public_trades(self, symbol: str, limit: int = 30) -> list[dict]:
        """市場全体の約定履歴(公開データ)。ダッシュボードのフィード表示用。"""
        out = []
        for t in self.client.fetch_trades(symbol, limit=limit):
            if t.get("price") is None:
                continue
            out.append(
                {
                    "id": str(t.get("id") or t.get("timestamp")),
                    "ts": int(t.get("timestamp") or 0),
                    "side": t.get("side") or "buy",
                    "price": float(t["price"]),
                    "amount": float(t.get("amount") or 0.0),
                }
            )
        return out

    def market_buy(self, symbol: str, amount: float, params: dict | None = None) -> dict:
        self._assert_spot(symbol, params)
        return self.client.create_order(symbol, "market", "buy", amount)

    def market_sell(self, symbol: str, amount: float, params: dict | None = None) -> dict:
        self._assert_spot(symbol, params)
        return self.client.create_order(symbol, "market", "sell", amount)

    def normalize_fill(
        self, order: dict, symbol: str, fallback_amount: float, fallback_price: float
    ) -> tuple[float, float, float]:
        return normalize_order_fill(
            order, self.base_currency(symbol), fallback_amount, fallback_price
        )
