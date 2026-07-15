"""ブラウザで見られる管理画面(ペーパートレード専用)。

使い方:
    python dashboard.py            # 起動してブラウザが自動で開く(http://localhost:8765)
    python dashboard.py --port 9000

安全設計:
- ペーパートレード(mode: paper)専用。liveモードでは起動を拒否する
- 自分のPCの中だけで動く(127.0.0.1にのみバインド。外部公開されない)
- 画面は読み取り+「今すぐ1回判断」ボタンのみ。設定変更はconfig.yamlで行う

構成:
- 背景スレッド①: interval_secondsごとに全銘柄の売買判断(bot本体)
- 背景スレッド②: 5秒ごとに価格、10秒ごとに市場約定フィードを取得
- フロントエンドは /api/state を3秒ごとにポーリングしてぬるぬる更新する
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import secrets
import threading
import time
import webbrowser
from urllib.parse import parse_qs, urlparse
from collections import deque
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from bot.config import load_config
from bot.exchange import SpotOnlyExchange
from bot.journal import COL_AMOUNT, COL_PRICE, COL_REALIZED, COL_SIDE, COL_TS, HEADER
from bot.lock import acquire_singleton_lock
from bot.notify import Notifier
from bot.portfolio import PortfolioRunner
from bot.runner import closes_needed, fetch_window
from bot.strategy import sma


def status_summary(state: dict) -> str:
    """スマホ通知用の定期レポート本文(Discord/Slackにそのまま送れるテキスト)。"""
    lines = [
        f"📊 CryptoBot定期レポート({state['lastRunAt']})",
        f"資産評価額: {state['equity']:,.0f}円" if state["equity"] is not None
        else "資産評価額: 計測中",
        f"現金: {state['cash']:,.0f}円 / 累計実現損益: {state['realizedPnl']:+,.0f}円",
    ]
    for p in state["perSymbol"]:
        price = f"{p['price']:,.0f}円" if p["price"] is not None else "—"
        halted = " ⛔停止中" if p["halted"] else ""
        lines.append(f"{p['symbol']}: {price} / 保有{p['position']:.6f}{halted}")
    if state["status"] != "ok":
        lines.append(f"⚠️ {state['statusText']}")
    return "\n".join(lines)

log = logging.getLogger("cryptobot.dashboard")

PRICE_POLL_SECONDS = 5
PRICE_HISTORY_MAX = 2880   # 5秒×2880 = 4時間ぶん
TRADES_POLL_SECONDS = 10   # 市場の約定フィードの取得間隔(銘柄を順繰りに取得)
MARKET_TRADES_MAX = 60
CANDLE_POLL_SECONDS = 60   # 戦略チャート用のOHLCV(ローソク足)の取得間隔
CANDLE_DISPLAY_MAX = 120   # 戦略チャートに描くローソク足の本数(判断より多めに見せる)


def _ma_series(closes: list[float], window: int) -> list[float | None]:
    """終値列に対する単純移動平均の系列(足りない区間はNone)。戦略のsmaと一致させる。"""
    out: list[float | None] = []
    for i in range(len(closes)):
        out.append(sma(closes[: i + 1], window) if i + 1 >= window else None)
    return out


def _crosses(fast: list[float | None], slow: list[float | None], ts: list[int],
             closes: list[float]) -> list[dict]:
    """fast/slowのゴールデン/デッドクロス点を検出する(戦略の判定と同じ向き)。"""
    out = []
    for i in range(1, len(ts)):
        fp, sp, fn, sn = fast[i - 1], slow[i - 1], fast[i], slow[i]
        if None in (fp, sp, fn, sn):
            continue
        if fp <= sp and fn > sn:
            out.append({"ts": ts[i], "type": "golden", "price": closes[i]})
        elif fp >= sp and fn < sn:
            out.append({"ts": ts[i], "type": "dead", "price": closes[i]})
    return out


class Dashboard:
    """botを背景スレッドで回し、最新状態を画面用に保持する。"""

    def __init__(self, cfg):
        if cfg.mode != "paper":
            raise SystemExit(
                "ダッシュボードはペーパートレード専用です。config.yaml を mode: paper にしてください。"
            )
        self.cfg = cfg
        self.portfolio = PortfolioRunner(cfg, SpotOnlyExchange(cfg))
        self.notifier = Notifier(cfg.notify.format)  # 定期レポート用(約定通知は各runnerが送る)
        self.key = self._load_or_create_key()  # トンネル共有時ののぞき見防止キー
        self.lock = threading.Lock()
        self.last_price: dict[str, float] = {}
        self.price_history = {s: deque(maxlen=PRICE_HISTORY_MAX) for s in cfg.symbols}
        self.market_trades = {s: deque(maxlen=MARKET_TRADES_MAX) for s in cfg.symbols}
        self._seen_ids = {s: deque(maxlen=MARKET_TRADES_MAX * 4) for s in cfg.symbols}
        self._trade_poll_i = 0
        self.candles: dict[str, list] = {s: [] for s in cfg.symbols}  # 戦略チャート用OHLCV
        self.last_results: dict[str, str] = {}
        self.last_error = ""
        self.last_run_at: datetime | None = None

    def _load_or_create_key(self) -> str:
        """アクセスキー。share.ps1でスマホ共有しても第三者に見られないようにする。"""
        key_file = Path(self.cfg.journal_path).parent / "dashboard_key.txt"
        if key_file.exists():
            key = key_file.read_text(encoding="utf-8").strip()
            if key:
                return key
        key = secrets.token_urlsafe(9)
        key_file.parent.mkdir(parents=True, exist_ok=True)
        key_file.write_text(key + "\n", encoding="utf-8")
        return key

    @property
    def exchange(self):
        return self.portfolio.exchange

    def _record_price(self, symbol: str, price: float) -> None:
        self.last_price[symbol] = price
        self.price_history[symbol].append((int(time.time() * 1000), price))

    def run_cycle(self) -> None:
        """全銘柄の売買判断1周ぶん。"""
        with self.lock:
            errors = []
            for sym, runner in self.portfolio.runners.items():
                try:
                    price = self.exchange.fetch_price(sym)
                    self._record_price(sym, price)
                    closes, highs, lows = fetch_window(self.exchange, runner.cfg)
                    self.last_results[sym] = runner.step(datetime.now(), price, closes, highs, lows)
                except Exception as e:
                    errors.append(f"{sym}: {type(e).__name__}: {e}")
                    log.warning("%s のサイクル失敗: %s", sym, e)
            self.last_error = " / ".join(errors)
            self.last_run_at = datetime.now()

    def poll_prices(self) -> None:
        """チャート用の価格サンプリング(売買判断はしない)。"""
        with self.lock:
            errors = []
            for sym in self.cfg.symbols:
                try:
                    self._record_price(sym, self.exchange.fetch_price(sym))
                except Exception as e:
                    errors.append(f"{sym}: {type(e).__name__}: {e}")
            self.last_error = " / ".join(errors)

    def poll_candles(self) -> None:
        """戦略チャート用のOHLCV(ローソク足)を取得してキャッシュする(ma_crossのみ)。"""
        if self.cfg.strategy != "ma_cross":
            return
        tf = self.cfg.ma_cross.timeframe
        limit = max(closes_needed(self.cfg), CANDLE_DISPLAY_MAX)
        for sym in self.cfg.symbols:
            try:
                rows = self.exchange.fetch_ohlcv(sym, tf, limit=limit)
            except Exception as e:
                log.debug("%s のローソク足取得失敗: %s", sym, e)
                continue
            with self.lock:
                self.candles[sym] = rows[-CANDLE_DISPLAY_MAX:]

    def strategy_chart(self, sym: str) -> dict | None:
        """選択銘柄の戦略チャート(ローソク足+移動平均+クロス点)を組み立てる。"""
        if self.cfg.strategy != "ma_cross":
            return None
        rows = self.candles.get(sym) or []
        if len(rows) < 2:
            return None
        ts = [int(r[0]) for r in rows]
        o = [float(r[1]) for r in rows]
        h = [float(r[2]) for r in rows]
        low = [float(r[3]) for r in rows]
        c = [float(r[4]) for r in rows]
        fast_n, slow_n = self.cfg.ma_cross.fast, self.cfg.ma_cross.slow
        fast = _ma_series(c, fast_n)
        slow = _ma_series(c, slow_n)
        gap = None
        if fast[-1] is not None and slow[-1] not in (None, 0):
            gap = (fast[-1] - slow[-1]) / slow[-1] * 100
        return {
            "ts": ts, "open": o, "high": h, "low": low, "close": c,
            "maFast": fast, "maSlow": slow, "fast": fast_n, "slow": slow_n,
            "crosses": _crosses(fast, slow, ts, c), "gapPct": gap,
            "timeframe": self.cfg.ma_cross.timeframe,
        }

    def poll_market_trades(self) -> None:
        """市場全体の約定(公開データ)を銘柄を順繰りに取り込む。"""
        sym = self.cfg.symbols[self._trade_poll_i % len(self.cfg.symbols)]
        self._trade_poll_i += 1
        try:
            trades = self.exchange.fetch_public_trades(sym, limit=30)
        except Exception as e:
            log.debug("%s の市場約定取得失敗: %s", sym, e)
            return
        with self.lock:
            for t in sorted(trades, key=lambda x: x["ts"]):
                if t["id"] in self._seen_ids[sym]:
                    continue
                self._seen_ids[sym].append(t["id"])
                self.market_trades[sym].append(t)

    def bot_loop(self) -> None:
        self.notifier.send("🚀 CryptoBot起動(ペーパートレード)。このあと判断のたびに定期レポートを送ります")
        while True:
            self.run_cycle()
            # スマホ用の定期レポート(interval_secondsごと=既定1時間ごと)
            self.notifier.send(status_summary(self.state()))
            # 売り残りのある銘柄がある間は短周期で再試行する
            time.sleep(min(r.next_sleep_seconds() for r in self.portfolio.runners.values()))

    def price_loop(self) -> None:
        time.sleep(PRICE_POLL_SECONDS)  # 起動直後はbot_loopが取得するので待つ
        self.poll_candles()  # 起動直後に戦略チャートを一度埋める(以後は定期更新)
        tick = 0
        while True:
            self.poll_prices()
            if tick % max(1, TRADES_POLL_SECONDS // PRICE_POLL_SECONDS) == 0:
                self.poll_market_trades()
            if tick % max(1, CANDLE_POLL_SECONDS // PRICE_POLL_SECONDS) == 0:
                self.poll_candles()
            tick += 1
            time.sleep(PRICE_POLL_SECONDS)

    def recent_trades(self, limit: int = 20) -> list[dict]:
        """全銘柄の取引を新しい順にマージして返す。"""
        out = []
        for sym, runner in self.portfolio.runners.items():
            path = Path(runner.cfg.journal_path)
            if not path.exists():
                continue
            with path.open(newline="", encoding="utf-8") as f:
                rows = [r for r in csv.reader(f) if r]
            if rows and rows[0] == HEADER:
                rows = rows[1:]
            for r in rows[-limit:]:
                out.append(
                    {
                        "ts": r[COL_TS],
                        "symbol": sym,
                        "side": r[COL_SIDE],
                        "amount": float(r[COL_AMOUNT]),
                        "price": float(r[COL_PRICE]),
                        "realized": float(r[COL_REALIZED]),
                    }
                )
        out.sort(key=lambda t: t["ts"], reverse=True)
        return out[:limit]

    def state(self) -> dict:
        with self.lock:
            halted = [s for s, r in self.portfolio.runners.items() if r.risk.halted]
            cash = sum(r.paper.jpy for r in self.portfolio.runners.values())
            pnl = sum(r.journal.total_realized_pnl for r in self.portfolio.runners.values())
            equity = 0.0
            equity_known = True
            per_symbol = []
            for sym, r in self.portfolio.runners.items():
                price = self.last_price.get(sym)
                if price is None:
                    equity_known = False
                else:
                    equity += r.paper.equity(price)
                per_symbol.append(
                    {
                        "symbol": sym,
                        "price": price,
                        "position": r.paper.base_amount,
                        "pnl": r.journal.total_realized_pnl,
                        "halted": r.risk.halted,
                        "lastResult": self.last_results.get(sym, "待機中"),
                    }
                )
            if len(halted) == len(self.portfolio.runners):
                status, status_text = "halted", "全銘柄停止中(人間の確認待ち)"
                detail = " / ".join(f"{s}: {self.portfolio.runners[s].risk.halt_reason}" for s in halted)
            elif self.last_error:
                status, status_text = "error", "接続エラー(自動で再試行します)"
                detail = self.last_error
            else:
                status, status_text = "ok", "稼働中 — ペーパートレード(仮想資金)"
                detail = f"停止中: {', '.join(halted)}" if halted else ""
            return {
                "exchange": self.cfg.exchange,
                "symbols": self.cfg.symbols,
                "strategy": self.cfg.strategy,
                "intervalSec": self.cfg.interval_seconds,
                "status": status,
                "statusText": status_text,
                "statusDetail": detail,
                "lastResults": self.last_results,
                "lastRunAt": self.last_run_at.strftime("%H:%M:%S") if self.last_run_at else "—",
                "equity": equity if equity_known else None,
                "cash": cash,
                "realizedPnl": pnl,
                "perSymbol": per_symbol,
                "history": {s: list(d) for s, d in self.price_history.items()},
                "marketTrades": {s: list(d)[::-1] for s, d in self.market_trades.items()},
                "trades": self.recent_trades(),
                "strategyChart": {s: self.strategy_chart(s) for s in self.cfg.symbols},
            }


PAGE = """<!doctype html>
<html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>CryptoBot Console</title>
<style>
:root {
  --bg:#070b14; --panel:rgba(148,184,255,.045); --line:rgba(120,180,255,.14);
  --ink:#e6edf7; --muted:#8593ab; --faint:#5a6579;
  --accent:#38e1ff; --accent2:#7c8cff;
  --ok:#3ddc97; --warn:#ffc857; --bad:#ff5d73;
  font-size:15px;
}
* { box-sizing:border-box; margin:0 }
body {
  font-family:"Segoe UI","Hiragino Sans","Yu Gothic UI",system-ui,sans-serif;
  background:var(--bg); color:var(--ink); min-height:100vh; overflow-x:hidden;
}
body::before {
  content:""; position:fixed; inset:-40%; z-index:-1; pointer-events:none;
  background:
    radial-gradient(38% 30% at 22% 18%, rgba(56,225,255,.09), transparent 70%),
    radial-gradient(34% 28% at 78% 12%, rgba(124,140,255,.10), transparent 70%),
    radial-gradient(50% 40% at 55% 95%, rgba(56,225,255,.05), transparent 70%);
  animation:drift 26s ease-in-out infinite alternate;
}
@keyframes drift { to { transform:translate3d(2.5%, 3.5%, 0) scale(1.06) } }
body::after {
  content:""; position:fixed; inset:0; z-index:-1; pointer-events:none; opacity:.5;
  background:
    linear-gradient(rgba(120,180,255,.05) 1px, transparent 1px) 0 0/100% 44px,
    linear-gradient(90deg, rgba(120,180,255,.05) 1px, transparent 1px) 0 0/44px 100%;
  mask-image:radial-gradient(70% 60% at 50% 30%, #000 30%, transparent 100%);
}
main { max-width:1040px; margin:0 auto; padding:28px 18px 40px }

header { display:flex; flex-wrap:wrap; align-items:baseline; gap:10px 16px; margin-bottom:18px }
h1 {
  font-size:1.3rem; letter-spacing:.14em; font-weight:650;
  background:linear-gradient(90deg,var(--accent),var(--accent2));
  -webkit-background-clip:text; background-clip:text; color:transparent;
}
.meta { color:var(--muted); font-size:.8rem; letter-spacing:.05em }

.card {
  background:var(--panel); border:1px solid var(--line); border-radius:16px;
  padding:18px 20px; backdrop-filter:blur(10px);
  box-shadow:0 0 0 1px rgba(56,225,255,.02), 0 10px 40px rgba(2,6,16,.5);
  transition:border-color .4s;
}
.card:hover { border-color:rgba(120,200,255,.28) }

.statusbar { display:flex; flex-wrap:wrap; align-items:center; gap:12px; margin-bottom:14px }
.pill {
  display:inline-flex; align-items:center; gap:8px; padding:7px 14px;
  border-radius:999px; border:1px solid var(--line); font-size:.85rem; font-weight:600;
  transition:all .5s;
}
.dot { width:9px; height:9px; border-radius:50%; position:relative }
.dot::after { content:""; position:absolute; inset:-4px; border-radius:50%; animation:pulse 2s infinite }
@keyframes pulse { 0%{box-shadow:0 0 0 0 currentColor; opacity:.55} 70%{box-shadow:0 0 0 9px transparent; opacity:0} 100%{opacity:0} }
.pill.ok    { color:var(--ok) }   .pill.ok .dot    { background:var(--ok);   color:var(--ok) }
.pill.error { color:var(--warn) } .pill.error .dot { background:var(--warn); color:var(--warn) }
.pill.halted{ color:var(--bad) }  .pill.halted .dot{ background:var(--bad);  color:var(--bad) }
.detail { color:var(--muted); font-size:.78rem; max-width:70ch; overflow-wrap:anywhere }

.grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(200px,1fr)); gap:12px; margin:14px 0 }
.tile .label { color:var(--muted); font-size:.72rem; letter-spacing:.12em }
.tile .value {
  margin-top:8px; font-size:1.7rem; font-weight:650;
  font-variant-numeric:tabular-nums; transition:text-shadow .6s;
}
.tile .value.glow { text-shadow:0 0 18px rgba(56,225,255,.55) }

