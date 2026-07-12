"""メインループ。

1サイクル = 価格サニティ → 戦略シグナル → レジームフィルター/コストゲート/
サイジング(リサーチ#3,#5,#8) → リスク承認(#2) → 執行(#1) → 記帳+実効コスト台帳(#4)
→ 通知。
"""
from __future__ import annotations

import logging
import time
from datetime import datetime

from .config import BotConfig
from .execution import MakerExecutor, paper_fee_rate, round_trip_cost_pct
from .journal import EPS, Fill, TradeJournal
from .notify import Notifier
from .paper import PaperBroker
from .risk import RiskManager
from .shortfall import ShortfallLedger
from .strategy import Action, MarketSnapshot, build_strategy

log = logging.getLogger("cryptobot")

MIN_BUY_JPY = 1_000  # これ未満に切り詰められた買いはスキップ(手数料負け防止)


def fetch_closes(exchange, cfg: BotConfig) -> list[float]:
    """戦略が必要とする終値履歴を取得する。

    DCAは価格履歴を使わないので取得しない(bitFlyer等、OHLCV API非対応の
    取引所でもDCA運用できるようにするため)。ma_crossはOHLCV対応の取引所
    (例: bitbank)が必要。
    """
    if cfg.strategy != "ma_cross":
        return []
    return [
        c[4]
        for c in exchange.fetch_ohlcv(
            cfg.symbol, cfg.ma_cross.timeframe, limit=cfg.ma_cross.slow + 5
        )
    ]


def atr_estimate(closes: list[float], period: int = 14) -> float | None:
    """終値ベースの簡易ATR(1バーあたりの平均値動き)。データ不足ならNone。"""
    if len(closes) < period + 1:
        return None
    diffs = [abs(closes[i] - closes[i - 1]) for i in range(1, len(closes))]
    window = diffs[-period:]
    return sum(window) / len(window)


