"""ブラウザで見られる管理画面(ペーパートレード専用)。

使い方:
    python dashboard.py            # 起動してブラウザが自動で開く(http://localhost:8765)
    python dashboard.py --port 9000

安全設計:
- ペーパートレード(mode: paper)専用。liveモードでは起動を拒否する
- 自分のPCの中だけで動く(127.0.0.1にのみバインド。外部公開されない)
- 画面は読み取り+「今すぐ1回判断」ボタンのみ。設定変更はconfig.yamlで行う
"""
from __future__ import annotations

import argparse
import csv
import html
import logging
import threading
import time
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from bot.config import load_config
from bot.exchange import SpotOnlyExchange
from bot.journal import COL_AMOUNT, COL_PRICE, COL_REALIZED, COL_SIDE, COL_TS, HEADER
from bot.runner import BotRunner

log = logging.getLogger("cryptobot.dashboard")


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
        self.last_result = "まだ実行していません(次の自動実行を待つか「今すぐ1回判断」を押してください)"
        self.last_error = ""
        self.last_run_at: datetime | None = None

    def run_cycle(self) -> None:
        with self.lock:
            try:
                price = self.runner.exchange.fetch_price(self.cfg.symbol)
                closes = [
                    c[4]
                    for c in self.runner.exchange.fetch_ohlcv(
                        self.cfg.symbol,
                        self.cfg.ma_cross.timeframe,
                        limit=self.cfg.ma_cross.slow + 5,
                    )
                ]
                self.last_price = price
                self.last_result = self.runner.step(datetime.now(), price, closes)
                self.last_error = ""
            except Exception as e:
                self.last_error = f"{type(e).__name__}: {e}"
                log.warning("サイクル失敗: %s", self.last_error)
            self.last_run_at = datetime.now()

    def loop(self) -> None:
        while True:
            self.run_cycle()
            time.sleep(self.cfg.interval_seconds)

    def recent_trades(self, limit: int = 20) -> list[list[str]]:
        path = Path(self.cfg.journal_path)
        if not path.exists():
            return []
        with path.open(newline="", encoding="utf-8") as f:
            rows = [r for r in csv.reader(f) if r]
        if rows and rows[0] == HEADER:
            rows = rows[1:]
        return rows[-limit:][::-1]  # 新しい順


def _fmt_jpy(v: float | None) -> str:
    return "—" if v is None else f"{v:,.0f}円"


def _fmt_signed_jpy(v: float) -> str:
    return f"{v:+,.0f}円"


