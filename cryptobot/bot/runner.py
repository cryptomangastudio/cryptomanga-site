"""メインループ。戦略シグナル → リスク承認 → 発注 → 記帳 の順で1サイクル回す。"""
from __future__ import annotations

import logging
import time
from datetime import datetime

from .config import BotConfig
from .journal import Fill, TradeJournal
from .paper import PaperBroker
from .risk import RiskManager
from .strategy import Action, MarketSnapshot, build_strategy

log = logging.getLogger("cryptobot")


class BotRunner:
    def __init__(self, cfg: BotConfig, exchange=None):
        self.cfg = cfg
        self.strategy = build_strategy(cfg)
        self.risk = RiskManager(cfg.risk, cfg.budget_jpy)
        self.journal = TradeJournal(cfg.journal_path)
        self.exchange = exchange  # SpotOnlyExchange(価格取得と、liveなら発注にも使う)
        self.paper = (
            PaperBroker(cfg.budget_jpy, cfg.fee_rate, cfg.paper_state_path)
            if cfg.mode == "paper"
            else None
        )

    def step(self, now: datetime, price: float, closes: list[float]) -> str:
        """1サイクル。何をしたかの説明文字列を返す。"""
        market = MarketSnapshot(
            price=price,
            closes=closes,
            position_amount=self.journal.position_amount,
            position_cost_jpy=self.journal.position_cost_jpy,
        )
        if self.paper:
            self.risk.update_equity(self.paper.equity(price))
        if self.risk.halted:
            return f"停止中: {self.risk.halt_reason}"

        signal = self.strategy.decide(market)
        if signal.action == Action.HOLD:
            return f"HOLD: {signal.reason}"

        sell_amount = market.position_amount if signal.jpy_amount == 0 else 0.0
        order_jpy = (
            signal.jpy_amount if signal.action == Action.BUY else sell_amount * price
        )
        decision = self.risk.check_order(
            signal.action.value, order_jpy, market.position_cost_jpy, now
        )
        if not decision.approved:
            return f"{signal.action.value.upper()}却下: {decision.reason}"

        if signal.action == Action.BUY:
            amount, fee = self._execute_buy(price, signal.jpy_amount)
        else:
            amount, fee = sell_amount, self._execute_sell(price, sell_amount)
        realized = self.journal.record(
            Fill(
                ts=now,
                exchange=self.cfg.exchange,
                symbol=self.cfg.symbol,
                side=signal.action.value,
                amount=amount,
                price=price,
                fee_jpy=fee,
                memo=f"[{self.cfg.mode}] {signal.reason}",
            )
        )
        self.risk.record_fill(now, realized)
        return (
            f"{signal.action.value.upper()} {amount:.8f} @ {price:.0f}円 "
            f"(実現損益 {realized:+.0f}円) - {signal.reason}"
        )

    def _execute_buy(self, price: float, jpy_amount: float) -> tuple[float, float]:
        if self.paper:
            return self.paper.market_buy(price, jpy_amount)
        order = self.exchange.market_buy(self.cfg.symbol, jpy_amount / price)
        filled = float(order.get("filled") or jpy_amount / price)
        fee = float((order.get("fee") or {}).get("cost") or 0.0)
        return filled, fee

    def _execute_sell(self, price: float, amount: float) -> float:
        """売却を実行し手数料JPYを返す。"""
        if self.paper:
            _, fee = self.paper.market_sell(price, amount)
            return fee
        order = self.exchange.market_sell(self.cfg.symbol, amount)
        return float((order.get("fee") or {}).get("cost") or 0.0)

    def run(self) -> None:
        assert self.exchange is not None, "run()には価格取得用のexchangeが必要"
        log.info(
            "起動 mode=%s strategy=%s symbol=%s budget=%s円",
            self.cfg.mode, self.cfg.strategy, self.cfg.symbol, self.cfg.budget_jpy,
        )
        while True:
            try:
                price = self.exchange.fetch_price(self.cfg.symbol)
                closes = [
                    c[4]
                    for c in self.exchange.fetch_ohlcv(
                        self.cfg.symbol,
                        self.cfg.ma_cross.timeframe,
                        limit=self.cfg.ma_cross.slow + 5,
                    )
                ]
                result = self.step(datetime.now(), price, closes)
                log.info("%s | 価格=%d円 | %s", self.cfg.symbol, price, result)
            except Exception:
                log.exception("サイクルでエラー(次の周期で再試行)")
            time.sleep(self.cfg.interval_seconds)
