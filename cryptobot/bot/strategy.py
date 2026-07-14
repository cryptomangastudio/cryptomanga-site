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
    highs: list[float] | None = None  # 高値(ATRのTrue Range計算用。無ければ終値差で近似)
    lows: list[float] | None = None


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
    """単純移動平均のクロス。ゴールデンクロスで買い、fast<slowの間は売り。

    出口は「クロスの瞬間」ではなく「fastがslowを下回っている状態」で判定する
    (レベル判定)。クロス瞬間のみの判定だと、その1バーで売り損ねたとき
    (未約定・再起動・halt中)に出口シグナルが二度と出ない。
    買いには小さなヒステリシス(閾値)を設け、ノイズ1本での往復を減らす。
    """

    BUY_HYSTERESIS = 0.001  # fastがslowを0.1%以上上抜けた場合のみ買い

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
        if market.position_amount > 0 and fast_now < slow_now:
            return Signal(Action.SELL, 0.0, "fastがslowを下回った(全量売却)")
        golden = (
            fast_prev <= slow_prev
            and fast_now > slow_now * (1 + self.BUY_HYSTERESIS)
        )
        if golden and market.position_amount == 0:
            return Signal(Action.BUY, float(self.buy_amount_jpy), "ゴールデンクロス")
        return Signal.hold("クロスなし")


def build_strategy(cfg) -> Strategy:
    if cfg.strategy == "dca":
        return DCAStrategy(cfg.dca.buy_amount_jpy)
    if cfg.strategy == "ma_cross":
        return MACrossStrategy(cfg.ma_cross.fast, cfg.ma_cross.slow, cfg.risk.max_order_jpy)
    raise ValueError(f"未知の戦略: {cfg.strategy}")
