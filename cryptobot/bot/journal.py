"""取引記帳。移動平均法で取得単価と実現損益を計算し、CSVに追記する。

このCSVは確定申告の基礎資料になる(暗号資産の所得計算は移動平均法/総平均法)。
注意: 利益を出して売却した時点で、日本円に出金していなくても課税対象。

既存のCSVがある場合は起動時に全行をリプレイして建玉・取得単価・累計損益を
復元する(bot再起動で帳簿が狂わないようにするため)。このため数量・約定価格・
手数料の3列はreprによる完全精度で書く(丸めるとリプレイ結果が実際の建玉と
ズレて、全量売却が「保有不足」になったり起動時リプレイが失敗したりする)。
派生列(約定金額・取得単価・損益)は表示用なので丸めてよい。
"""
from __future__ import annotations

import csv
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

HEADER = [
    "日時",
    "取引所",
    "銘柄",
    "売買",
    "数量",
    "約定価格(JPY)",
    "約定金額(JPY)",
    "手数料(JPY)",
    "取得単価_移動平均(JPY)",
    "実現損益(JPY)",
    "累計実現損益(JPY)",
    "メモ",
]
# 列番号(report.py等の読み手と共有する)
COL_TS, COL_SYMBOL, COL_SIDE, COL_AMOUNT, COL_PRICE, COL_JPY, COL_FEE, COL_REALIZED = (
    0, 2, 3, 4, 5, 6, 7, 9,
)

EPS = 1e-12  # 建玉数量の実質ゼロ判定(paper/runnerとも共有)


@dataclass
class Fill:
    ts: datetime
    exchange: str
    symbol: str
    side: str  # buy | sell
    amount: float
    price: float
    fee_jpy: float
    memo: str = ""


class TradeJournal:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.position_amount = 0.0
        self.avg_cost = 0.0  # 移動平均法による取得単価
        self.total_realized_pnl = 0.0
        # 売却成績の統計(ケリー基準の推定に使う。リプレイで自動復元される)
        self.sell_count = 0
        self.win_count = 0
        self.total_win_jpy = 0.0
        self.total_loss_jpy = 0.0
        # 直近30回の売却リターン率(古い成績が永久に推定を支配しないための窓)
        self.recent_returns: deque[float] = deque(maxlen=30)
        if self.path.exists():
            self._replay_existing()
        else:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("w", newline="", encoding="utf-8") as f:
                csv.writer(f).writerow(HEADER)

    @property
    def position_cost_jpy(self) -> float:
        return self.position_amount * self.avg_cost

    def record(self, fill: Fill) -> float:
        """約定を記帳し、実現損益(JPY)を返す(買いは常に0)。"""
        realized = self._apply(fill.side, fill.amount, fill.price, fill.fee_jpy)
        with self.path.open("a", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow(
                [
                    fill.ts.strftime("%Y-%m-%d %H:%M:%S"),
                    fill.exchange,
                    fill.symbol,
                    "買" if fill.side == "buy" else "売",
                    repr(fill.amount),
                    repr(fill.price),
                    f"{fill.amount * fill.price:.2f}",
                    repr(fill.fee_jpy),
                    f"{self.avg_cost:.2f}",
                    f"{realized:.2f}",
                    f"{self.total_realized_pnl:.2f}",
                    fill.memo,
                ]
            )
        return realized

    def _apply(self, side: str, amount: float, price: float, fee_jpy: float) -> float:
        """帳簿の内部状態を更新し、実現損益を返す(CSVには書かない)。"""
        if side == "buy":
            # 手数料は取得原価に含める(移動平均法)
            new_cost = self.position_cost_jpy + amount * price + fee_jpy
            self.position_amount += amount
            self.avg_cost = new_cost / self.position_amount if self.position_amount else 0.0
            return 0.0
        if side == "sell":
            if amount > self.position_amount + EPS:
                raise ValueError(f"保有量{self.position_amount}を超える売却: {amount}")
            realized = (price - self.avg_cost) * amount - fee_jpy
            cost_basis = self.avg_cost * amount
            if cost_basis > 0:
                self.recent_returns.append(realized / cost_basis)
            self.position_amount -= amount
            if self.position_amount <= EPS:
                self.position_amount = 0.0
                self.avg_cost = 0.0
            self.total_realized_pnl += realized
            self.sell_count += 1
            if realized > 0:
                self.win_count += 1
                self.total_win_jpy += realized
            else:
                self.total_loss_jpy += -realized
            return realized
        raise ValueError(f"不正なside: {side}")

    def kelly_fraction(self) -> float | None:
        """直近の売却リターン率からケリー比率 f = W - (1-W)/R を推定する。

        - 直近30回のローリング窓(古い成績が推定を永久支配しない)
        - リターン率ベース(賭け金の大小に汚染されない)
        - 全勝はNone(判定保留)を返す: サンプル不足の全勝を「無制限」と
          解釈してはいけない
        負の値は「統計上、期待値が負」を意味し、呼び出し側は新規エントリーを止める。
        """
        n = len(self.recent_returns)
        if n == 0:
            return None
        wins = [r for r in self.recent_returns if r > 0]
        losses = [r for r in self.recent_returns if r <= 0]
        if not losses:
            return None  # 全勝は判定保留(まぐれの可能性を無制限と混同しない)
        if not wins:
            return -1.0
        avg_win = sum(wins) / len(wins)
        avg_loss = abs(sum(losses) / len(losses))
        if avg_loss <= 0:
            return None
        w = len(wins) / n
        r = avg_win / avg_loss
        return w - (1 - w) / r

    def _replay_existing(self) -> None:
        """既存CSVの全行をリプレイして建玉・取得単価・累計損益を復元する。"""
        with self.path.open(newline="", encoding="utf-8") as f:
            reader = csv.reader(f)
            header = next(reader, None)
            if header != HEADER:
                raise ValueError(
                    f"{self.path} のヘッダーが想定と異なります。"
                    "手動編集した場合は列構成を元に戻してください"
                )
            for line_no, row in enumerate(reader, start=2):
                if not row:
                    continue
                try:
                    side = {"買": "buy", "売": "sell"}[row[COL_SIDE]]
                    self._apply(
                        side, float(row[COL_AMOUNT]), float(row[COL_PRICE]), float(row[COL_FEE])
                    )
                except (KeyError, ValueError, IndexError) as e:
                    raise ValueError(f"{self.path}:{line_no}行目が不正です: {e}") from e