class BotRunner:
    def __init__(self, cfg: BotConfig, exchange=None):
        self.cfg = cfg
        self.strategy = build_strategy(cfg)
        self.notifier = Notifier(cfg.notify.format)
        self.risk = RiskManager(
            cfg.risk,
            cfg.budget_jpy,
            cfg.halt_file,
            on_halt=lambda reason: self.notifier.send(f"🛑 CryptoBot停止: {reason}"),
            max_buys_per_month=cfg.governor.max_buys_per_month,
        )
        self.journal = TradeJournal(cfg.journal_path)
        self.shortfall = ShortfallLedger(cfg.shortfall_path)
        self.exchange = exchange  # SpotOnlyExchange(価格取得と、liveなら発注にも使う)
        self.paper = (
            PaperBroker(cfg.budget_jpy, paper_fee_rate(cfg), cfg.paper_state_path)
            if cfg.mode == "paper"
            else None
        )
        self.maker = (
            MakerExecutor(exchange, cfg)
            if cfg.mode == "live" and cfg.execution.style == "maker" and exchange
            else None
        )
        self._last_sane_price: float | None = None
        self._ma_cache: tuple[str, float | None] | None = None
        self._regime_warned = False
        self._check_state_consistency()

    def _check_state_consistency(self) -> None:
        """ペーパー残高と記帳簿の建玉がズレていたら警告(片方だけ消した等)。"""
        if self.paper and abs(self.paper.base_amount - self.journal.position_amount) > EPS:
            log.warning(
                "ペーパー残高(%.12f)と記帳簿の建玉(%.12f)が一致しません。"
                "data/ 以下を片方だけ削除しませんでしたか?",
                self.paper.base_amount,
                self.journal.position_amount,
            )

    def _bot_cash_jpy(self) -> float:
        """botの自己勘定上の現金。予算 + 累計実現損益 - 建玉の取得原価。

        liveモードでも取引所口座全体の残高を使わない: 口座への入出金(botと
        無関係な資金移動)を損益やドローダウンとして誤検知しないため。
        """
        return self.cfg.budget_jpy + self.journal.total_realized_pnl - self.journal.position_cost_jpy

    def _equity_jpy(self, price: float) -> float:
        if self.paper:
            return self.paper.equity(price)
        return self._bot_cash_jpy() + self.journal.position_amount * price

    def _available_jpy(self) -> float:
        """買いに使えるJPY。live時は実残高が下回っていればそちらを優先する。"""
        if self.paper:
            return self.paper.jpy
        cash = self._bot_cash_jpy()
        if self.exchange is not None:
            try:
                cash = min(cash, self.exchange.fetch_jpy_balance())
            except Exception as e:
                log.warning("残高取得失敗(自己勘定の値を使用): %s", e)
        return cash

    def _regime_ma(self, now: datetime) -> float | None:
        """200日移動平均(レジームフィルター#8)。日足非対応の取引所ではNone。"""
        if not (self.cfg.regime.enabled and self.exchange is not None):
            return None
        key = now.strftime("%Y-%m-%d")
        if self._ma_cache and self._ma_cache[0] == key:
            return self._ma_cache[1]
        ma = None
        try:
            closes = [
                c[4]
                for c in self.exchange.fetch_ohlcv(
                    self.cfg.symbol, "1d", limit=self.cfg.regime.ma_days + 5
                )
            ]
            if len(closes) >= self.cfg.regime.ma_days:
                ma = sum(closes[-self.cfg.regime.ma_days :]) / self.cfg.regime.ma_days
        except Exception as e:
            if not self._regime_warned:
                log.warning(
                    "%s: 日足が取得できないためレジームフィルターは無効(%s)",
                    self.cfg.symbol, e,
                )
                self._regime_warned = True
        self._ma_cache = (key, ma)
        return ma

    def step(self, now: datetime, price: float, closes: list[float]) -> str:
        """1サイクル。何をしたかの説明文字列を返す。"""
        # 価格サニティチェック(#2): 異常値・誤参照への防御
        if self._last_sane_price is not None and self.cfg.price_sanity_pct > 0:
            jump_pct = abs(price / self._last_sane_price - 1) * 100
            if jump_pct > self.cfg.price_sanity_pct:
                prev = self._last_sane_price
                self._last_sane_price = price  # 実相場の急変なら次周期から再開できる
                return (
                    f"スキップ: 価格が前回比{jump_pct:.1f}%乖離(異常値防御。"
                    f"前回{prev:,.0f}円→今回{price:,.0f}円)"
                )
        self._last_sane_price = price

        market = MarketSnapshot(
            price=price,
            closes=closes,
            position_amount=self.journal.position_amount,
            position_cost_jpy=self.journal.position_cost_jpy,
        )
        self.risk.update_equity(self._equity_jpy(price))
        if self.risk.halted:
            return f"停止中: {self.risk.halt_reason}"

        signal = self.strategy.decide(market)
        if signal.action == Action.HOLD:
            return f"HOLD: {signal.reason}"

        if signal.action == Action.BUY:
            return self._try_buy(now, price, market, signal)
        return self._try_sell(now, price, market, signal)

    def _apply_buy_gates(self, now, price, market, signal) -> tuple[float, str] | str:
        """レジーム(#8)・コストゲート(#3)・サイジング(#5)。(買付額, 補足) か却下文字列を返す。"""
        buy_jpy = signal.jpy_amount
        notes = []
        ma = self._regime_ma(now)

        if self.cfg.strategy == "ma_cross":
            if ma is not None and price < ma:
                return f"BUYスキップ: レジームフィルター(価格が{self.cfg.regime.ma_days}日MA {ma:,.0f}円 未満)"
            atr = atr_estimate(market.closes)
            if self.cfg.cost_gate.enabled and atr is not None:
                edge_pct = atr / price * 100
                cost_pct = round_trip_cost_pct(self.cfg)
                if edge_pct < self.cfg.cost_gate.k * cost_pct:
                    return (
                        f"BUYスキップ: コストゲート(期待値動き{edge_pct:.3f}% < "
                        f"往復コスト{cost_pct:.3f}%×{self.cfg.cost_gate.k})"
                    )
            equity = self._equity_jpy(price)
            if atr is not None and atr > 0:
                stop_pct = atr * self.cfg.sizing.atr_mult / price
                size_jpy = equity * (self.cfg.sizing.risk_pct / 100) / stop_pct
                if size_jpy < buy_jpy:
                    buy_jpy = size_jpy
                    notes.append(f"ATRサイジング→{buy_jpy:,.0f}円")
            kelly = self.journal.kelly_fraction()
            if kelly is not None and self.journal.sell_count >= self.cfg.sizing.kelly_min_trades:
                if kelly <= 0:
                    return (
                        f"BUYスキップ: ケリー推定が負({kelly:.2f}、直近{self.journal.sell_count}回の"
                        "売却実績で期待値マイナス)。戦略を見直すまで新規買い停止"
                    )
                cap = kelly * self.cfg.sizing.kelly_cap * equity
                if cap < buy_jpy:
                    buy_jpy = cap
                    notes.append(f"ケリー上限→{buy_jpy:,.0f}円")
        elif self.cfg.strategy == "dca" and ma is not None and self.cfg.regime.dca_tilt > 0:
            # DCA傾斜(#8): 200日MAより安いほど多く、高いほど少なく買う
            deviation = (price - ma) / ma
            tilt = self.cfg.regime.dca_tilt
            factor = max(1 - tilt, min(1 + tilt, 1 - deviation))
            buy_jpy *= factor
            notes.append(f"MA乖離{deviation:+.1%}→積立×{factor:.2f}")

        return buy_jpy, ("(" + " / ".join(notes) + ")" if notes else "")

    def _try_buy(self, now, price, market, signal) -> str:
        gated = self._apply_buy_gates(now, price, market, signal)
        if isinstance(gated, str):
            return gated
        buy_jpy, note = gated

        buy_jpy = min(buy_jpy, self._available_jpy())
        if not self.paper:
            # live: 注文額 + 手数料 が残高に収まるよう手数料ぶんの余裕を取る
            buy_jpy /= 1 + max(paper_fee_rate(self.cfg), 0)
        if buy_jpy < MIN_BUY_JPY:
            return f"BUYスキップ: 発注可能額{buy_jpy:.0f}円 < 最低{MIN_BUY_JPY}円(手数料負け防止)"

        # 取引所の最低注文数量チェック(bitFlyerは0.001 BTC等。少額運用の要注意点)
        amount_estimate = buy_jpy / price
        min_amount = None
        if self.exchange is not None:
            try:
                min_amount = self.exchange.min_order_amount(self.cfg.symbol)
            except Exception as e:
                log.warning("最低注文数量の取得失敗(チェックを省略): %s", e)
        if min_amount and amount_estimate < min_amount:
            # リサーチ#5: 切り上げは1%ルールの静かな崩壊なので「見送り」にする
            return (
                f"BUYスキップ: 注文数量{amount_estimate:.8f} < 取引所の最低数量{min_amount}。"
                f"約{min_amount * price:.0f}円以上の注文が必要です(config.yamlと運用計画の見直しを)"
            )

        decision = self.risk.check_order("buy", buy_jpy, market.position_cost_jpy, now)
        if not decision.approved:
            return f"BUY却下: {decision.reason}"

        wait_s = 0.0
        if self.paper:
            amount, fee_jpy = self.paper.market_buy(price, buy_jpy)
            fill_price = price
        elif self.maker:
            result = self.maker.execute(self.cfg.symbol, "buy", amount_estimate)
            if result.amount <= 0:
                return (
                    f"BUY見送り: メイカー指値が{result.requotes}回の再指値でも約定せず"
                    "(テイカーには逃げない方針)"
                )
            amount, fill_price, fee_jpy = result.amount, result.price, result.fee_jpy
            wait_s = result.wait_seconds
        else:
            order = self.exchange.market_buy(self.cfg.symbol, amount_estimate)
            amount, fill_price, fee_jpy = self.exchange.normalize_fill(
                order, self.cfg.symbol, amount_estimate, price
            )
        return self._book(now, "buy", amount, fill_price, fee_jpy, signal.reason + note,
                          signal_price=price, wait_seconds=wait_s)

    def _try_sell(self, now, price, market, signal) -> str:
        amount = market.position_amount  # 現状は全量売却のみ(現物なので保有分だけ)
        if amount <= 0:
            return "SELLスキップ: 保有なし"
        decision = self.risk.check_order("sell", amount * price, market.position_cost_jpy, now)
        if not decision.approved:
            return f"SELL却下: {decision.reason}"

        wait_s = 0.0
        if self.paper:
            _, fee_jpy = self.paper.market_sell(price, amount)
            fill_price = price
        elif self.maker:
            result = self.maker.execute(self.cfg.symbol, "sell", amount)
            if result.amount <= 0:
                return (
                    f"SELL見送り: メイカー指値が{result.requotes}回の再指値でも約定せず"
                    "(次の周期のシグナルで再試行)"
                )
            amount, fill_price, fee_jpy = result.amount, result.price, result.fee_jpy
            wait_s = result.wait_seconds
        else:
            order = self.exchange.market_sell(self.cfg.symbol, amount)
            # 部分約定なら約定分だけ記帳する(残りは次周期のシグナルで再売却)
            amount, fill_price, fee_jpy = self.exchange.normalize_fill(
                order, self.cfg.symbol, amount, price
            )
        return self._book(now, "sell", amount, fill_price, fee_jpy, signal.reason,
                          signal_price=price, wait_seconds=wait_s)

    def _book(self, now, side, amount, price, fee_jpy, reason,
              signal_price: float | None = None, wait_seconds: float = 0.0) -> str:
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
        self.shortfall.record(
            now, self.cfg.symbol, side,
            signal_price if signal_price is not None else price,
            price, amount, fee_jpy, wait_seconds,
        )
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
            "起動 mode=%s strategy=%s symbol=%s budget=%s円 execution=%s",
            self.cfg.mode, self.cfg.strategy, self.cfg.symbol, self.cfg.budget_jpy,
            self.cfg.execution.style,
        )
        while True:
            try:
                price = self.exchange.fetch_price(self.cfg.symbol)
                closes = fetch_closes(self.exchange, self.cfg)
                result = self.step(datetime.now(), price, closes)
                log.info("%s | 価格=%s円 | %s", self.cfg.symbol, f"{price:,.0f}", result)
            except Exception:
                log.exception("サイクルでエラー(次の周期で再試行)")
            time.sleep(self.cfg.interval_seconds)