def render_html(dash: Dashboard) -> str:
    r = dash.runner
    price = dash.last_price
    equity = r.paper.equity(price) if price is not None else None
    pnl = r.journal.total_realized_pnl
    halted = r.risk.halted

    if halted:
        status = f'<span class="badge bad">⛔ 停止中</span><div class="muted">{html.escape(r.risk.halt_reason)}</div>'
    elif dash.last_error:
        status = f'<span class="badge warn">⚠️ 接続エラー(自動で再試行します)</span><div class="muted">{html.escape(dash.last_error)}</div>'
    else:
        status = '<span class="badge ok">● 稼働中(ペーパートレード=仮想のお金)</span>'

    trade_rows = []
    for row in dash.recent_trades():
        realized = float(row[COL_REALIZED])
        trade_rows.append(
            "<tr>"
            f"<td>{html.escape(row[COL_TS])}</td>"
            f"<td>{html.escape(row[COL_SIDE])}</td>"
            f"<td class='num'>{float(row[COL_AMOUNT]):.8f}</td>"
            f"<td class='num'>{float(row[COL_PRICE]):,.0f}</td>"
            f"<td class='num'>{_fmt_signed_jpy(realized) if row[COL_SIDE] == '売' else '—'}</td>"
            "</tr>"
        )
    trades_html = (
        f"<table><thead><tr><th>日時</th><th>売買</th><th>数量</th><th>価格(円)</th>"
        f"<th>実現損益</th></tr></thead><tbody>{''.join(trade_rows)}</tbody></table>"
        if trade_rows
        else '<p class="muted">まだ取引はありません。戦略の条件が揃うと自動で仮想売買されます。</p>'
    )

    last_run = dash.last_run_at.strftime("%H:%M:%S") if dash.last_run_at else "—"
    return f"""<!doctype html>
<html lang="ja"><head><meta charset="utf-8">
<meta http-equiv="refresh" content="30">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>CryptoBot ダッシュボード</title>
<style>
:root {{ --ink:#1a1f26; --muted:#5c6470; --line:#e4e7ec; --card:#ffffff; --bg:#f5f6f8;
        --ok:#0a7d43; --warn:#8a5a00; --bad:#b3261e; }}
@media (prefers-color-scheme: dark) {{
  :root {{ --ink:#e8eaee; --muted:#9aa3af; --line:#2a2f37; --card:#1b1f26; --bg:#12151a;
          --ok:#4ccb8b; --warn:#e0b153; --bad:#f28b82; }}
}}
* {{ box-sizing:border-box }}
body {{ font-family:-apple-system,"Hiragino Sans","Yu Gothic UI",sans-serif; margin:0;
       background:var(--bg); color:var(--ink); }}
main {{ max-width:860px; margin:0 auto; padding:24px 16px }}
h1 {{ font-size:1.25rem; margin:0 0 4px }}
.sub {{ color:var(--muted); font-size:.85rem; margin-bottom:16px }}
.badge {{ font-weight:600 }}
.badge.ok {{ color:var(--ok) }} .badge.warn {{ color:var(--warn) }} .badge.bad {{ color:var(--bad) }}
.tiles {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(180px,1fr)); gap:12px; margin:16px 0 }}
.tile {{ background:var(--card); border:1px solid var(--line); border-radius:10px; padding:14px 16px }}
.tile .label {{ color:var(--muted); font-size:.78rem }}
.tile .value {{ font-size:1.45rem; font-weight:650; margin-top:4px; font-variant-numeric:tabular-nums }}
.card {{ background:var(--card); border:1px solid var(--line); border-radius:10px; padding:16px; margin-bottom:16px }}
.muted {{ color:var(--muted); font-size:.85rem }}
table {{ width:100%; border-collapse:collapse; font-size:.85rem }}
th {{ text-align:left; color:var(--muted); font-weight:500; border-bottom:1px solid var(--line); padding:6px 8px }}
td {{ padding:6px 8px; border-bottom:1px solid var(--line) }}
td.num {{ text-align:right; font-variant-numeric:tabular-nums }}
.overflow {{ overflow-x:auto }}
button {{ background:var(--ink); color:var(--bg); border:0; border-radius:8px;
         padding:10px 16px; font-size:.9rem; cursor:pointer }}
</style></head><body><main>
<h1>CryptoBot ダッシュボード</h1>
<div class="sub">{html.escape(dash.cfg.exchange)} / {html.escape(dash.cfg.symbol)} /
戦略: {html.escape(dash.cfg.strategy)} / 30秒ごとに自動更新(最終実行 {last_run})</div>
<div class="card">{status}
<div class="muted" style="margin-top:8px">最新の判断: {html.escape(dash.last_result)}</div>
<form method="post" action="/step" style="margin:12px 0 0">
<button>今すぐ1回判断する</button></form></div>
<div class="tiles">
<div class="tile"><div class="label">資産評価額(仮想)</div><div class="value">{_fmt_jpy(equity)}</div></div>
<div class="tile"><div class="label">現金残高(仮想)</div><div class="value">{_fmt_jpy(r.paper.jpy)}</div></div>
<div class="tile"><div class="label">保有数量</div><div class="value">{r.paper.base_amount:.8f}</div></div>
<div class="tile"><div class="label">累計実現損益 ※課税対象の目安</div><div class="value">{_fmt_signed_jpy(pnl)}</div></div>
<div class="tile"><div class="label">現在価格</div><div class="value">{_fmt_jpy(price)}</div></div>
</div>
<div class="card"><h2 style="font-size:1rem;margin:0 0 8px">直近の取引(新しい順)</h2>
<div class="overflow">{trades_html}</div></div>
<p class="muted">これはペーパートレード(仮想売買)です。実際のお金は動いていません。
止めるには起動した黒い画面(ターミナル)を閉じるだけでOKです。</p>
</main></body></html>"""


def make_handler(dash: Dashboard):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path != "/":
                self.send_error(404)
                return
            body = render_html(dash).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            if self.path != "/step":
                self.send_error(404)
                return
            dash.run_cycle()
            self.send_response(303)
            self.send_header("Location", "/")
            self.end_headers()

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
    threading.Thread(target=dash.loop, daemon=True).start()

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
