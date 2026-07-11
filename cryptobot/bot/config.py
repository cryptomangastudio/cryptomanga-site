"""設定の読み込みと検証。

危険な設定(予算超過・現物以外など)はここで弾く。
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .notify import VALID_FORMATS as VALID_NOTIFY_FORMATS

VALID_MODES = ("paper", "live")
VALID_STRATEGIES = ("dca", "ma_cross")


@dataclass
class RiskConfig:
    max_order_jpy: int = 10_000
    max_position_jpy: int = 50_000
    max_daily_loss_jpy: int = 3_000
    max_drawdown_pct: float = 15.0
    cooldown_minutes: int = 60


@dataclass
class DCAConfig:
    buy_amount_jpy: int = 3_000


@dataclass
class MACrossConfig:
    timeframe: str = "1h"
    fast: int = 9
    slow: int = 26


@dataclass
class NotifyConfig:
    # 通知先URLは環境変数 CRYPTOBOT_WEBHOOK_URL で渡す(設定ファイルに書かない)
    format: str = "none"  # none | discord | slack


@dataclass
class BotConfig:
    exchange: str = "bitflyer"
    symbol: str = "BTC/JPY"
    mode: str = "paper"
    strategy: str = "dca"
    interval_seconds: int = 3600
    budget_jpy: int = 100_000
    fee_rate: float = 0.0015
    risk: RiskConfig = field(default_factory=RiskConfig)
    dca: DCAConfig = field(default_factory=DCAConfig)
    ma_cross: MACrossConfig = field(default_factory=MACrossConfig)
    notify: NotifyConfig = field(default_factory=NotifyConfig)
    journal_path: str = "data/trades.csv"
    paper_state_path: str = "data/paper_state.json"
    halt_file: str = "data/HALTED"


class ConfigError(ValueError):
    pass


def load_config(path: str | Path) -> BotConfig:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    cfg = BotConfig(
        exchange=raw.get("exchange", "bitflyer"),
        symbol=raw.get("symbol", "BTC/JPY"),
        mode=raw.get("mode", "paper"),
        strategy=raw.get("strategy", "dca"),
        interval_seconds=int(raw.get("interval_seconds", 3600)),
        budget_jpy=int(raw.get("budget_jpy", 100_000)),
        fee_rate=float(raw.get("fee_rate", 0.0015)),
        risk=RiskConfig(**raw.get("risk", {})),
        dca=DCAConfig(**raw.get("dca", {})),
        ma_cross=MACrossConfig(**raw.get("ma_cross", {})),
        notify=NotifyConfig(**raw.get("notify", {})),
        journal_path=raw.get("journal_path", "data/trades.csv"),
        paper_state_path=raw.get("paper_state_path", "data/paper_state.json"),
        halt_file=raw.get("halt_file", "data/HALTED"),
    )
    validate(cfg)
    return cfg


def validate(cfg: BotConfig) -> None:
    if cfg.mode not in VALID_MODES:
        raise ConfigError(f"mode は {VALID_MODES} のいずれか: {cfg.mode!r}")
    if cfg.strategy not in VALID_STRATEGIES:
        raise ConfigError(f"strategy は {VALID_STRATEGIES} のいずれか: {cfg.strategy!r}")
    if cfg.budget_jpy <= 0:
        raise ConfigError("budget_jpy は正の値にしてください")
    if cfg.budget_jpy > 100_000:
        raise ConfigError(
            "budget_jpy が10万円を超えています。運用計画(元手10万円まで)を"
            "見直した上で、この上限チェックを意図的に変更してください"
        )
    r = cfg.risk
    if r.max_order_jpy > cfg.budget_jpy:
        raise ConfigError("max_order_jpy が総予算を超えています")
    if r.max_position_jpy > cfg.budget_jpy:
        raise ConfigError("max_position_jpy が総予算を超えています")
    if not (0 < r.max_drawdown_pct <= 100):
        raise ConfigError("max_drawdown_pct は 0〜100 の範囲")
    if cfg.ma_cross.fast >= cfg.ma_cross.slow:
        raise ConfigError("ma_cross.fast は slow より小さくしてください")
    if cfg.notify.format not in VALID_NOTIFY_FORMATS:
        raise ConfigError(f"notify.format は {VALID_NOTIFY_FORMATS} のいずれか")
    if cfg.mode == "live" and os.environ.get("CRYPTOBOT_LIVE") != "YES":
        raise ConfigError(
            "mode: live には環境変数 CRYPTOBOT_LIVE=YES が必要です(誤発注防止の二重ロック)"
        )
