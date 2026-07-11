"""売買戦略。戦略は「シグナルを出すだけ」で、発注可否はRiskManagerが決める。"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Action(Enum):
    BUY = "buy"
    SELL = "sell"
    HOLD = "hold"


@dataclass
class Signal:
    action: Action
    jpy_amount: float = 0.0  # BUY時: 使う日本円。SELL時: 0なら全量売却
    reason: str = ""

    @staticmethod
    def hold(reason: str = "") -> "Signal":
        return Signal(Action.HOLD, 0.0, reason)


@dataclass
class MarketSnapshot:
    price: float
    closes: list[float]  # 直近の終値(古い→新しい)。DCAでは空でよい
    position_amount: float  # 保有数量(BTCなど)
    position_cost_jpy: float  # 保有分の取得原価合計


class Strategy:
    def decide(self, market: MarketSnapshot) -> Signal:
        raise NotImplementedError


class DCAStrategy(Strategy):
    """定期積立。呼ばれるたびに固定額の買いシグナルを出す(間隔はrunnerが制御)。"""

    def __init__(self, buy_amount_jpy: int):
        self.buy_amount_jpy = buy_amount_jpy

    def decide(self, market: MarketSnapshot) -> Signal:
        return Signal(Action.BUY, float(self.buy_amount_jpy), "DCA定期買付")


def sma(values: list[float], window: int) -> float:
    return sum(values[-window:]) / window


class MACrossStrategy(Strategy):
    """単純移動平均のクロス。ゴールデンクロスで買い、デッドクロスで全量売却。"""

    def __init__(self, fast: int, slow: int, buy_amount_jpy: int):
        assert fast < slow
        self.fast = fast
        self.slow = slow
        self.buy_amount_jpy = buy_amount_jpy

    def decide(self, market: MarketSnapshot) -> Signal:
        closes = market.closes
        if len(closes) < self.slow + 1:
            return Signal.hold("データ不足")
        fast_now = sma(closes, self.fast)
        slow_now = sma(closes, self.slow)
        fast_prev = sma(closes[:-1], self.fast)
        slow_prev = sma(closes[:-1], self.slow)
        golden = fast_prev <= slow_prev and fast_now > slow_now
        dead = fast_prev >= slow_prev and fast_now < slow_now
        if golden and market.position_amount == 0:
            return Signal(Action.BUY, float(self.buy_amount_jpy), "ゴールデンクロス")
        if dead and market.position_amount > 0:
            return Signal(Action.SELL, 0.0, "デッドクロス(全量売却)")
        return Signal.hold("クロスなし")


def build_strategy(cfg) -> Strategy:
    if cfg.strategy == "dca":
        return DCAStrategy(cfg.dca.buy_amount_jpy)
    if cfg.strategy == "ma_cross":
        return MACrossStrategy(cfg.ma_cross.fast, cfg.ma_cross.slow, cfg.risk.max_order_jpy)
    raise ValueError(f"未知の戦略: {cfg.strategy}")
