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
    max_weekly_loss_jpy: int = 7_000     # 週次損失上限(多層ブレーカー第2層)
    max_consecutive_losses: int = 5      # 連敗数上限(第3層)。到達で買い停止
    max_drawdown_pct: float = 15.0
    hard_stop_pct: float = 10.0          # 取得単価比-この%で強制全量売却(ma_cross系。0で無効)
    cooldown_minutes: int = 60


@dataclass
class DCAConfig:
    buy_amount_jpy: int = 3_000


@dataclass
class MACrossConfig:
    timeframe: str = "1h"
    fast: int = 9
    slow: int = 26
    ma_type: str = "sma"  # sma | ema。emaは直近の値を重く見るため反応が速い(シグナルが増える)


@dataclass
class NotifyConfig:
    # 通知先URLは環境変数 CRYPTOBOT_WEBHOOK_URL で渡す(設定ファイルに書かない)
    format: str = "none"  # none | discord | slack


@dataclass
class ExecutionConfig:
    """執行方式。リサーチ結論#1: メイカー執行がコスト構造を反転させる最重要機能。"""
    style: str = "maker"          # maker | taker。ペーパーでは手数料率の違いとして近似
    maker_fee_rate: float = -0.0002   # bitbankは-0.02%(受け取り)。取引所に合わせて変更
    taker_fee_rate: float = 0.0012
    requote_seconds: int = 20     # live: 指値が約定しない場合の再指値までの秒数
    max_requotes: int = 5         # live: 再指値の上限。超えたら見送り(買いはテイカーに逃げない)
    # 出口だけは非対称: 売りが約定しないままシグナル価格からこの%下がったら
    # テイカー成行で確定させる(入口の見送りは仮説の損、出口の見送りは実損の拡大)
    exit_taker_fallback_pct: float = 2.0  # 0で無効


@dataclass
class CostGateConfig:
    """発注前コストゲート(#3)。期待利幅がコストのk倍未満の売買を機械的に見送る。"""
    enabled: bool = True
    k: float = 2.0                    # 期待利幅 >= 往復コスト×k を要求
    spread_pct_estimate: float = 0.05  # スプレッド+滑りの見積もり(%)


@dataclass
class GovernorConfig:
    """売買頻度ガバナー(#3)。バグによるシグナル乱発の最終防波堤も兼ねる。"""
    max_buys_per_month: int = 40  # DCA(1時間毎×上限まで)も通る程度に。ma_crossはコストゲートが主役


@dataclass
class SizingConfig:
    """ATR連動サイジング+クォーターケリー上限(#5)。ma_cross系のエントリーに適用。"""
    risk_pct: float = 1.0     # 1回のトレードで許容する損失 = 資金の1%
    atr_mult: float = 2.0     # 想定損切り幅 = ATR×この倍率
    kelly_cap: float = 0.25   # 推定ケリー値のこの割合を上限に
    kelly_min_trades: int = 10  # ケリー推定に必要な最低売却回数(未満なら判定しない)


@dataclass
class RegimeConfig:
    """200日MAレジームフィルター+DCA傾斜(#8)。日足OHLCV対応の取引所でのみ有効。"""
    enabled: bool = True
    ma_days: int = 200
    # DCA額の傾斜(±この割合)。「MA乖離は回帰する」という未検証の仮説に賭けるレバーの
    # ため既定は0(無効)。有効化する場合はバックテストでA/B検証してから
    dca_tilt: float = 0.0
    dca_hard_floor_pct: float = 25.0  # 価格がMA比-この%を割ったらDCAの新規買い自体を停止(0で無効)


@dataclass
class BotConfig:
    exchange: str = "bitbank"  # メイカーリベート・日足API・最低数量の点でbitbankを既定に
    symbol: str = "BTC/JPY"
    symbols: list[str] = field(default_factory=list)  # 複数銘柄。空ならsymbolのみ
    mode: str = "paper"
    strategy: str = "dca"
    interval_seconds: int = 3600
    budget_jpy: int = 100_000
    risk: RiskConfig = field(default_factory=RiskConfig)
    dca: DCAConfig = field(default_factory=DCAConfig)
    ma_cross: MACrossConfig = field(default_factory=MACrossConfig)
    notify: NotifyConfig = field(default_factory=NotifyConfig)
    execution: ExecutionConfig = field(default_factory=ExecutionConfig)
    cost_gate: CostGateConfig = field(default_factory=CostGateConfig)
    governor: GovernorConfig = field(default_factory=GovernorConfig)
    sizing: SizingConfig = field(default_factory=SizingConfig)
    regime: RegimeConfig = field(default_factory=RegimeConfig)
    tax_rate_pct: float = 20.0  # 税引後表示の概算税率(実際は総合課税で人による)
    price_sanity_pct: float = 10.0  # 前回価格からこの%超乖離した価格での判断を拒否
    journal_path: str = "data/trades.csv"
    paper_state_path: str = "data/paper_state.json"
    halt_file: str = "data/HALTED"
    shortfall_path: str = "data/execution_log.csv"
    risk_state_path: str = "data/risk_state.json"

    def __post_init__(self):
        if not self.symbols:
            self.symbols = [self.symbol]
        self.symbol = self.symbols[0]
        if self.mode == "live":
            # paperの記録(仮想売買)がliveの帳簿・リスク状態・税務集計に混ざらないよう、
            # 既定パスのままの場合はliveサフィックス付きに自動で分離する
            defaults = {
                "journal_path": ("data/trades.csv", "data/trades_live.csv"),
                "shortfall_path": ("data/execution_log.csv", "data/execution_log_live.csv"),
                "halt_file": ("data/HALTED", "data/HALTED_live"),
                "risk_state_path": ("data/risk_state.json", "data/risk_state_live.json"),
            }
            for field_name, (paper_default, live_default) in defaults.items():
                if getattr(self, field_name) == paper_default:
                    setattr(self, field_name, live_default)


