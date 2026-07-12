"""ブラウザで見られる管理画面(ペーパートレード専用)。

使い方:
    python dashboard.py            # 起動してブラウザが自動で開く(http://localhost:8765)
    python dashboard.py --port 9000

安全設計:
- ペーパートレード(mode: paper)専用。liveモードでは起動を拒否する
- 自分のPCの中だけで動く(127.0.0.1にのみバインド。外部公開されない)
- 画面は読み取り+「今すぐ1回判断」ボタンのみ。設定変更はconfig.yamlで行う

構成:
- 背景スレッド①: interval_secondsごとに売買判断(bot本体)
- 背景スレッド②: 20秒ごとに価格だけ取得してチャート用の履歴を貯める
- フロントエンドは /api/state を5秒ごとにポーリングしてぬるぬる更新する
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import threading
import time
import webbrowser
from collections import deque
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from bot.config import load_config
from bot.exchange import SpotOnlyExchange
from bot.journal import COL_AMOUNT, COL_PRICE, COL_REALIZED, COL_SIDE, COL_TS, HEADER
from bot.runner import BotRunner, fetch_closes

log = logging.getLogger("cryptobot.dashboard")

PRICE_POLL_SECONDS = 20
PRICE_HISTORY_MAX = 720  # 20秒×720 = 4時間ぶん


class Dashboard:
    """botを背景スレッドで回し、最新状態を画面用に保持する。"""

    def __init__(self, cfg):
        if cfg.mode != "paper":
            raise SystemExit(
                "ダッシュボードはペーパートレード専用です。config.yaml を mode: paper にしてください。"
            )
        self.cfg = cfg
        self.runner = BotRunner(cfg, SpotOnlyExchange(cfg))
        self.lock = threading.Lock()
        self.last_price: float | None = None
        self.price_history: deque[tuple[int, float]] = deque(maxlen=PRICE_HISTORY_MAX)
        self.last_result = "まだ実行していません(自動実行を待つか「今すぐ判断」を押してください)"
        self.last_error = ""
        self.last_run_at: datetime | None = None

    def _record_price(self, price: float) -> None:
        self.last_price = price
        self.price_history.append((int(time.time() * 1000), price))

    def run_cycle(self) -> None:
        """売買判断1回ぶん。"""
        with self.lock:
            try:
                price = self.runner.exchange.fetch_price(self.cfg.symbol)
                self._record_price(price)
                closes = fetch_closes(self.runner.exchange, self.cfg)
                self.last_result = self.runner.step(datetime.now(), price, closes)
                self.last_error = ""
            except Exception as e:
                self.last_error = f"{type(e).__name__}: {e}"
                log.warning("サイクル失敗: %s", self.last_error)
            self.last_run_at = datetime.now()

    def poll_price(self) -> None:
        """チャート用の価格サンプリング(売買判断はしない)。"""
        with self.lock:
            try:
                self._record_price(self.runner.exchange.fetch_price(self.cfg.symbol))
                self.last_error = ""
            except Exception as e:
                self.last_error = f"{type(e).__name__}: {e}"

    def bot_loop(self) -> None:
        while True:
            self.run_cycle()
            time.sleep(self.cfg.interval_seconds)

    def price_loop(self) -> None:
        time.sleep(PRICE_POLL_SECONDS)  # 起動直後はbot_loopが取得するので待つ
        while True:
            self.poll_price()
            time.sleep(PRICE_POLL_SECONDS)

    def recent_trades(self, limit: int = 20) -> list[dict]:
        path = Path(self.cfg.journal_path)
        if not path.exists():
            return []
        with path.open(newline="", encoding="utf-8") as f:
            rows = [r for r in csv.reader(f) if r]
        if rows and rows[0] == HEADER:
            rows = rows[1:]
        out = []
        for r in rows[-limit:][::-1]:  # 新しい順
            out.append(
                {
                    "ts": r[COL_TS],
                    "side": r[COL_SIDE],
                    "amount": float(r[COL_AMOUNT]),
                    "price": float(r[COL_PRICE]),
                    "realized": float(r[COL_REALIZED]),
                }
            )
        return out

    def state(self) -> dict:
        with self.lock:
            r = self.runner
            price = self.last_price
            if r.risk.halted:
                status, status_text = "halted", "全停止中(人間の確認待ち)"
                detail = r.risk.halt_reason
            elif self.last_error:
                status, status_text = "error", "接続エラー(自動で再試行します)"
                detail = self.last_error
            else:
                status, status_text = "ok", "稼働中 — ペーパートレード(仮想資金)"
                detail = ""
            return {
                "exchange": self.cfg.exchange,
                "symbol": self.cfg.symbol,
                "strategy": self.cfg.strategy,
                "intervalSec": self.cfg.interval_seconds,
                "status": status,
                "statusText": status_text,
                "statusDetail": detail,
                "lastResult": self.last_result,
                "lastRunAt": self.last_run_at.strftime("%H:%M:%S") if self.last_run_at else "—",
                "price": price,
                "history": list(self.price_history),
                "equity": r.paper.equity(price) if price is not None else None,
                "cash": r.paper.jpy,
                "position": r.paper.base_amount,
                "realizedPnl": r.journal.total_realized_pnl,
                "trades": self.recent_trades(),
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
body::before { /* 奥行きのあるオーロラ背景 */
  content:""; position:fixed; inset:-40%; z-index:-1; pointer-events:none;
  background:
    radial-gradient(38% 30% at 22% 18%, rgba(56,225,255,.09), transparent 70%),
    radial-gradient(34% 28% at 78% 12%, rgba(124,140,255,.10), transparent 70%),
    radial-gradient(50% 40% at 55% 95%, rgba(56,225,255,.05), transparent 70%);
  animation:drift 26s ease-in-out infinite alternate;
}
@keyframes drift { to { transform:translate3d(2.5%, 3.5%, 0) scale(1.06) } }
body::after { /* 近未来グリッド */
  content:""; position:fixed; inset:0; z-index:-1; pointer-events:none; opacity:.5;
  background:
    linear-gradient(rgba(120,180,255,.05) 1px, transparent 1px) 0 0/100% 44px,
    linear-gradient(90deg, rgba(120,180,255,.05) 1px, transparent 1px) 0 0/44px 100%;
  mask-image:radial-gradient(70% 60% at 50% 30%, #000 30%, transparent 100%);
}
main { max-width:980px; margin:0 auto; padding:28px 18px 40px }

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
.detail { color:var(--muted); font-size:.78rem; max-width:60ch; overflow-wrap:anywhere }

.grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(200px,1fr)); gap:12px; margin:14px 0 }
.tile .label { color:var(--muted); font-size:.72rem; letter-spacing:.12em }
.tile .value {
  margin-top:8px; font-size:1.7rem; font-weight:650;
  font-variant-numeric:tabular-nums; transition:text-shadow .6s;
}
.tile .value.glow { text-shadow:0 0 18px rgba(56,225,255,.55) }
.unit { font-size:.85rem; color:var(--muted); margin-left:2px }

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
  <div class="detail">最新の判断: <span id="lastresult" style="color:var(--ink)">—</span>
    <span id="lastrun"></span></div>
  <div style="margin-top:12px"><button id="stepbtn">⚡ 今すぐ1回判断する</button></div>
</div>

<section class="card">
  <div class="chart-head"><h2>PRICE — <span id="chartsymbol"></span>(直近4時間)</h2>
    <span id="bigprice">—</span></div>
  <div id="chartwrap">
    <svg id="chart" viewBox="0 0 960 220" width="100%" height="220" preserveAspectRatio="none"></svg>
    <div id="tooltip"></div>
  </div>
</section>

<div class="grid">
  <div class="card tile"><div class="label">資産評価額(仮想)</div>
    <div class="value" id="equity">—</div></div>
  <div class="card tile"><div class="label">現金残高(仮想)</div>
    <div class="value" id="cash">—</div></div>
  <div class="card tile"><div class="label">保有数量</div>
    <div class="value" id="position">—</div></div>
  <div class="card tile"><div class="label">累計実現損益 ※課税対象の目安</div>
    <div class="value" id="pnl">—</div></div>
</div>

<section class="card">
  <h2>RECENT TRADES(新しい順)</h2>
  <div style="overflow-x:auto"><table>
    <thead><tr><th>日時</th><th>売買</th><th>数量</th><th>価格(円)</th><th>実現損益</th></tr></thead>
    <tbody id="trades"><tr><td colspan="5" style="color:var(--faint)">まだ取引はありません。条件が揃うと自動で仮想売買されます。</td></tr></tbody>
  </table></div>
</section>

<p class="note">これはペーパートレード(仮想売買)です。実際のお金は一切動いていません。
止めるには起動した黒い画面(ターミナル)を閉じるだけでOK。再開すると残高・履歴は自動復元されます。</p>
</main>
<script>
const $ = id => document.getElementById(id);
const yen = v => v == null ? "—" : Math.round(v).toLocaleString("ja-JP") + "円";
const cur = {};  // 数値アニメーションの現在値

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

function drawChart(hist) {
  const svg = $("chart");
  if (!hist || hist.length < 2) {
    svg.innerHTML = '<text x="480" y="115" text-anchor="middle">データ収集中…(20秒ごとに増えます)</text>';
    return;
  }
  const W = 960, H = 220, PL = 8, PR = 74, PT = 14, PB = 22;
  const ts = hist.map(h => h[0]), ps = hist.map(h => h[1]);
  let lo = Math.min(...ps), hi = Math.max(...ps);
  if (hi - lo < hi * 1e-4) { const m = (hi + lo) / 2; lo = m * 0.9995; hi = m * 1.0005; }
  const pad = (hi - lo) * 0.12; lo -= pad; hi += pad;
  const X = t => PL + (t - ts[0]) / Math.max(1, ts[ts.length-1] - ts[0]) * (W - PL - PR);
  const Y = p => PT + (1 - (p - lo) / (hi - lo)) * (H - PT - PB);
  let d = "";
  hist.forEach((h, i) => { d += (i ? "L" : "M") + X(h[0]).toFixed(1) + "," + Y(h[1]).toFixed(1); });
  const area = d + `L${(W-PR).toFixed(1)},${H-PB}L${PL},${H-PB}Z`;
  const gl = [0, .5, 1].map(f => { // 目盛り3本
    const p = lo + (hi - lo) * f, y = Y(p).toFixed(1);
    return `<line x1="${PL}" y1="${y}" x2="${W-PR}" y2="${y}" stroke="rgba(120,180,255,.08)"/>` +
           `<text x="${W-PR+6}" y="${(+y+3.5)}">${Math.round(p).toLocaleString()}</text>`;
  }).join("");
  svg.innerHTML = `
    <defs><linearGradient id="ag" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="rgba(56,225,255,.28)"/>
      <stop offset="1" stop-color="rgba(56,225,255,0)"/></linearGradient></defs>
    ${gl}
    <path d="${area}" fill="url(#ag)"/>
    <path d="${d}" fill="none" stroke="#38e1ff" stroke-width="2" stroke-linejoin="round"
      style="filter:drop-shadow(0 0 6px rgba(56,225,255,.6))"/>
    <line id="xh" y1="${PT}" y2="${H-PB}" stroke="rgba(230,240,255,.35)" stroke-dasharray="3 3" visibility="hidden"/>
    <circle id="xc" r="4" fill="#38e1ff" visibility="hidden" style="filter:drop-shadow(0 0 6px #38e1ff)"/>`;
  svg.onmousemove = ev => {
    const r = svg.getBoundingClientRect();
    const mx = (ev.clientX - r.left) / r.width * W;
    let best = 0, bd = 1e18;
    hist.forEach((h, i) => { const dx = Math.abs(X(h[0]) - mx); if (dx < bd) { bd = dx; best = i; } });
    const h = hist[best], x = X(h[0]), y = Y(h[1]);
    const xh = $("xh"), xc = $("xc"), tip = $("tooltip");
    xh.setAttribute("x1", x); xh.setAttribute("x2", x); xh.style.visibility = "visible";
    xc.setAttribute("cx", x); xc.setAttribute("cy", y); xc.style.visibility = "visible";
    const t = new Date(h[0]);
    tip.textContent = `${String(t.getHours()).padStart(2,"0")}:${String(t.getMinutes()).padStart(2,"0")}  ${Math.round(h[1]).toLocaleString()}円`;
    tip.style.left = Math.min(x / W * r.width + 12, r.width - 130) + "px";
    tip.style.top = (y / H * r.height - 34) + "px";
    tip.style.opacity = 1;
  };
  svg.onmouseleave = () => { $("tooltip").style.opacity = 0;
    $("xh").style.visibility = "hidden"; $("xc").style.visibility = "hidden"; };
}

let lastTradeKey = "";
function render(s) {
  $("meta").textContent =
    `${s.exchange} / ${s.symbol} / 戦略: ${s.strategy} / 判断間隔: ${Math.round(s.intervalSec/60)}分 / 表示は5秒ごと更新`;
  $("chartsymbol").textContent = s.symbol;
  const pill = $("pill");
  pill.className = "pill " + s.status;
  $("statustext").textContent = s.statusText;
  $("statusdetail").textContent = s.statusDetail || "";
  $("lastresult").textContent = s.lastResult;
  $("lastrun").textContent = s.lastRunAt !== "—" ? `(${s.lastRunAt})` : "";
  tween("bigprice", s.price, v => Math.round(v).toLocaleString("ja-JP") + "円");
  tween("equity", s.equity, v => Math.round(v).toLocaleString("ja-JP") + "円");
  tween("cash", s.cash, v => Math.round(v).toLocaleString("ja-JP") + "円");
  tween("pnl", s.realizedPnl, v => (v >= 0 ? "+" : "") + Math.round(v).toLocaleString("ja-JP") + "円");
  $("position").textContent = s.position.toFixed(8);
  $("pnl").style.color = s.realizedPnl > 0 ? "var(--ok)" : s.realizedPnl < 0 ? "var(--bad)" : "";
  drawChart(s.history);
  const key = s.trades.length ? s.trades[0].ts + s.trades.length : "";
  if (s.trades.length) {
    $("trades").innerHTML = s.trades.map((t, i) =>
      `<tr class="${i === 0 && key !== lastTradeKey ? "fresh" : ""}">` +
      `<td>${t.ts}</td><td class="${t.side === "買" ? "side-buy" : "side-sell"}">${t.side}</td>` +
      `<td class="num">${t.amount.toFixed(8)}</td><td class="num">${Math.round(t.price).toLocaleString()}</td>` +
      `<td class="num ${t.realized > 0 ? "pnl-pos" : t.realized < 0 ? "pnl-neg" : ""}">` +
      `${t.side === "売" ? (t.realized >= 0 ? "+" : "") + Math.round(t.realized).toLocaleString() + "円" : "—"}</td></tr>`
    ).join("");
  }
  lastTradeKey = key;
}

async function refresh() {
  try {
    const s = await (await fetch("/api/state")).json();
    render(s);
  } catch (e) { $("statustext").textContent = "画面とbotの接続が切れました(黒い画面が閉じていませんか?)"; $("pill").className = "pill error"; }
}
$("stepbtn").onclick = async () => {
  const b = $("stepbtn"); b.disabled = true; b.textContent = "⏳ 判断中…";
  try { await fetch("/step", { method: "POST" }); await refresh(); }
  finally { b.disabled = false; b.textContent = "⚡ 今すぐ1回判断する"; }
};
if (window.MOCK_STATE) { render(window.MOCK_STATE); }
else { refresh(); setInterval(refresh, 5000); }
</script></body></html>"""


def make_handler(dash: Dashboard):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, body: bytes, ctype: str, code: int = 200):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path == "/":
                self._send(PAGE.encode("utf-8"), "text/html; charset=utf-8")
            elif self.path == "/api/state":
                self._send(
                    json.dumps(dash.state(), ensure_ascii=False).encode("utf-8"),
                    "application/json; charset=utf-8",
                )
            else:
                self.send_error(404)

        def do_POST(self):
            if self.path != "/step":
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

    dash = Dashboard(load_config(args.config))
    threading.Thread(target=dash.bot_loop, daemon=True).start()
    threading.Thread(target=dash.price_loop, daemon=True).start()

    url = f"http://127.0.0.1:{args.port}"
    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(dash))
    print(f"ダッシュボード起動: {url} (この画面を閉じるとbotも止まります)")
    threading.Timer(1.0, webbrowser.open, args=(url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("停止しました")


if __name__ == "__main__":
    main()
