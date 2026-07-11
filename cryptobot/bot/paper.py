"""ペーパートレード(仮想売買)ブローカー。実際の発注は一切行わない。"""
from __future__ import annotations

import json
from pathlib import Path

from .journal import EPS


class PaperBroker:
    def __init__(self, jpy_balance: float, fee_rate: float, state_path: str | Path | None = None):
        self.jpy = jpy_balance
        self.base_amount = 0.0  # 保有数量(BTCなど)
        self.fee_rate = fee_rate
        self.state_path = Path(state_path) if state_path else None
        self._load()

    def market_buy(self, price: float, jpy_amount: float) -> tuple[float, float]:
        """成行買いをシミュレート。(約定数量, 手数料JPY) を返す。"""
        if jpy_amount > self.jpy:
            raise ValueError(f"残高不足: 残高{self.jpy:.0f}円 < 注文{jpy_amount:.0f}円")
        fee = jpy_amount * self.fee_rate
        amount = (jpy_amount - fee) / price
        self.jpy -= jpy_amount
        self.base_amount += amount
        self._save()
        return amount, fee

    def market_sell(self, price: float, amount: float) -> tuple[float, float]:
        """成行売りをシミュレート。(受取JPY, 手数料JPY) を返す。"""
        if amount > self.base_amount + EPS:
            raise ValueError(f"保有不足: 保有{self.base_amount} < 売却{amount}")
        gross = amount * price
        fee = gross * self.fee_rate
        self.base_amount -= amount
        self.jpy += gross - fee
        self._save()
        return gross - fee, fee

    def equity(self, price: float) -> float:
        return self.jpy + self.base_amount * price

    def _save(self) -> None:
        if self.state_path:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            self.state_path.write_text(
                json.dumps({"jpy": self.jpy, "base_amount": self.base_amount}),
                encoding="utf-8",
            )

    def _load(self) -> None:
        if self.state_path and self.state_path.exists():
            state = json.loads(self.state_path.read_text(encoding="utf-8"))
            self.jpy = state["jpy"]
            self.base_amount = state["base_amount"]
