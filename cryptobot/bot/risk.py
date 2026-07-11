"""リスク管理。すべての注文はここの承認を通らない限り発注されない。

方針:
- 買い(リスクを増やす注文)は厳しく制限する
- 売り(リスクを減らす注文)はクールダウン・日次損失停止の対象外
- 全停止(halt)だけはすべての注文を止める。haltはファイルに永続化され、
  人間がファイルを消すまで再起動しても解除されない
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from .config import RiskConfig


@dataclass
class RiskDecision:
    approved: bool
    reason: str


class RiskManager:
    def __init__(
        self,
        cfg: RiskConfig,
        budget_jpy: int,
        halt_file: str | Path | None = None,
        on_halt: Callable[[str], None] | None = None,
    ):
        self.cfg = cfg
        self.budget_jpy = budget_jpy
        self.halt_file = Path(halt_file) if halt_file else None
        self.on_halt = on_halt  # 停止イベントのフック(通知など)。halt()から必ず呼ばれる
        self.halted = False
        self.halt_reason = ""
        self._last_buy_at: datetime | None = None
        self._daily_realized_loss = 0.0
        self._daily_date: str | None = None
        self._peak_equity: float | None = None
        if self.halt_file and self.halt_file.exists():
            self.halted = True
            self.halt_reason = (
                self.halt_file.read_text(encoding="utf-8").strip()
                or f"停止ファイル {self.halt_file} が存在"
            )

    def halt(self, reason: str) -> None:
        self.halted = True
        self.halt_reason = reason
        if self.halt_file:
            self.halt_file.parent.mkdir(parents=True, exist_ok=True)
            self.halt_file.write_text(reason + "\n", encoding="utf-8")
        if self.on_halt:
            self.on_halt(reason)

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
            return RiskDecision(
                False,
                f"bot停止中: {self.halt_reason}"
                + (f"(再開するには {self.halt_file} を削除)" if self.halt_file else ""),
            )
        if side != "buy":
            return RiskDecision(True, "OK(売りはリスク削減のため制限なし)")

        if self._daily_realized_loss >= self.cfg.max_daily_loss_jpy:
            return RiskDecision(
                False,
                f"本日の損失上限({self.cfg.max_daily_loss_jpy}円)に到達。本日の買いは停止",
            )
        if self._last_buy_at is not None:
            wait_until = self._last_buy_at + timedelta(minutes=self.cfg.cooldown_minutes)
            if now < wait_until:
                return RiskDecision(False, f"クールダウン中({wait_until:%H:%M}まで)")
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

    def record_fill(self, now: datetime, side: str, realized_pnl_jpy: float = 0.0) -> None:
        """約定後に呼ぶ。実現損益(売却時)を日次損失に反映する。"""
        self._roll_day(now)
        if side == "buy":
            self._last_buy_at = now
        if realized_pnl_jpy < 0:
            self._daily_realized_loss += -realized_pnl_jpy

    def update_equity(self, equity_jpy: float) -> None:
        """資産評価額の更新。最大ドローダウン超過でbotを恒久停止する。"""
        if self._peak_equity is None or equity_jpy > self._peak_equity:
            self._peak_equity = equity_jpy
        drawdown_pct = (1 - equity_jpy / self._peak_equity) * 100
        if drawdown_pct >= self.cfg.max_drawdown_pct and not self.halted:
            self.halt(
                f"最大ドローダウン{self.cfg.max_drawdown_pct}%超過"
                f"(現在{drawdown_pct:.1f}%)。人間の判断があるまで再開しない"
            )
