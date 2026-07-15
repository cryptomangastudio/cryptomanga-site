"""config.yaml に売買プリセットを安全に適用する(文字化けしないUTF-8書き込み)。

使い方:
    python apply_preset.py aggressive   # 積極運用(EMA9/21・ゲート緩め・サイズ大)
    python apply_preset.py safe          # 安全寄り(既定に戻す)

手でconfig.yamlを編集するとWindowsのメモ帳/PowerShellが文字コードを壊しやすい
ため、このスクリプト経由での切り替えを推奨。既存の他の設定(notify・銘柄・
パス等)は保持し、売買まわりのキーだけ書き換える。
"""
from __future__ import annotations

import sys
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from bot.config import _read_config_text, load_config, validate  # noqa: E402

CFG = HERE / "config.yaml"

# 各プリセットが上書きするキー(セクション単位でmerge)。ここに無いキーは触らない。
PRESETS: dict[str, dict] = {
    "aggressive": {
        "strategy": "ma_cross",
        "ma_cross": {"timeframe": "1h", "fast": 9, "slow": 21, "ma_type": "ema"},
        "cost_gate": {"k": 1.0},
        "risk": {"cooldown_minutes": 30, "max_order_jpy": 30000, "max_position_jpy": 80000},
        "governor": {"max_buys_per_month": 80},
    },
    "safe": {
        "strategy": "ma_cross",
        "ma_cross": {"timeframe": "1h", "fast": 9, "slow": 26, "ma_type": "sma"},
        "cost_gate": {"k": 2.0},
        "risk": {"cooldown_minutes": 60, "max_order_jpy": 10000, "max_position_jpy": 50000},
        "governor": {"max_buys_per_month": 40},
    },
}


def apply(name: str) -> None:
    if name not in PRESETS:
        raise SystemExit("使い方: python apply_preset.py [aggressive|safe]")
    if not CFG.exists():
        raise SystemExit(f"config.yaml が見つかりません({CFG})。先にセットアップを実行してください。")

    raw = yaml.safe_load(_read_config_text(CFG)) or {}
    for section, vals in PRESETS[name].items():
        if isinstance(vals, dict):
            cur = raw.get(section)
            if not isinstance(cur, dict):
                cur = {}
            cur.update(vals)
            raw[section] = cur
        else:
            raw[section] = vals

    # UTF-8(BOMなし)で書き戻す。日本語コメントは失われるが値は正しく保たれる
    text = yaml.safe_dump(raw, allow_unicode=True, sort_keys=False)
    CFG.write_text(text, encoding="utf-8")

    # 書き込んだ設定が妥当か即検証(予算超過などがあればここで気づける)
    validate(load_config(CFG))

    m = raw["ma_cross"]
    print(f"✅ プリセット『{name}』を適用しました。botを再起動してください。")
    print(f"   戦略      : ma_cross（{m['ma_type'].upper()} {m['fast']}/{m['slow']} @ {m['timeframe']}）")
    print(f"   コストゲートk: {raw['cost_gate']['k']}  クールダウン: {raw['risk']['cooldown_minutes']}分")
    print(f"   1回の買い : {raw['risk']['max_order_jpy']:,}円  保有上限: {raw['risk']['max_position_jpy']:,}円")
    print(f"   月間買い上限: {raw['governor']['max_buys_per_month']}回")


if __name__ == "__main__":
    apply(sys.argv[1] if len(sys.argv) > 1 else "")