.symrow { display:grid; grid-template-columns:repeat(auto-fit,minmax(220px,1fr)); gap:12px; margin:14px 0 }
.symcard { cursor:pointer; user-select:none }
.symcard.active { border-color:rgba(56,225,255,.55); box-shadow:0 0 24px rgba(56,225,255,.12) }
.symcard .sy { font-weight:700; letter-spacing:.08em }
.symcard .px { font-size:1.25rem; font-weight:650; font-variant-numeric:tabular-nums; margin-top:6px }
.symcard .ln { color:var(--muted); font-size:.72rem; margin-top:6px; font-variant-numeric:tabular-nums }
.symcard .halt { color:var(--bad); font-size:.72rem; font-weight:700 }

.chart-head { display:flex; justify-content:space-between; align-items:baseline; margin-bottom:6px }
.chart-head h2, section h2 { font-size:.85rem; letter-spacing:.14em; color:var(--muted); font-weight:600 }
#bigprice { font-size:2rem; font-weight:700; font-variant-numeric:tabular-nums;
  color:var(--accent); text-shadow:0 0 22px rgba(56,225,255,.35) }
#chartwrap { position:relative }
#tooltip {
  position:absolute; pointer-events:none; padding:6px 10px; border-radius:8px;
  background:rgba(10,16,30,.92); border:1px solid var(--line); font-size:.75rem;
  font-variant-numeric:tabular-nums; opacity:0; transition:opacity .15s; white-space:nowrap;
}
svg text { fill:var(--faint); font-size:10px; font-variant-numeric:tabular-nums }

