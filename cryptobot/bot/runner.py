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
from .execution import (
    ExecutionResult,
    MakerExecutor,
    conservative_fee_rate,
    paper_fee_rate,
    round_trip_cost_pct,
)
from .journal import EPS, Fill, TradeJournal
from .notify import Notifier
from .paper import PaperBroker
from .risk import RiskManager
from .shortfall import ShortfallLedger
from .strategy import Action, MarketSnapshot, Signal, build_strategy, sma

log = logging.getLogger("cryptobot")

MIN_BUY_JPY = 1_000  # これ未満に切り詰められた買いはスキップ(手数料負け防止)


def fetch_window(exchange, cfg: BotConfig) -> tuple[list[float], list[float], list[float]]:
    """戦略が必要とする (終値, 高値, 安値) 履歴を取得する。

    DCAは価格履歴を使わないので取得しない(bitFlyer等、OHLCV API非対応の
    取引所でもDCA運用できるようにするため)。ma_crossはOHLCV対応の取引所
    (例: bitbank)が必要。
    """
    if cfg.strategy != "ma_cross":
        return [], [], []
    rows = exchange.fetch_ohlcv(cfg.symbol, cfg.ma_cross.timeframe, limit=closes_needed(cfg))
    return [r[4] for r in rows], [r[2] for r in rows], [r[3] for r in rows]


def fetch_closes(exchange, cfg: BotConfig) -> list[float]:
    return fetch_window(exchange, cfg)[0]


def closes_needed(cfg: BotConfig) -> int:
    """戦略+ATR(15本)が必要とする終値の本数。バックテストと実運転で共有する。"""
    return max(cfg.ma_cross.slow + 5, 16)


