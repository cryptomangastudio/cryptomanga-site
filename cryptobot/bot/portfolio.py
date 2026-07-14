"""複数銘柄の現物ポートフォリオ運用。

予算を銘柄数で等分し、銘柄ごとに独立した BotRunner(帳簿・ペーパー残高・
リスク管理・停止ファイル)を持つ。1銘柄が全停止しても他の銘柄は動き続ける。
"""
from __future__ import annotations

import dataclasses
import logging
import time
from datetime import datetime
from pathlib import Path

from .config import BotConfig
from .runner import BotRunner, fetch_window

log = logging.getLogger("cryptobot.portfolio")


def symbol_slug(symbol: str) -> str:
    return symbol.replace("/", "_")


def sub_config(cfg: BotConfig, symbol: str) -> BotConfig:
    """銘柄ごとの設定を作る。予算・リスク上限は銘柄数で按分する。"""
    n = len(cfg.symbols)
    budget = cfg.budget_jpy // n
    risk = dataclasses.replace(
        cfg.risk,
        max_order_jpy=min(cfg.risk.max_order_jpy, budget),
        max_position_jpy=min(cfg.risk.max_position_jpy, budget),
        max_daily_loss_jpy=max(500, cfg.risk.max_daily_loss_jpy // n),
        max_weekly_loss_jpy=max(1_000, cfg.risk.max_weekly_loss_jpy // n),
    )
    dca = dataclasses.replace(
        cfg.dca, buy_amount_jpy=min(cfg.dca.buy_amount_jpy, risk.max_order_jpy)
    )
    # 暴走防止ガバナーも銘柄数で按分(しないとポートフォリオ全体でn倍緩んでしまう)
    governor = dataclasses.replace(
        cfg.governor, max_buys_per_month=max(2, cfg.governor.max_buys_per_month // n)
    )
    if n == 1:
        paths = {}
    else:
        slug = symbol_slug(symbol)
        paths = {
            "journal_path": str(Path(cfg.journal_path).with_name(f"trades_{slug}.csv")),
            "paper_state_path": (
                str(Path(cfg.paper_state_path).with_name(f"paper_{slug}.json"))
                if cfg.paper_state_path else cfg.paper_state_path
            ),
            "halt_file": (
                str(Path(cfg.halt_file).with_name(f"HALTED_{slug}"))
                if cfg.halt_file else cfg.halt_file
            ),
            "shortfall_path": str(
                Path(cfg.shortfall_path).with_name(f"execution_{slug}.csv")
            ),
            "risk_state_path": (
                str(Path(cfg.risk_state_path).with_name(f"risk_state_{slug}.json"))
                if cfg.risk_state_path else cfg.risk_state_path
            ),
        }
    return dataclasses.replace(
        cfg, symbol=symbol, symbols=[symbol], budget_jpy=budget,
        risk=risk, dca=dca, governor=governor, **paths
    )


class PortfolioRunner:
    def __init__(self, cfg: BotConfig, exchange=None):
        self.cfg = cfg
        self.exchange = exchange
        self.runners: dict[str, BotRunner] = {
            sym: BotRunner(sub_config(cfg, sym), exchange) for sym in cfg.symbols
        }

    def step_symbol(self, symbol: str, now: datetime, price: float, closes: list[float]) -> str:
        return self.runners[symbol].step(now, price, closes)

    def step_all(self, now: datetime) -> dict[str, str]:
        """全銘柄について価格取得→判断を1周する(exchange必須)。"""
        results = {}
        for sym, runner in self.runners.items():
            try:
                price = self.exchange.fetch_price(sym)
                closes, highs, lows = fetch_window(self.exchange, runner.cfg)
                results[sym] = runner.step(now, price, closes, highs, lows)
            except Exception as e:
                results[sym] = f"エラー: {type(e).__name__}: {e}"
                log.warning("%s のサイクル失敗: %s", sym, e)
        return results

    def run(self) -> None:
        assert self.exchange is not None, "run()には価格取得用のexchangeが必要"
        log.info(
            "起動 mode=%s strategy=%s symbols=%s budget=%s円(銘柄ごとに等分)",
            self.cfg.mode, self.cfg.strategy, self.cfg.symbols, self.cfg.budget_jpy,
        )
        while True:
            for sym, result in self.step_all(datetime.now()).items():
                log.info("%s | %s", sym, result)
            time.sleep(min(r.next_sleep_seconds() for r in self.runners.values()))