button {
  background:linear-gradient(90deg, rgba(56,225,255,.14), rgba(124,140,255,.14));
  border:1px solid rgba(56,225,255,.45); color:var(--accent);
  padding:10px 22px; border-radius:10px; font-size:.9rem; font-weight:600;
  letter-spacing:.08em; cursor:pointer; transition:all .25s;
}
button:hover { box-shadow:0 0 22px rgba(56,225,255,.25); transform:translateY(-1px) }
button:active { transform:translateY(1px) }
button:disabled { opacity:.5; cursor:wait }

table { width:100%; border-collapse:collapse; font-size:.82rem; margin-top:8px }
th { text-align:left; color:var(--faint); font-weight:500; letter-spacing:.08em;
     border-bottom:1px solid var(--line); padding:7px 8px; font-size:.72rem }
td { padding:7px 8px; border-bottom:1px solid rgba(120,180,255,.07);
     font-variant-numeric:tabular-nums }
td.num { text-align:right }
tr.fresh { animation:slidein .6s ease-out }
@keyframes slidein { from { opacity:0; transform:translateX(-8px) } }
.side-buy  { color:var(--ok);  font-weight:600 }
.side-sell { color:var(--bad); font-weight:600 }
.pnl-pos { color:var(--ok) } .pnl-neg { color:var(--bad) }
.note { color:var(--faint); font-size:.75rem; margin-top:16px; line-height:1.7 }
section { margin-top:14px }