def atr_estimate(
    closes: list[float],
    highs: list[float] | None = None,
    lows: list[float] | None = None,
    period: int = 14,
) -> float | None:
    """ATR(1バーあたりの平均値動き)。データ不足ならNone。

    高値・安値があればWilderのTrue Range(ヒゲ込みの実勢レンジ)を使う。
    終値差だけの近似はヒゲを捨てて実勢の半分程度に過小評価しがちで、
    サイジング(許容損失÷値動き)が過大になるため、H/Lが取れる限りTRを使う。
    """
    if len(closes) < period + 1:
        return None
    if highs and lows and len(highs) == len(closes) and len(lows) == len(closes):
        trs = [
            max(
                highs[i] - lows[i],
                abs(highs[i] - closes[i - 1]),
                abs(lows[i] - closes[i - 1]),
            )
            for i in range(1, len(closes))
        ]
    else:
        trs = [abs(closes[i] - closes[i - 1]) for i in range(1, len(closes))]
    window = trs[-period:]
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
            state_file=cfg.risk_state_path or None,
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
        self._pending_price: float | None = None  # 未確認の急変価格(次周期で確認)
        # 売りが未約定のまま残っている場合の元シグナル価格(None=残りなし)。
        # ここからexit_taker_fallback_pct%下がったらテイカー成行で確定させる
        self._pending_exit_price: float | None = None
        self._kelly_block_notified = False
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
            closes = self.exchange.fetch_daily_closes(self.cfg.symbol, self.cfg.regime.ma_days)
            if len(closes) >= self.cfg.regime.ma_days:
                ma = sma(closes, self.cfg.regime.ma_days)
            elif not self._regime_warned:
                log.warning(
                    "%s: 日足が%d本しか取れずレジームフィルターは無効(%d本必要)",
                    self.cfg.symbol, len(closes), self.cfg.regime.ma_days,
                )
                self._regime_warned = True
        except Exception as e:
            if not self._regime_warned:
                log.warning(
                    "%s: 日足が取得できないためレジームフィルターは無効(%s)",
                    self.cfg.symbol, e,
                )
                self._regime_warned = True
        self._ma_cache = (key, ma)
        return ma

    def step(
        self,
        now: datetime,
        price: float,
        closes: list[float],
        highs: list[float] | None = None,
        lows: list[float] | None = None,
    ) -> str:
        """1サイクル。何をしたかの説明文字列を返す。"""
        # 価格サニティチェック(#2): 単発の異常値は無視し、2周期連続で同水準なら
        # 実際の急変と判断して処理を続行する(単純スキップだと暴落が続く間
        # DD停止も売りも一切発火しない穴になる)
        if self._last_sane_price is not None and self.cfg.price_sanity_pct > 0:
            jump_pct = abs(price / self._last_sane_price - 1) * 100
            if jump_pct > self.cfg.price_sanity_pct:
                pending = self._pending_price
                confirmed = (
                    pending is not None
                    and abs(price / pending - 1) * 100 <= 2 * self.cfg.price_sanity_pct
                )
                if not confirmed:
                    self._pending_price = price
                    return (
                        f"スキップ: 価格が前回比{jump_pct:.1f}%乖離(異常値の可能性。"
                        f"次の周期も同水準なら実際の急変として処理します)"
                    )
        self._pending_price = None
        self._last_sane_price = price

        market = MarketSnapshot(
            price=price,
            closes=closes,
            position_amount=self.journal.position_amount,
            position_cost_jpy=self.journal.position_cost_jpy,
            highs=highs,
            lows=lows,
        )
        self.risk.update_equity(self._equity_jpy(price))

        signal = self.strategy.decide(market)

        # ハードストップ: 取得単価比-X%で戦略に関わらず強制全量売却
        # (MAクロスの出口はSMAの遅行で数%遅れる。1トレードの実損を有界にする最後の出口)
        hs = self.cfg.risk.hard_stop_pct
        if (
            self.cfg.strategy == "ma_cross"
            and hs > 0
            and market.position_amount > 0
            and self.journal.avg_cost > 0
            and price <= self.journal.avg_cost * (1 - hs / 100)
        ):
            signal = Signal(Action.SELL, 0.0, f"ハードストップ(取得単価比-{hs:.0f}%)")

        # 前周期の売りが未約定のまま残っていれば、シグナルに関わらず再試行する
        if self._pending_exit_price is not None:
            if market.position_amount <= 0:
                self._pending_exit_price = None
            elif signal.action != Action.SELL:
                signal = Signal(Action.SELL, 0.0, "前回の売り残りを再試行")

        # 売り(リスク削減)はhalt中でも実行する。買いだけがhaltの対象
        if signal.action == Action.SELL:
            return self._try_sell(now, price, market, signal)
        if self.risk.halted:
            return f"停止中(買い禁止・売りは可能): {self.risk.halt_reason}"
        if signal.action == Action.HOLD:
            return f"HOLD: {signal.reason}"
        return self._try_buy(now, price, market, signal)

    def _apply_buy_gates(self, now, price, market, signal) -> tuple[float, str] | str:
        """レジーム(#8)・コストゲート(#3)・サイジング(#5)。(買付額, 補足) か却下文字列を返す。"""
        buy_jpy = signal.jpy_amount
        notes = []
        ma = self._regime_ma(now)

        if self.cfg.strategy == "dca" and ma is not None and self.cfg.regime.dca_hard_floor_pct > 0:
            # DCAハードフロア: レジーム崩壊級の下落(MA比-25%等)では「安いから多く買う」
            # をやめて積立自体を停止する(浅い押し目のナンピンと構造的ベアを区別する)
            floor = ma * (1 - self.cfg.regime.dca_hard_floor_pct / 100)
            if price < floor:
                return (
                    f"BUYスキップ: DCAハードフロア(価格が{self.cfg.regime.ma_days}日MA比"
                    f"-{self.cfg.regime.dca_hard_floor_pct:.0f}%の{floor:,.0f}円を下回った)"
                )

        if self.cfg.strategy == "ma_cross":
            if ma is not None and price < ma:
                return f"BUYスキップ: レジームフィルター(価格が{self.cfg.regime.ma_days}日MA {ma:,.0f}円 未満)"
            atr = atr_estimate(market.closes, market.highs, market.lows)
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
            samples = len(self.journal.recent_returns)
            if kelly is not None and samples >= self.cfg.sizing.kelly_min_trades:
                if kelly <= 0:
                    if not self._kelly_block_notified:
                        # サイレント永久停止にしない: 人間に判断材料を届ける
                        self.notifier.send(
                            f"⚠️ {self.cfg.symbol}: 直近{samples}回の売却実績でケリー推定が負"
                            f"({kelly:.2f})のため新規買いを停止中。戦略・設定の見直しを検討してください"
                        )
                        self._kelly_block_notified = True
                    return (
                        f"BUYスキップ: ケリー推定が負({kelly:.2f}、直近{samples}回の"
                        "売却実績で期待値マイナス)。戦略を見直すまで新規買い停止"
                    )
                self._kelly_block_notified = False
                cap = kelly * self.cfg.sizing.kelly_cap * equity
                if cap < buy_jpy:
                    buy_jpy = cap
                    notes.append(f"ケリー上限→{buy_jpy:,.0f}円")
        elif self.cfg.strategy == "dca" and ma is not None and self.cfg.regime.dca_tilt > 0:
            # DCA傾斜(#8): 200日MAより安いほど多く、高いほど少なく買う。
            # 増額後もリスク上限は超えない(超過して全却下されたら傾斜の意味が真逆になる)
            deviation = (price - ma) / ma
            tilt = self.cfg.regime.dca_tilt
            factor = max(1 - tilt, min(1 + tilt, 1 - deviation))
            buy_jpy = min(buy_jpy * factor, self.cfg.risk.max_order_jpy)
            notes.append(f"MA乖離{deviation:+.1%}→積立×{factor:.2f}")

        return buy_jpy, ("(" + " / ".join(notes) + ")" if notes else "")

    def _try_buy(self, now, price, market, signal) -> str:
        gated = self._apply_buy_gates(now, price, market, signal)
        if isinstance(gated, str):
            return gated
        buy_jpy, note = gated

        buy_jpy = min(buy_jpy, self._available_jpy())
        if not self.paper:
            # live: 注文額 + 手数料 が残高に収まるよう、高い方の手数料率ぶんの余裕を取る
            # (設定ミスでリベートのない取引所にmaker指定をしても不足しないように)
            buy_jpy /= 1 + conservative_fee_rate(self.cfg)
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

        result = self._execute_order("buy", amount_estimate, price)
        if result.amount <= 0:
            return (
                f"BUY見送り: メイカー指値が{result.requotes}回の再指値でも約定せず"
                "(テイカーには逃げない方針)"
            )
        return self._book(now, "buy", result.amount, result.price, result.fee_jpy,
                          signal.reason + note,
                          signal_price=price, wait_seconds=result.wait_seconds)

    def _try_sell(self, now, price, market, signal) -> str:
        amount = market.position_amount  # 現状は全量売却のみ(現物なので保有分だけ)
        if amount <= 0:
            return "SELLスキップ: 保有なし"

        # live: 帳簿と実残高の突合(オーナーの手動売買・記帳漏れで乖離した場合、
        # 幻の建玉を永久に売り続けようとするデッドロックを防ぐ)
        if not self.paper and self.exchange is not None:
            try:
                actual = self.exchange.fetch_base_balance(self.cfg.symbol)
            except Exception as e:
                log.warning("実残高の取得失敗(帳簿の建玉で続行): %s", e)
            else:
                if actual < amount - EPS:
                    self.notifier.send(
                        f"⚠️ {self.cfg.symbol}: 帳簿の建玉({amount:.8f})より実残高({actual:.8f})が"
                        "少ないため、実残高分のみ売却します。取引所で手動売買しませんでしたか?"
                        "(手動介入する場合は必ずbotを止めてから)"
                    )
                    amount = actual
                if amount <= EPS:
                    self._pending_exit_price = None
                    return "SELLスキップ: 実残高なし(外部で売却済みの可能性。帳簿の確認を)"
        decision = self.risk.check_order("sell", amount * price, market.position_cost_jpy, now)
        if not decision.approved:
            return f"SELL却下: {decision.reason}"

        # 出口エスカレーション: 未約定のままシグナル価格から一定%滑ったら
        # テイカー成行で確定する(出口の見送りは実損の拡大なので入口と非対称に扱う)
        esc_pct = self.cfg.execution.exit_taker_fallback_pct
        force_taker = (
            esc_pct > 0
            and self._pending_exit_price is not None
            and price <= self._pending_exit_price * (1 - esc_pct / 100)
        )
        result = self._execute_order("sell", amount, price, force_taker=force_taker)
        if result.amount <= 0:
            if self._pending_exit_price is None:
                self._pending_exit_price = price  # 元シグナル価格として記憶
            return (
                f"SELL見送り: メイカー指値が{result.requotes}回の再指値でも約定せず"
                f"(次周期に再試行。シグナル比-{esc_pct:.0f}%までにはテイカーで確定します)"
            )
        if result.amount < amount - EPS:
            if self._pending_exit_price is None:
                self._pending_exit_price = price  # 部分約定: 残りを次周期に再試行
        else:
            self._pending_exit_price = None
        note = "(テイカーで確定)" if force_taker else ""
        return self._book(now, "sell", result.amount, result.price, result.fee_jpy,
                          signal.reason + note, signal_price=price,
                          wait_seconds=result.wait_seconds)

    def _execute_order(self, side: str, amount: float, price: float, force_taker: bool = False):
        """paper / live-maker / live-taker の執行を1か所に集約する。

        戻り値は ExecutionResult(数量・平均価格・手数料JPY・待ち秒・再指値回数)。
        force_taker は出口エスカレーション用(メイカーを飛ばして成行で確定)。
        """
        if self.paper:
            if side == "buy":
                filled, fee_jpy = self.paper.market_buy(price, amount * price)
            else:
                _, fee_jpy = self.paper.market_sell(price, amount)
                filled = amount
            return ExecutionResult(filled, price, fee_jpy, 0.0, 0)
        if self.maker and not force_taker:
            return self.maker.execute(self.cfg.symbol, side, amount)
        if side == "buy":
            order = self.exchange.market_buy(self.cfg.symbol, amount)
        else:
            order = self.exchange.market_sell(self.cfg.symbol, amount)
        filled, fill_price, fee_jpy = self.exchange.normalize_fill(
            order, self.cfg.symbol, amount, price
        )
        return ExecutionResult(filled, fill_price, fee_jpy, 0.0, 0)

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

    def next_sleep_seconds(self) -> int:
        """売り残りがある間は1時間待たず短周期で再試行する(出口の滑り拡大防止)。"""
        if self._pending_exit_price is not None:
            return min(self.cfg.interval_seconds, 60)
        return self.cfg.interval_seconds

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
                closes, highs, lows = fetch_window(self.exchange, self.cfg)
                result = self.step(datetime.now(), price, closes, highs, lows)
                log.info("%s | 価格=%s円 | %s", self.cfg.symbol, f"{price:,.0f}", result)
            except Exception:
                log.exception("サイクルでエラー(次の周期で再試行)")
            time.sleep(self.next_sleep_seconds())
