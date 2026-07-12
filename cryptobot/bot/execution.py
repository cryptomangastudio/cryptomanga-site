"""執行エンジン(リサーチ#1: Post-Onlyメイカー執行)。

- paper: メイカー/テイカーの手数料率の違いとして近似する(paper_fee_rate)。
  注意: ペーパーは「常に約定する」楽観的な近似(実際のメイカーは未約定リスクあり)。
- live: Post-Only指値を最良気配に置き、未約定なら再指値。上限回数で見送る。
  テイカーへの自動フォールバックはしない(討論の裁定: 取れなかった利益は仮説、
  払った手数料は確定損)。
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from .config import BotConfig
from .exchange import SpotOnlyExchange, normalize_order_fill

log = logging.getLogger("cryptobot.execution")

POLL_SECONDS = 2.0


def paper_fee_rate(cfg: BotConfig) -> float:
    """ペーパー/バックテストで使う手数料率。メイカーなら負(リベート)になりうる。"""
    if cfg.execution.style == "maker":
        return cfg.execution.maker_fee_rate
    return cfg.execution.taker_fee_rate


def conservative_fee_rate(cfg: BotConfig) -> float:
    """残高の手数料バッファ等、安全側に見積もるべき場面の手数料率。

    設定ミス(リベートのない取引所でmaker指定等)でも不足しないよう、
    メイカー/テイカーの高い方(かつ0以上)を使う。
    """
    return max(cfg.execution.maker_fee_rate, cfg.execution.taker_fee_rate, 0.0)


def round_trip_cost_pct(cfg: BotConfig) -> float:
    """往復の実効コスト見積もり(%)。コストゲート(#3)の分母。

    メイカーのリベートはコストを打ち消す方向に効くが、ゲート計算では保守的に
    手数料は0未満に数えない(リベートが逆選択コストで相殺されうるため)。
    """
    fee_pct = max(paper_fee_rate(cfg), 0.0) * 100
    return 2 * (fee_pct + cfg.cost_gate.spread_pct_estimate)


@dataclass
class ExecutionResult:
    amount: float      # 実際に約定した数量(0なら全く約定しなかった)
    price: float       # 平均約定価格
    fee_jpy: float
    wait_seconds: float
    requotes: int


class MakerExecutor:
    """live用のPost-Only指値執行。bitbank等、post_onlyに対応した取引所で使う。

    途中で通信エラーが起きても、それまでの約定分は必ず結果として返す
    (返さないと実際に買えた現物が帳簿から漏れて建玉が狂うため)。
    """

    def __init__(self, exchange: SpotOnlyExchange, cfg: BotConfig):
        self.exchange = exchange
        self.cfg = cfg

    def _best_quote(self, symbol: str, side: str) -> float:
        t = self.exchange.client.fetch_ticker(symbol)
        price = t.get("bid") if side == "buy" else t.get("ask")
        if not price:
            raise RuntimeError(f"{symbol} の気配値が取得できません")
        return float(price)

    def execute(self, symbol: str, side: str, amount: float) -> ExecutionResult:
        """post-only指値 → 待つ → 未約定なら板に追随して再指値、を繰り返す。"""
        client = self.exchange.client
        started = time.time()
        remaining = amount
        filled_total = 0.0
        cost_total = 0.0   # filled × price の合計(平均価格計算用)
        fee_total = 0.0
        requotes = 0
        base = self.exchange.base_currency(symbol)

        def result() -> ExecutionResult:
            avg = cost_total / filled_total if filled_total > 0 else 0.0
            return ExecutionResult(
                amount=filled_total, price=avg, fee_jpy=fee_total,
                wait_seconds=time.time() - started, requotes=requotes,
            )

        while remaining > 0 and requotes <= self.cfg.execution.max_requotes:
            try:
                price = self._best_quote(symbol, side)
                order = self.exchange.limit_post_only(symbol, side, remaining, price)
            except Exception as e:
                # post-only拒否(板を食う価格)や気配取得失敗は即座に次の気配で再試行
                log.info("post-only発注できず(再試行): %s", e)
                requotes += 1
                continue

            order_id = order["id"]
            deadline = time.time() + self.cfg.execution.requote_seconds
            status = order
            try:
                while True:
                    status = client.fetch_order(order_id, symbol)
                    if status.get("status") == "closed" or time.time() >= deadline:
                        break
                    time.sleep(POLL_SECONDS)
                if status.get("status") != "closed":
                    try:
                        client.cancel_order(order_id, symbol)
                    except Exception as e:
                        log.info("キャンセル失敗(直後に約定した可能性): %s", e)
                    status = client.fetch_order(order_id, symbol)
            except Exception as e:
                # 状態不明のまま続けると二重発注の危険。ここまでの約定分を返して終了
                log.warning("注文状態の確認に失敗(ここまでの約定分のみ記帳): %s", e)
                fill, fill_price, fee = normalize_order_fill(status, base, 0.0, price)
                if fill > 0:
                    filled_total += fill
                    cost_total += fill * fill_price
                    fee_total += fee
                return result()

            # 部分約定も含めて、実受渡数量(基軸通貨建て手数料は控除)を取り込む
            gross_fill = float(status.get("filled") or 0.0)
            fill, fill_price, fee = normalize_order_fill(status, base, 0.0, price)
            if fill > 0:
                filled_total += fill
                cost_total += fill * fill_price
                fee_total += fee
            remaining -= gross_fill  # 残量は取引所視点の約定量で減らす(手数料控除前)
            if status.get("status") == "closed":
                break
            requotes += 1

        return result()