.cols { display:grid; grid-template-columns:5fr 7fr; gap:12px; margin-top:14px }
@media (max-width:760px) { .cols { grid-template-columns:1fr } }
@media (max-width:520px) {
  .chart-head { flex-wrap:wrap; gap:2px }
  #bigprice { font-size:1.45rem }
  .legend { margin-left:0; width:100% }
}
#feed { margin-top:8px; height:320px; overflow:hidden; position:relative;
  mask-image:linear-gradient(#000 78%, transparent 100%) }
.frow { display:grid; grid-template-columns:52px 44px 1fr 1fr; gap:8px;
  padding:5px 6px; font-size:.8rem; font-variant-numeric:tabular-nums;
  border-bottom:1px solid rgba(120,180,255,.06) }
.frow.new { animation:feedin .5s ease-out }
@keyframes feedin { from { opacity:0; transform:translateY(-10px);
  background:rgba(56,225,255,.12) } to { background:transparent } }
.frow .t { color:var(--faint) }
.frow .p, .frow .a { text-align:right }
.livechip { display:inline-flex; align-items:center; gap:6px; color:var(--bad);
  font-size:.68rem; letter-spacing:.2em; font-weight:700 }
.livechip::before { content:""; width:7px; height:7px; border-radius:50%;
  background:var(--bad); animation:blink 1.2s infinite }
@keyframes blink { 50% { opacity:.25 } }

/* チャートのモード切替(セグメントボタン) */
.chart-toggle { display:flex; flex-wrap:wrap; align-items:center; gap:8px; margin:2px 0 10px }
.seg {
  background:rgba(120,180,255,.06); border:1px solid var(--line); color:var(--muted);
  padding:6px 14px; border-radius:9px; font-size:.78rem; font-weight:600; letter-spacing:.06em;
  cursor:pointer; transition:all .3s; box-shadow:none;
}
.seg:hover { color:var(--ink); border-color:rgba(120,200,255,.3); transform:none }
.seg.active { color:var(--accent); border-color:rgba(56,225,255,.5);
  background:linear-gradient(90deg, rgba(56,225,255,.14), rgba(124,140,255,.12));
  box-shadow:0 0 18px rgba(56,225,255,.15) }
.legend { display:flex; gap:14px; font-size:.72rem; color:var(--muted); margin-left:auto;
  flex-wrap:wrap; font-variant-numeric:tabular-nums }
.legend .lg { display:inline-flex; align-items:center; gap:6px }
.legend .sw { width:16px; height:0; border-top-width:2px; border-top-style:solid; border-radius:2px }
.stratline { margin-top:10px; font-size:.8rem; color:var(--muted); line-height:1.6;
  border-left:2px solid var(--line); padding-left:12px; min-height:1.2em; transition:border-color .5s }
.stratline b { color:var(--ink); font-variant-numeric:tabular-nums }
.stratline .up { color:var(--ok) } .stratline .down { color:var(--bad) }

/* チャート内の描画要素 */
.gline { stroke:#38e1ff; stroke-width:2; fill:none; stroke-linejoin:round;
  filter:drop-shadow(0 0 6px rgba(56,225,255,.55)) }
.gline.draw { stroke-dasharray:var(--len); stroke-dashoffset:var(--len); animation:draw 1.1s ease forwards }
@keyframes draw { to { stroke-dashoffset:0 } }
.candle-up { stroke:var(--ok); fill:rgba(61,220,151,.85) }
.candle-dn { stroke:var(--bad); fill:rgba(255,93,115,.85) }
.ma-fast { stroke:#ffc857; stroke-width:1.6; fill:none; stroke-linejoin:round; opacity:.95 }
.ma-slow { stroke:#7c8cff; stroke-width:1.6; fill:none; stroke-linejoin:round; opacity:.95 }
.xmark { font-weight:700 }
.tmark-buy { fill:var(--ok) } .tmark-sell { fill:var(--bad) }
</style></head><body><main>
<header>
  <h1>CRYPTOBOT CONSOLE</h1>
  <span class="meta" id="meta">—</span>
</header>

<div class="card">
  <div class="statusbar">
    <span class="pill ok" id="pill"><span class="dot"></span><span id="statustext">接続中…</span></span>
    <span class="detail" id="statusdetail"></span>
  </div>
  <div class="detail" id="lastresults">—</div>
  <div style="margin-top:12px"><button id="stepbtn">⚡ 今すぐ全銘柄を1回判断する</button>
    <span class="detail" id="lastrun" style="margin-left:10px"></span></div>
</div>

<div class="symrow" id="symrow"></div>

<section class="card">
  <div class="chart-head"><h2 id="charttitle">PRICE — <span id="chartsymbol"></span></h2>
    <span id="bigprice">—</span></div>
  <div class="chart-toggle">
    <button class="seg active" id="modeLive">ライブ ・ 直近4時間</button>
    <button class="seg" id="modeStrat">戦略 ・ 移動平均クロス</button>
    <span class="legend" id="legend"></span>
  </div>
  <div id="chartwrap">
    <svg id="chart" viewBox="0 0 960 240" width="100%" height="240" preserveAspectRatio="none"></svg>
    <div id="tooltip"></div>
  </div>
  <div class="stratline" id="stratline"></div>
</section>

<div class="grid">
  <div class="card tile"><div class="label">資産評価額(仮想・全銘柄合計)</div>
    <div class="value" id="equity">—</div></div>
  <div class="card tile"><div class="label">現金残高(仮想・合計)</div>
    <div class="value" id="cash">—</div></div>
  <div class="card tile"><div class="label">累計実現損益 ※課税対象の目安</div>
    <div class="value" id="pnl">—</div></div>
</div>

<div class="cols">
  <section class="card">
    <div style="display:flex;justify-content:space-between;align-items:center">
      <h2>MARKET FEED — <span id="feedsymbol"></span> 市場の約定</h2><span class="livechip">LIVE</span></div>
    <div id="feed"><div class="frow"><span class="t">収集中…</span></div></div>
  </section>
  <section class="card">
    <h2>MY TRADES — botの取引(全銘柄・新しい順)</h2>
    <div style="overflow-x:auto"><table>
      <thead><tr><th>日時</th><th>銘柄</th><th>売買</th><th>数量</th><th>価格(円)</th><th>実現損益</th></tr></thead>
      <tbody id="trades"><tr><td colspan="6" style="color:var(--faint)">まだ取引はありません。条件が揃うと自動で仮想売買されます。</td></tr></tbody>
    </table></div>
  </section>
</div>

<p class="note">これはペーパートレード(仮想売買)です。実際のお金は一切動いていません。
銘柄カードをクリックするとチャートとフィードが切り替わります。
止めるには起動した黒い画面(ターミナル)を閉じるだけでOK。再開すると残高・履歴は自動復元されます。</p>
</main>
<script>
const $ = id => document.getElementById(id);
const cur = {};
let sel = null;      // 選択中の銘柄
let lastState = null;

function tween(id, target, fmt) {
  if (target == null) { $(id).textContent = "—"; cur[id] = null; return; }
  const from = (cur[id] == null || isNaN(cur[id])) ? target : cur[id];
  cur[id] = target;
  const el = $(id), t0 = performance.now(), dur = 700;
  if (from !== target) { el.classList.add("glow"); setTimeout(() => el.classList.remove("glow"), 900); }
  (function frame(t) {
    const p = Math.min(1, (t - t0) / dur), e = 1 - Math.pow(1 - p, 3);
    el.textContent = fmt(from + (target - from) * e);
    if (p < 1) requestAnimationFrame(frame);
  })(t0);
}

const CW = 960, CH = 240, PL = 8, PR = 74, PT = 14, PB = 22;
let chartMode = "live";      // "live"(ライブ線) | "strat"(戦略ローソク)
let lastChartKey = "";        // mode+銘柄。変わったときだけ描画アニメを流す

const pad2 = n => String(n).padStart(2, "0");
const hm = d => `${pad2(d.getHours())}:${pad2(d.getMinutes())}`;
function parseTs(s) { const d = new Date(String(s).replace(" ", "T")); return isNaN(d) ? null : d.getTime(); }
function gridLines(lo, hi, Y) {
  return [0, .25, .5, .75, 1].map(f => {
    const p = lo + (hi - lo) * f, y = Y(p).toFixed(1);
    return `<line x1="${PL}" y1="${y}" x2="${CW-PR}" y2="${y}" stroke="rgba(120,180,255,.07)"/>` +
      `<text x="${CW-PR+6}" y="${(+y+3.5)}">${Math.round(p).toLocaleString()}</text>`;
  }).join("");
}
// botの売買を三角マーカーとして返す(範囲外・解析不能は捨てる)
function tradeMarks(trades, sym, t0, t1, xForTs, Y) {
  if (!trades) return "";
  return trades.filter(t => t.symbol === sym).map(t => {
    const ms = parseTs(t.ts); if (ms == null || ms < t0 || ms > t1) return "";
    const x = xForTs(ms), y = Y(t.price), buy = t.side === "買" || t.side === "buy";
    const cls = buy ? "tmark-buy" : "tmark-sell", dy = buy ? 9 : -9, tip = buy ? "買" : "売";
    return `<path class="${cls}" d="M${x-5},${y+dy} L${x+5},${y+dy} L${x},${y+(buy?0:0)+(buy?-0:0)} Z"
      transform="translate(0,${buy?4:-4})" opacity=".95"/>` +
      `<text class="${cls}" x="${x}" y="${buy?y+22:y-14}" text-anchor="middle" font-size="10">${tip}</text>`;
  }).join("");
}
function animateLine(svg) {
  const p = svg.querySelector(".gline"); if (!p) return;
  try { const L = p.getTotalLength(); p.style.setProperty("--len", L.toFixed(0)); p.classList.add("draw"); } catch (e) {}
}

function drawChart(hist, trades, animate) {
  const svg = $("chart");
  if (!hist || hist.length < 2) {
    svg.innerHTML = '<text x="480" y="120" text-anchor="middle">データ収集中…(5秒ごとに増えます)</text>';
    return;
  }
  if (hist.length > 400) {
    const step = Math.ceil(hist.length / 400);
    hist = hist.filter((_, i) => i % step === 0 || i === hist.length - 1);
  }
  const ts = hist.map(h => h[0]), ps = hist.map(h => h[1]);
  let lo = Math.min(...ps), hi = Math.max(...ps);
  if (hi - lo < hi * 1e-4) { const m = (hi + lo) / 2; lo = m * 0.9995; hi = m * 1.0005; }
  const p0 = (hi - lo) * 0.12; lo -= p0; hi += p0;
  const X = t => PL + (t - ts[0]) / Math.max(1, ts[ts.length-1] - ts[0]) * (CW - PL - PR);
  const Y = p => PT + (1 - (p - lo) / (hi - lo)) * (CH - PT - PB);
  let d = "";
  hist.forEach((h, i) => { d += (i ? "L" : "M") + X(h[0]).toFixed(1) + "," + Y(h[1]).toFixed(1); });
  const area = d + `L${(CW-PR).toFixed(1)},${CH-PB}L${PL},${CH-PB}Z`;
  svg.innerHTML = `
    <defs><linearGradient id="ag" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="rgba(56,225,255,.28)"/>
      <stop offset="1" stop-color="rgba(56,225,255,0)"/></linearGradient></defs>
    ${gridLines(lo, hi, Y)}
    <path d="${area}" fill="url(#ag)"/>
    <path class="gline" d="${d}"/>
    ${tradeMarks(trades, sel, ts[0], ts[ts.length-1], X, Y)}
    <line id="xh" y1="${PT}" y2="${CH-PB}" stroke="rgba(230,240,255,.35)" stroke-dasharray="3 3" visibility="hidden"/>
    <circle id="xc" r="4" fill="#38e1ff" visibility="hidden" style="filter:drop-shadow(0 0 6px #38e1ff)"/>`;
  if (animate) animateLine(svg);
  svg.onmousemove = ev => {
    const r = svg.getBoundingClientRect();
    const mx = (ev.clientX - r.left) / r.width * CW;
    let best = 0, bd = 1e18;
    hist.forEach((h, i) => { const dx = Math.abs(X(h[0]) - mx); if (dx < bd) { bd = dx; best = i; } });
    const h = hist[best], x = X(h[0]), y = Y(h[1]);
    $("xh").setAttribute("x1", x); $("xh").setAttribute("x2", x); $("xh").style.visibility = "visible";
    $("xc").setAttribute("cx", x); $("xc").setAttribute("cy", y); $("xc").style.visibility = "visible";
    const tip = $("tooltip");
    tip.textContent = `${hm(new Date(h[0]))}  ${Math.round(h[1]).toLocaleString()}円`;
    tip.style.left = Math.min(x / CW * r.width + 12, r.width - 130) + "px";
    tip.style.top = (y / CH * r.height - 34) + "px";
    tip.style.opacity = 1;
  };
  svg.onmouseleave = () => { $("tooltip").style.opacity = 0;
    $("xh").style.visibility = "hidden"; $("xc").style.visibility = "hidden"; };
}

function drawStrategy(st, trades, animate) {
  const svg = $("chart");
  if (!st || !st.close || st.close.length < 2) {
    svg.innerHTML = '<text x="480" y="120" text-anchor="middle">ローソク足を取得中…(戦略はma_crossのときに表示されます)</text>';
    return;
  }
  const n = st.close.length, ts = st.ts;
  const vals = st.high.concat(st.low, st.maFast.filter(v => v != null), st.maSlow.filter(v => v != null));
  let lo = Math.min(...vals), hi = Math.max(...vals);
  const p0 = (hi - lo) * 0.08 || hi * 0.01; lo -= p0; hi += p0;
  const band = (CW - PL - PR) / n;
  const cx = i => PL + (i + 0.5) * band;
  const Y = p => PT + (1 - (p - lo) / (hi - lo)) * (CH - PT - PB);
  const xForTs = ms => { let b = 0, bd = 1e18; ts.forEach((t, i) => { const dd = Math.abs(t - ms); if (dd < bd) { bd = dd; b = i; } }); return cx(b); };
  const bw = Math.max(1.2, band * 0.6);
  let candles = "";
  for (let i = 0; i < n; i++) {
    const up = st.close[i] >= st.open[i], cls = up ? "candle-up" : "candle-dn";
    const x = cx(i), yH = Y(st.high[i]), yL = Y(st.low[i]);
    const yO = Y(st.open[i]), yC = Y(st.close[i]);
    const top = Math.min(yO, yC), h = Math.max(1, Math.abs(yO - yC));
    candles += `<line class="${cls}" x1="${x}" y1="${yH.toFixed(1)}" x2="${x}" y2="${yL.toFixed(1)}" stroke-width="1"/>` +
      `<rect class="${cls}" x="${(x-bw/2).toFixed(1)}" y="${top.toFixed(1)}" width="${bw.toFixed(1)}" height="${h.toFixed(1)}"/>`;
  }
  const maPath = (arr, cls) => {
    let d = "", started = false;
    arr.forEach((v, i) => { if (v == null) return; d += (started ? "L" : "M") + cx(i).toFixed(1) + "," + Y(v).toFixed(1); started = true; });
    return d ? `<path class="${cls}" d="${d}"/>` : "";
  };
  const crossMarks = (st.crosses || []).map(cr => {
    const x = xForTs(cr.ts), y = Y(cr.price), g = cr.type === "golden";
    return `<circle cx="${x}" cy="${y}" r="6" fill="none" stroke="${g ? "var(--ok)" : "var(--bad)"}" stroke-width="1.5" opacity=".9"/>` +
      `<text class="xmark" x="${x}" y="${g ? y+20 : y-12}" text-anchor="middle" fill="${g ? "var(--ok)" : "var(--bad)"}" font-size="11">${g ? "▲G" : "▼D"}</text>`;
  }).join("");
  svg.innerHTML = `${gridLines(lo, hi, Y)}${candles}
    ${maPath(st.maSlow, "ma-slow")}${maPath(st.maFast, "ma-fast")}
    ${crossMarks}
    ${tradeMarks(trades, sel, ts[0], ts[n-1], xForTs, Y)}`;
  svg.onmousemove = ev => {
    const r = svg.getBoundingClientRect();
    const mx = (ev.clientX - r.left) / r.width * CW;
    const i = Math.max(0, Math.min(n - 1, Math.round((mx - PL) / band - 0.5)));
    const tip = $("tooltip"), d = new Date(ts[i]);
    tip.innerHTML = `${pad2(d.getMonth()+1)}/${pad2(d.getDate())} ${hm(d)}　終値 ${Math.round(st.close[i]).toLocaleString()}円` +
      (st.maFast[i] != null ? `<br>短期 ${Math.round(st.maFast[i]).toLocaleString()} / 長期 ${st.maSlow[i]!=null?Math.round(st.maSlow[i]).toLocaleString():"—"}` : "");
    tip.style.left = Math.min(cx(i) / CW * r.width + 12, r.width - 180) + "px";
    tip.style.top = (Y(st.close[i]) / CH * r.height - 40) + "px";
    tip.style.opacity = 1;
  };
  svg.onmouseleave = () => { $("tooltip").style.opacity = 0; };
}

function renderLegend(st) {
  const lg = $("legend");
  if (chartMode === "strat" && st) {
    lg.innerHTML =
      `<span class="lg"><span class="sw" style="border-color:#ffc857"></span>短期(${st.fast})</span>` +
      `<span class="lg"><span class="sw" style="border-color:#7c8cff"></span>長期(${st.slow})</span>` +
      `<span class="lg"><span style="color:var(--ok)">▲G</span>ゴールデン</span>` +
      `<span class="lg"><span style="color:var(--bad)">▼D</span>デッド</span>`;
  } else {
    lg.innerHTML = `<span class="lg"><span class="sw" style="border-color:#38e1ff"></span>価格(5秒ごと)</span>` +
      `<span class="lg"><span style="color:var(--ok)">▲</span>bot買 <span style="color:var(--bad)">▼</span>bot売</span>`;
  }
}

// fast vs slow を平易な言葉で説明(なぜ売る/売らないかが分かる一文)
function renderStratLine(st, hasPos) {
  const el = $("stratline");
  if (chartMode !== "strat") { el.textContent = "チャートにカーソルを合わせると、その時刻の価格が出ます。▲▼はbotが実際に売買した地点です。"; return; }
  if (!st || st.gapPct == null) { el.textContent = "移動平均を計算するのに十分なローソク足がまだありません(取得中)。"; return; }
  const up = st.gapPct >= 0, cls = up ? "up" : "down";
  const arrow = up ? "上回っています" : "下回っています";
  let verdict;
  if (up) verdict = hasPos ? "上昇トレンド。保有を継続します(短期が長期を下回ると売りシグナル)。"
    : "上昇トレンド。次にゴールデンクロスが出れば買います。";
  else verdict = hasPos ? "下降トレンド。<b class='down'>売りシグナル点灯中</b>(次の判断で全量売却します)。"
    : "下降トレンド。今は買いません(様子見)。";
  el.innerHTML = `短期線(${st.fast}本)は長期線(${st.slow}本)を <b class="${cls}">${up?"+":""}${st.gapPct.toFixed(2)}%</b> ${arrow}。${verdict}`;
}

const seenFeed = new Set();
function renderFeed(mts) {
  if (!mts || !mts.length) {
    $("feed").innerHTML = '<div class="frow"><span class="t">収集中…</span></div>';
    return;
  }
  const firstLoad = seenFeed.size === 0;
  $("feed").innerHTML = mts.map(t => {
    const d = new Date(t.ts);
    const hh = String(d.getHours()).padStart(2, "0"), mm = String(d.getMinutes()).padStart(2, "0"),
          ss = String(d.getSeconds()).padStart(2, "0");
    const fresh = !firstLoad && !seenFeed.has(t.id);
    return `<div class="frow ${fresh ? "new" : ""}">` +
      `<span class="t">${hh}:${mm}:${ss}</span>` +
      `<span class="${t.side === "buy" ? "side-buy" : "side-sell"}">${t.side === "buy" ? "▲ 買" : "▼ 売"}</span>` +
      `<span class="p">${Math.round(t.price).toLocaleString()}円</span>` +
      `<span class="a">${t.amount.toFixed(4)}</span></div>`;
  }).join("");
  mts.forEach(t => seenFeed.add(t.id));
}

function renderSymbols(s) {
  $("symrow").innerHTML = s.perSymbol.map(p =>
    `<div class="card symcard ${p.symbol === sel ? "active" : ""}" onclick="pick('${p.symbol}')">` +
    `<div class="sy">${p.symbol}${p.halted ? ' <span class="halt">⛔停止</span>' : ""}</div>` +
    `<div class="px">${p.price == null ? "—" : Math.round(p.price).toLocaleString() + "円"}</div>` +
    `<div class="ln">保有 ${p.position.toFixed(6)} ・ 損益 ` +
    `<span class="${p.pnl > 0 ? "pnl-pos" : p.pnl < 0 ? "pnl-neg" : ""}">` +
    `${(p.pnl >= 0 ? "+" : "") + Math.round(p.pnl).toLocaleString()}円</span></div></div>`
  ).join("");
}

function pick(sym) { sel = sym; seenFeed.clear(); if (lastState) render(lastState); }
window.pick = pick;

function setMode(m) {
  chartMode = m;
  $("modeLive").classList.toggle("active", m === "live");
  $("modeStrat").classList.toggle("active", m === "strat");
  if (lastState) render(lastState);
}
$("modeLive").onclick = () => setMode("live");
$("modeStrat").onclick = () => setMode("strat");

let lastTradeKey = "";
function render(s) {
  lastState = s;
  if (!sel || !s.symbols.includes(sel)) sel = s.symbols[0];
  $("meta").textContent =
    `${s.exchange} / ${s.symbols.join(" ・ ")} / 戦略: ${s.strategy} / 判断間隔: ${Math.round(s.intervalSec/60)}分`;
  $("chartsymbol").textContent = sel;
  $("feedsymbol").textContent = sel;
  $("pill").className = "pill " + s.status;
  $("statustext").textContent = s.statusText;
  $("statusdetail").textContent = s.statusDetail || "";
  $("lastresults").innerHTML = s.symbols.map(sym =>
    `<div>最新の判断 <b>${sym}</b>: ${s.lastResults[sym] || "待機中"}</div>`).join("");
  $("lastrun").textContent = s.lastRunAt !== "—" ? `最終実行 ${s.lastRunAt}` : "";
  renderSymbols(s);
  const selInfo = s.perSymbol.find(p => p.symbol === sel);
  tween("bigprice", selInfo ? selInfo.price : null, v => Math.round(v).toLocaleString("ja-JP") + "円");
  tween("equity", s.equity, v => Math.round(v).toLocaleString("ja-JP") + "円");
  tween("cash", s.cash, v => Math.round(v).toLocaleString("ja-JP") + "円");
  tween("pnl", s.realizedPnl, v => (v >= 0 ? "+" : "") + Math.round(v).toLocaleString("ja-JP") + "円");
  $("pnl").style.color = s.realizedPnl > 0 ? "var(--ok)" : s.realizedPnl < 0 ? "var(--bad)" : "";
  // 戦略チャート(移動平均)は ma_cross のときだけ。DCAでは戦略ボタンを隠す
  const st = (s.strategyChart || {})[sel];
  const isMa = s.strategy === "ma_cross";
  $("modeStrat").style.display = isMa ? "" : "none";
  if (!isMa && chartMode === "strat") setMode("live");
  const key = chartMode + "|" + sel;
  const animate = key !== lastChartKey; lastChartKey = key;
  $("charttitle").innerHTML = (chartMode === "strat" ? "STRATEGY — " : "PRICE — ") + `<span id="chartsymbol">${sel}</span>`;
  if (chartMode === "strat") drawStrategy(st, s.trades, animate);
  else drawChart(s.history[sel], s.trades, animate);
  renderLegend(st);
  renderStratLine(st, selInfo ? selInfo.position > 0 : false);
  renderFeed(s.marketTrades[sel]);
  const tkey = s.trades.length ? s.trades[0].ts + s.trades[0].symbol + s.trades.length : "";
  if (s.trades.length) {
    $("trades").innerHTML = s.trades.map((t, i) =>
      `<tr class="${i === 0 && tkey !== lastTradeKey ? "fresh" : ""}">` +
      `<td>${t.ts}</td><td>${t.symbol}</td>` +
      `<td class="${t.side === "買" ? "side-buy" : "side-sell"}">${t.side}</td>` +
      `<td class="num">${t.amount.toFixed(8)}</td><td class="num">${Math.round(t.price).toLocaleString()}</td>` +
      `<td class="num ${t.realized > 0 ? "pnl-pos" : t.realized < 0 ? "pnl-neg" : ""}">` +
      `${t.side === "売" ? (t.realized >= 0 ? "+" : "") + Math.round(t.realized).toLocaleString() + "円" : "—"}</td></tr>`
    ).join("");
  }
  lastTradeKey = tkey;
}

const KEY = new URLSearchParams(location.search).get("key") || "";
async function refresh() {
  try {
    const s = await (await fetch("/api/state?key=" + encodeURIComponent(KEY))).json();
    render(s);
  } catch (e) {
    $("statustext").textContent = "画面とbotの接続が切れました(黒い画面が閉じていませんか?)";
    $("pill").className = "pill error";
  }
}
$("stepbtn").onclick = async () => {
  const b = $("stepbtn"); b.disabled = true; b.textContent = "⏳ 判断中…";
  try { await fetch("/step?key=" + encodeURIComponent(KEY), { method: "POST" }); await refresh(); }
  finally { b.disabled = false; b.textContent = "⚡ 今すぐ全銘柄を1回判断する"; }
};
if (window.MOCK_STATE) { render(window.MOCK_STATE); }
else { refresh(); setInterval(refresh, 3000); }
</script></body></html>"""


def make_handler(dash: Dashboard):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, body: bytes, ctype: str, code: int = 200):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _route(self) -> tuple[str, bool]:
            """(パス, 認証OKか)。キー必須: トンネル共有時に第三者から守るため。"""
            parsed = urlparse(self.path)
            key = (parse_qs(parsed.query).get("key") or [""])[0]
            return parsed.path, key == dash.key

        def do_GET(self):
            path, ok = self._route()
            if not ok:
                self._send(
                    "アクセスキーが必要です。PCの黒い画面に表示されたURL"
                    "(?key=... 付き)から開いてください。".encode("utf-8"),
                    "text/plain; charset=utf-8", 401,
                )
                return
            if path == "/":
                self._send(PAGE.encode("utf-8"), "text/html; charset=utf-8")
            elif path == "/api/state":
                self._send(
                    json.dumps(dash.state(), ensure_ascii=False).encode("utf-8"),
                    "application/json; charset=utf-8",
                )
            else:
                self.send_error(404)

        def do_POST(self):
            path, ok = self._route()
            if not ok:
                self._send(b'{"error": "unauthorized"}', "application/json", 401)
                return
            if path != "/step":
                self.send_error(404)
                return
            dash.run_cycle()
            self._send(b'{"ok": true}', "application/json")

        def log_message(self, *args):
            pass  # アクセスログでターミナルを埋めない

    return Handler


def main() -> None:
    parser = argparse.ArgumentParser(description="CryptoBot ダッシュボード(ペーパートレード専用)")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    _lock = acquire_singleton_lock()  # 二重起動防止(帳簿の交錯を防ぐ)。プロセス終了まで保持
    dash = Dashboard(load_config(args.config))
    threading.Thread(target=dash.bot_loop, daemon=True).start()
    threading.Thread(target=dash.price_loop, daemon=True).start()

    url = f"http://127.0.0.1:{args.port}/?key={dash.key}"
    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(dash))
    print(f"ダッシュボード起動: {url}")
    print("(この画面を閉じるとbotも止まります。スマホで見るには share.ps1 を実行)")
    threading.Timer(1.0, webbrowser.open, args=(url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("停止しました")


if __name__ == "__main__":
    main()