class ConfigError(ValueError):
    pass


def _read_config_text(path: str | Path) -> str:
    """config.yaml を文字コードに寛容に読む。

    Windows PowerShell の Set-Content は日本語Windowsの既定コードページ(cp932/
    Shift-JIS)でファイルを書き出すことがあり、日本語コメントを含む config.yaml が
    UTF-8として壊れて読めなくなる。utf-8(BOM付きも許容)→ cp932 の順に試す。
    """
    data = Path(path).read_bytes()
    for enc in ("utf-8-sig", "cp932"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    # 最後はUTF-8で読み、壊れた文字は失敗させずに落とす(値のASCIIキーは生き残る)
    return data.decode("utf-8", errors="replace")


def load_config(path: str | Path) -> BotConfig:
    raw = yaml.safe_load(_read_config_text(path)) or {}
    if "fee_rate" in raw:
        raise ConfigError(
            "fee_rate は廃止されました。execution: の maker_fee_rate / taker_fee_rate に"
            "移行してください(黙って無視すると手数料の前提が変わり危険なためエラーにしています)"
        )
    cfg = BotConfig(
        exchange=raw.get("exchange", "bitbank"),
        symbol=raw.get("symbol", "BTC/JPY"),
        symbols=list(raw.get("symbols") or []),
        mode=raw.get("mode", "paper"),
        strategy=raw.get("strategy", "dca"),
        interval_seconds=int(raw.get("interval_seconds", 3600)),
        budget_jpy=int(raw.get("budget_jpy", 100_000)),
        risk=RiskConfig(**raw.get("risk", {})),
        dca=DCAConfig(**raw.get("dca", {})),
        ma_cross=MACrossConfig(**raw.get("ma_cross", {})),
        notify=NotifyConfig(**raw.get("notify", {})),
        execution=ExecutionConfig(**raw.get("execution", {})),
        cost_gate=CostGateConfig(**raw.get("cost_gate", {})),
        governor=GovernorConfig(**raw.get("governor", {})),
        sizing=SizingConfig(**raw.get("sizing", {})),
        regime=RegimeConfig(**raw.get("regime", {})),
        tax_rate_pct=float(raw.get("tax_rate_pct", 20.0)),
        price_sanity_pct=float(raw.get("price_sanity_pct", 10.0)),
        journal_path=raw.get("journal_path", "data/trades.csv"),
        paper_state_path=raw.get("paper_state_path", "data/paper_state.json"),
        halt_file=raw.get("halt_file", "data/HALTED"),
        shortfall_path=raw.get("shortfall_path", "data/execution_log.csv"),
        risk_state_path=raw.get("risk_state_path", "data/risk_state.json"),
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
    if cfg.ma_cross.ma_type not in ("sma", "ema"):
        raise ConfigError("ma_cross.ma_type は sma か ema")
    if cfg.notify.format not in VALID_NOTIFY_FORMATS:
        raise ConfigError(f"notify.format は {VALID_NOTIFY_FORMATS} のいずれか")
    if len(cfg.symbols) != len(set(cfg.symbols)):
        raise ConfigError("symbols に重複があります")
    if cfg.budget_jpy // len(cfg.symbols) < 5_000:
        raise ConfigError(
            f"銘柄数{len(cfg.symbols)}に対して予算が少なすぎます"
            "(1銘柄あたり5,000円以上になるよう銘柄を減らすか予算を見直してください)"
        )
    if cfg.execution.style not in ("maker", "taker"):
        raise ConfigError("execution.style は maker | taker のいずれか")
    if cfg.execution.requote_seconds < 2:
        raise ConfigError("execution.requote_seconds は2秒以上(注文状態のポーリング間隔より短くできない)")
    if not (0 < cfg.sizing.kelly_cap <= 1):
        raise ConfigError("sizing.kelly_cap は 0〜1 の範囲")
    if cfg.cost_gate.k < 1:
        raise ConfigError("cost_gate.k は 1 以上(期待利幅がコスト未満の売買は確実な損失)")
    if cfg.mode == "live" and os.environ.get("CRYPTOBOT_LIVE") != "YES":
        raise ConfigError(
            "mode: live には環境変数 CRYPTOBOT_LIVE=YES が必要です(誤発注防止の二重ロック)"
        )
