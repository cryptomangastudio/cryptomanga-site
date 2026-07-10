"""取引記帳。移動平均法で取得単価と実現損益を計算し、CSVに追記する。

このCSVは確定申告の基礎資料になる(暗号資産の所得計算は移動平均法/総平均法)。
注意: 利益を出して売却した時点で、日本円に出金していなくても課税対象。
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

HEADER = [
    "日時",
    "取引所",
    "銘柄",
    "売買",
    "数量",
    "約定価格(JPY)",
    "約定金額(JPY)",
    "手数料(JPY)",
    "取得単価_移動平均(JPY)",
    "実現損益(JPY)",
    "累計実現損益(JPY)",
    "メモ",
]


@dataclass
class Fill:
    ts: datetime
    exchange: str
    symbol: str
    side: str  # buy | sell
    amount: float
    price: float
    fee_jpy: float
    memo: str = ""


class TradeJournal:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.position_amount = 0.0
        self.avg_cost = 0.0  # 移動平均法による取得単価
        self.total_realized_pnl = 0.0
        self._ensure_header()

    def _ensure_header(self) -> None:
        if not self.path.exists():
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("w", newline="", encoding="utf-8") as f:
                csv.writer(f).writerow(HEADER)

    @property
    def position_cost_jpy(self) -> float:
        return self.position_amount * self.avg_cost

    def record(self, fill: Fill) -> float:
        """約定を記帳し、実現損益(JPY)を返す(買いは常に0)。"""
        realized = 0.0
        if fill.side == "buy":
            # 手数料は取得原価に含める(移動平均法)
            new_cost = self.position_cost_jpy + fill.amount * fill.price + fill.fee_jpy
            self.position_amount += fill.amount
            self.avg_cost = new_cost / self.position_amount if self.position_amount else 0.0
        elif fill.side == "sell":
            if fill.amount > self.position_amount + 1e-12:
                raise ValueError(
                    f"保有量{self.position_amount}を超える売却: {fill.amount}"
                )
            realized = (fill.price - self.avg_cost) * fill.amount - fill.fee_jpy
            self.position_amount -= fill.amount
            if self.position_amount <= 1e-12:
                self.position_amount = 0.0
                self.avg_cost = 0.0
            self.total_realized_pnl += realized
        else:
            raise ValueError(f"不正なside: {fill.side}")

        with self.path.open("a", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow(
                [
                    fill.ts.strftime("%Y-%m-%d %H:%M:%S"),
                    fill.exchange,
                    fill.symbol,
                    "買" if fill.side == "buy" else "売",
                    f"{fill.amount:.8f}",
                    f"{fill.price:.0f}",
                    f"{fill.amount * fill.price:.0f}",
                    f"{fill.fee_jpy:.1f}",
                    f"{self.avg_cost:.0f}",
                    f"{realized:.0f}",
                    f"{self.total_realized_pnl:.0f}",
                    fill.memo,
                ]
            )
        return realized
