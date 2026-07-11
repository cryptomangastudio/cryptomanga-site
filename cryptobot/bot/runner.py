"""メインループ。戦略シグナル → リスク承認 → 発注 → 記帳 → 通知 の順で1サイクル回す。"""
from __future__ import annotations

import logging
import time
from datetime import datetime

from .config import BotConfig
from .exchange import fee_to_jpy
from .journal import Fill, TradeJournal
from .notify import Notifier
from .paper import PaperBroker
from .risk import RiskManager
from .strategy import Action, MarketSnapshot, build_strategy

log = logging.getLogger("cryptobot")

MIN_BUY_JPY = 1_000  # これ未満に切り詰められた買いはスキップ(手数料負け防止)


class BotRunner:
    def __init__(self, cfg: BotConfig, exchange=None):
        self.cfg = cfg
        self.strategy = build_strategy(cfg)
        self.risk = RiskManager(cfg.risk, cfg.budget_jpy, cfg.halt_file)
        self.journal = TradeJournal(cfg.journal_path)
        self.notifier = Notifier(cfg.notify.format)
        self.exchange = exchange  # SpotOnlyExchange(価格取得と、liveなら発注にも使う)
        self.paper = (
            PaperBroker(cfg.budget_jpy, cfg.fee_rate, cfg.paper_state_path)
            if cfg.mode == "paper"
            else None
        )
        self._check_state_consistency()

    def _check_state_consistency(self) -> None:
        """ペーパー残高と記帳簿の建玉がズレていたら警告(片方だけ消した等)。"""
        if self.paper and abs(self.paper.base_amount - self.journal.position_amount) > 1e-9:
            log.warning(
                "ペーパー残高(%.8f)と記帳簿の建玉(%.8f)が一致しません。"
                "data/ 以下を片方だけ削除しませんでしたか?",
                self.paper.base_amount,
                self.journal.position_amount,
            )

    def _available_jpy(self) -> float | None:
        """買いに使えるJPY。取得できない場合はNone。"""
        if self.paper:
            return self.paper.jpy
        if self.exchange:
            return self.exchange.fetch_jpy_balance()
        return None

    def step(self, now: datetime, price: float, closes: list[float]) -> str:
        """1サイクル。何をしたかの説明文字列を返す。"""
        market = MarketSnapshot(
            price=price,
            closes=closes,
            position_amount=self.journal.position_amount,
            position_cost_jpy=self.journal.position_cost_jpy,
        )
        equity = None
        if self.paper:
            equity = self.paper.equity(price)
        elif self.cfg.mode == "live" and self.exchange is not None:
            try:
                equity = self.exchange.fetch_jpy_balance() + market.position_amount * price
            except Exception as e:
                log.warning("残高取得失敗(この周期はドローダウン監視をスキップ): %s", e)
        if equity is not None:
            was_halted = self.risk.halted
            self.risk.update_equity(equity)
            if self.risk.halted and not was_halted:
                self.notifier.send(f"🛑 CryptoBot停止: {self.risk.halt_reason}")
        if self.risk.halted:
            return f"停止中: {self.risk.halt_reason}"

        signal = self.strategy.decide(market)
        if signal.action == Action.HOLD:
            return f"HOLD: {signal.reason}"

        if signal.action == Action.BUY:
            return self._try_buy(now, price, market, signal)
        return self._try_sell(now, price, market, signal)

    def _try_buy(self, now, price, market, signal) -> str:
        buy_jpy = signal.jpy_amount
        available = self._available_jpy()
        if available is not None:
            buy_jpy = min(buy_jpy, available)
        if buy_jpy < MIN_BUY_JPY:
            return f"BUYスキップ: 発注可能額{buy_jpy:.0f}円 < 最低{MIN_BUY_JPY}円(手数料負け防止)"

        # 取引所の最低注文数量チェック(bitFlyerは0.001 BTC等。少額運用の要注意点)
        amount_estimate = buy_jpy / price
        min_amount = self.exchange.min_order_amount(self.cfg.symbol) if self.exchange else None
        if min_amount and amount_estimate < min_amount:
            return (
                f"BUYスキップ: 注文数量{amount_estimate:.8f} < 取引所の最低数量{min_amount}。"
                f"約{min_amount * price:.0f}円以上の注文が必要です(config.yamlと運用計画の見直しを)"
            )

        decision = self.risk.check_order("buy", buy_jpy, market.position_cost_jpy, now)
        if not decision.approved:
            return f"BUY却下: {decision.reason}"

        if self.paper:
            amount, fee_jpy = self.paper.market_buy(price, buy_jpy)
            fill_price = price
        else:
            order = self.exchange.market_buy(self.cfg.symbol, amount_estimate)
            amount = float(order.get("filled") or amount_estimate)
            fill_price = float(order.get("average") or price)
            fee_jpy = fee_to_jpy(
                order.get("fee"), fill_price, self.exchange.base_currency(self.cfg.symbol)
            )
        return self._book(now, "buy", amount, fill_price, fee_jpy, signal.reason)

    def _try_sell(self, now, price, market, signal) -> str:
        amount = market.position_amount  # 現状は全量売却のみ(現物なので保有分だけ)
        if amount <= 0:
            return "SELLスキップ: 保有なし"
        decision = self.risk.check_order("sell", amount * price, market.position_cost_jpy, now)
        if not decision.approved:
            return f"SELL却下: {decision.reason}"

        if self.paper:
            _, fee_jpy = self.paper.market_sell(price, amount)
            fill_price = price
        else:
            order = self.exchange.market_sell(self.cfg.symbol, amount)
            fill_price = float(order.get("average") or price)
            fee_jpy = fee_to_jpy(
                order.get("fee"), fill_price, self.exchange.base_currency(self.cfg.symbol)
            )
        return self._book(now, "sell", amount, fill_price, fee_jpy, signal.reason)

    def _book(self, now, side, amount, price, fee_jpy, reason) -> str:
        realized = self.journal.record(
            Fill(
                ts=now,
                exchange=self.cfg.exchange,
                symbol=self.cfg.symbol,
                side=side,
                amount=amount,
                price=price,
                fee_jpy=fee_jpy,
                memo=f"[{self.cfg.mode}] {reason}",
            )
        )
        self.risk.record_fill(now, side, realized)
        line = (
            f"{side.upper()} {amount:.8f} @ {price:,.0f}円"
            + (f"(実現損益 {realized:+,.0f}円 ※課税対象)" if side == "sell" else "")
            + f" - {reason}"
        )
        self.notifier.send(f"{'🟢' if side == 'buy' else '🔴'} [{self.cfg.mode}] {line}")
        return line

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
                log.info("%s | 価格=%s円 | %s", self.cfg.symbol, f"{price:,.0f}", result)
            except Exception:
                log.exception("サイクルでエラー(次の周期で再試行)")
            time.sleep(self.cfg.interval_seconds)
