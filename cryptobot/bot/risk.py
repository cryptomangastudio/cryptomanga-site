"""リスク管理。すべての注文はここの承認を通らない限り発注されない。"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from .config import RiskConfig


@dataclass
class RiskDecision:
    approved: bool
    reason: str


class RiskManager:
    def __init__(self, cfg: RiskConfig, budget_jpy: int):
        self.cfg = cfg
        self.budget_jpy = budget_jpy
        self.halted = False
        self.halt_reason = ""
        self._last_order_at: datetime | None = None
        self._daily_realized_loss = 0.0
        self._daily_date: str | None = None
        self._peak_equity: float | None = None

    def _roll_day(self, now: datetime) -> None:
        day = now.strftime("%Y-%m-%d")
        if day != self._daily_date:
            self._daily_date = day
            self._daily_realized_loss = 0.0

    def check_order(
        self,
        side: str,
        order_jpy: float,
        position_cost_jpy: float,
        now: datetime,
    ) -> RiskDecision:
        """発注前チェック。position_cost_jpy は現在保有の取得原価合計。"""
        self._roll_day(now)
        if self.halted:
            return RiskDecision(False, f"bot停止中: {self.halt_reason}")
        if self._daily_realized_loss >= self.cfg.max_daily_loss_jpy:
            return RiskDecision(
                False,
                f"本日の損失上限({self.cfg.max_daily_loss_jpy}円)に到達。本日は停止",
            )
        if self._last_order_at is not None:
            wait_until = self._last_order_at + timedelta(minutes=self.cfg.cooldown_minutes)
            if now < wait_until:
                return RiskDecision(False, f"クールダウン中({wait_until:%H:%M}まで)")
        if side == "buy":
            if order_jpy > self.cfg.max_order_jpy:
                return RiskDecision(
                    False, f"注文額{order_jpy:.0f}円 > 1回上限{self.cfg.max_order_jpy}円"
                )
            if position_cost_jpy + order_jpy > self.cfg.max_position_jpy:
                return RiskDecision(
                    False,
                    f"保有上限{self.cfg.max_position_jpy}円を超過"
                    f"(現在{position_cost_jpy:.0f}円 + 注文{order_jpy:.0f}円)",
                )
            if position_cost_jpy + order_jpy > self.budget_jpy:
                return RiskDecision(False, f"総予算{self.budget_jpy}円を超過")
        return RiskDecision(True, "OK")

    def record_fill(self, now: datetime, realized_pnl_jpy: float = 0.0) -> None:
        """約定後に呼ぶ。実現損益(売却時)を日次損失に反映する。"""
        self._roll_day(now)
        self._last_order_at = now
        if realized_pnl_jpy < 0:
            self._daily_realized_loss += -realized_pnl_jpy

    def update_equity(self, equity_jpy: float) -> None:
        """資産評価額の更新。最大ドローダウン超過でbotを恒久停止する。"""
        if self._peak_equity is None or equity_jpy > self._peak_equity:
            self._peak_equity = equity_jpy
        drawdown_pct = (1 - equity_jpy / self._peak_equity) * 100
        if drawdown_pct >= self.cfg.max_drawdown_pct:
            self.halted = True
            self.halt_reason = (
                f"最大ドローダウン{self.cfg.max_drawdown_pct}%超過"
                f"(現在{drawdown_pct:.1f}%)。人間の判断があるまで再開しない"
            )
