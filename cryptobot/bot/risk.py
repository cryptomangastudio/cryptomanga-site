"""リスク管理。すべての注文はここの承認を通らない限り発注されない。

方針:
- 買い(リスクを増やす注文)は厳しく制限する
- 売り(リスクを減らす注文)は一切ブロックしない。halt中であっても
  「ポジション解消の売り」は常に許可する(DD超過=最も売るべき局面で
  売りを止めるのは停止の目的と真逆になるため)
- halt(買い全停止)はファイルに永続化され、人間が消すまで解除されない

多層ブレーカー(リサーチ#2): 日次損失 / 週次損失 / 連敗数 / 最大ドローダウン。
売買頻度ガバナー(リサーチ#3): 月間の買い回数上限(バグ暴走の最終防波堤)。
損失カウンタ・ピーク資産は risk_state.json に永続化され、再起動で
ブレーカーが武装解除されない(レッドチーム指摘対応)。
"""
from __future__ import annotations

import json
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
        max_buys_per_month: int | None = None,
        state_file: str | Path | None = None,
    ):
        self.cfg = cfg
        self.budget_jpy = budget_jpy
        self.halt_file = Path(halt_file) if halt_file else None
        self.state_file = Path(state_file) if state_file else None
        self.on_halt = on_halt  # 停止イベントのフック(通知など)。halt()から必ず呼ばれる
        self.max_buys_per_month = max_buys_per_month
        self.halted = False
        self.halt_reason = ""
        self._last_buy_at: datetime | None = None
        self._daily_realized_loss = 0.0
        self._daily_date: str | None = None
        self._weekly_realized_loss = 0.0
        self._weekly_key: str | None = None
        self._consecutive_losses = 0
        self._monthly_buys = 0
        self._monthly_key: str | None = None
        self._peak_equity: float | None = None
        self._load_state()
        if self.halt_file and self.halt_file.exists():
            self.halted = True
            self.halt_reason = (
                self.halt_file.read_text(encoding="utf-8").strip()
                or f"停止ファイル {self.halt_file} が存在"
            )

    def _load_state(self) -> None:
        """損失カウンタ・ピーク資産の復元(再起動でブレーカーが消えないように)。"""
        if not (self.state_file and self.state_file.exists()):
            return
        try:
            s = json.loads(self.state_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return  # 壊れていたら無視(保守側フォールバックはupdate_equityで効く)
        self._daily_date = s.get("daily_date")
        self._daily_realized_loss = float(s.get("daily_loss", 0.0))
        self._weekly_key = s.get("weekly_key")
        self._weekly_realized_loss = float(s.get("weekly_loss", 0.0))
        self._monthly_key = s.get("monthly_key")
        self._monthly_buys = int(s.get("monthly_buys", 0))
        self._consecutive_losses = int(s.get("consecutive_losses", 0))
        if s.get("peak_equity") is not None:
            self._peak_equity = float(s["peak_equity"])
        if s.get("last_buy_at"):
            self._last_buy_at = datetime.fromisoformat(s["last_buy_at"])

    def _save_state(self) -> None:
        if not self.state_file:
            return
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self.state_file.write_text(
            json.dumps(
                {
                    "daily_date": self._daily_date,
                    "daily_loss": self._daily_realized_loss,
                    "weekly_key": self._weekly_key,
                    "weekly_loss": self._weekly_realized_loss,
                    "monthly_key": self._monthly_key,
                    "monthly_buys": self._monthly_buys,
                    "consecutive_losses": self._consecutive_losses,
                    "peak_equity": self._peak_equity,
                    "last_buy_at": self._last_buy_at.isoformat() if self._last_buy_at else None,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
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
        iso = now.isocalendar()
        week = f"{iso[0]}-W{iso[1]:02d}"
        if week != self._weekly_key:
            self._weekly_key = week
            self._weekly_realized_loss = 0.0
        month = now.strftime("%Y-%m")
        if month != self._monthly_key:
            self._monthly_key = month
            self._monthly_buys = 0

    def check_order(
        self,
        side: str,
        order_jpy: float,
        position_cost_jpy: float,
        now: datetime,
    ) -> RiskDecision:
        """発注前チェック。position_cost_jpy は現在保有の取得原価合計。"""
        self._roll_day(now)
        if side != "buy":
            # 売り(リスク削減)はhalt中でも常に許可する。DD超過で止めるべきは買いだけ
            return RiskDecision(True, "OK(売りはリスク削減のため制限なし)")
        if self.halted:
            return RiskDecision(False, f"bot停止中(買い禁止): {self.halt_reason}")

        if self._daily_realized_loss >= self.cfg.max_daily_loss_jpy:
            return RiskDecision(
                False,
                f"本日の損失上限({self.cfg.max_daily_loss_jpy}円)に到達。本日の買いは停止",
            )
        if self._weekly_realized_loss >= self.cfg.max_weekly_loss_jpy:
            return RiskDecision(
                False,
                f"今週の損失上限({self.cfg.max_weekly_loss_jpy}円)に到達。今週の買いは停止",
            )
        if self._consecutive_losses >= self.cfg.max_consecutive_losses:
            return RiskDecision(
                False,
                f"{self._consecutive_losses}連敗中。買いを停止(勝ちトレードでリセット)",
            )
        if self.max_buys_per_month is not None and self._monthly_buys >= self.max_buys_per_month:
            return RiskDecision(
                False,
                f"今月の買い回数上限({self.max_buys_per_month}回)に到達(暴走防止ガバナー)",
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
        """約定後に呼ぶ。実現損益(売却時)を損失カウンタ・連敗数に反映する。"""
        self._roll_day(now)
        if side == "buy":
            self._last_buy_at = now
            self._monthly_buys += 1
        elif side == "sell":
            if realized_pnl_jpy < 0:
                self._consecutive_losses += 1
            elif realized_pnl_jpy > 0:
                self._consecutive_losses = 0
        if realized_pnl_jpy < 0:
            self._daily_realized_loss += -realized_pnl_jpy
            self._weekly_realized_loss += -realized_pnl_jpy
        self._save_state()

    def update_equity(self, equity_jpy: float) -> None:
        """資産評価額の更新。最大ドローダウン超過で買いを恒久停止する。"""
        if self._peak_equity is None:
            # 状態ファイルがない初回は、少なくとも予算額をピークとみなす
            # (暴落後の再起動で「今の資産が新ピーク」になるラチェットを防ぐ)
            self._peak_equity = max(float(self.budget_jpy), equity_jpy)
        elif equity_jpy > self._peak_equity:
            self._peak_equity = equity_jpy
        self._save_state()
        drawdown_pct = (1 - equity_jpy / self._peak_equity) * 100
        if drawdown_pct >= self.cfg.max_drawdown_pct and not self.halted:
            self.halt(
                f"最大ドローダウン{self.cfg.max_drawdown_pct}%超過"
                f"(現在{drawdown_pct:.1f}%)。買いを停止(売り・ポジション解消は可能)。"
                "人間の判断があるまで再開しない"
            )
