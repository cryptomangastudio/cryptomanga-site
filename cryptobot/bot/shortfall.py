"""実効コスト台帳(リサーチ#4: Implementation Shortfall)。

シグナル時の理論価格と実約定の差(スリッページ)・手数料・待ち時間を全約定で記録し、
「バックテストと実運用の乖離」を実測できるようにする読み取り専用の計測層。
発注ロジックには一切影響しない。
"""
from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path

HEADER = [
    "日時",
    "銘柄",
    "売買",
    "シグナル価格(JPY)",
    "約定価格(JPY)",
    "数量",
    "スリッページ(JPY)",
    "手数料(JPY)",
    "待ち秒",
    "実効コスト(JPY)",
]


class ShortfallLedger:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        if not self.path.exists():
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("w", newline="", encoding="utf-8") as f:
                csv.writer(f).writerow(HEADER)

    def record(
        self,
        ts: datetime,
        symbol: str,
        side: str,
        signal_price: float,
        fill_price: float,
        amount: float,
        fee_jpy: float,
        wait_seconds: float = 0.0,
    ) -> float:
        """記録して実効コスト(スリッページ+手数料)を返す。

        スリッページは「シグナル価格で約定できた場合との差」。
        買いは高く約定するほど正、売りは安く約定するほど正(=どちらも損)。
        メイカー執行が機能していれば負(有利化+リベート)になりうる。
        """
        if side == "buy":
            slippage = (fill_price - signal_price) * amount
        else:
            slippage = (signal_price - fill_price) * amount
        effective = slippage + fee_jpy
        with self.path.open("a", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow(
                [
                    ts.strftime("%Y-%m-%d %H:%M:%S"),
                    symbol,
                    "買" if side == "buy" else "売",
                    repr(signal_price),
                    repr(fill_price),
                    repr(amount),
                    f"{slippage:.2f}",
                    f"{fee_jpy:.2f}",
                    f"{wait_seconds:.1f}",
                    f"{effective:.2f}",
                ]
            )
        return effective
