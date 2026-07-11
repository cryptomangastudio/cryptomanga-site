"""月次レポート生成。取引記帳CSVから月別の損益サマリーを作る。

出力(reports/report_YYYY.md)は Google Drive の「CryptoBot運用」フォルダに
月1回アップロードして保管する運用を推奨。確定申告時の基礎資料にもなる。

使い方:
    python report.py --config config.yaml
    python report.py --journal data/trades.csv --out reports/
"""
from __future__ import annotations

import argparse
import csv
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from bot.config import load_config
from bot.journal import HEADER


@dataclass
class MonthlySummary:
    buys: int = 0
    sells: int = 0
    buy_jpy: float = 0.0
    sell_jpy: float = 0.0
    fee_jpy: float = 0.0
    realized_pnl: float = 0.0


def aggregate(journal_path: Path) -> dict[str, MonthlySummary]:
    months: dict[str, MonthlySummary] = defaultdict(MonthlySummary)
    with journal_path.open(newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        header = next(reader, None)
        if header != HEADER:
            raise SystemExit(f"{journal_path} のヘッダーが想定と異なります")
        for row in reader:
            if not row:
                continue
            month = row[0][:7]  # YYYY-MM
            m = months[month]
            jpy = float(row[6])
            m.fee_jpy += float(row[7])
            if row[3] == "買":
                m.buys += 1
                m.buy_jpy += jpy
            else:
                m.sells += 1
                m.sell_jpy += jpy
                m.realized_pnl += float(row[9])
    return dict(sorted(months.items()))


def render_markdown(months: dict[str, MonthlySummary]) -> str:
    lines = [
        "# CryptoBot 月次レポート",
        "",
        "| 月 | 買付回数 | 売却回数 | 買付額(円) | 売却額(円) | 手数料(円) | 実現損益(円) | 累計実現損益(円) |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    cumulative = 0.0
    for month, m in months.items():
        cumulative += m.realized_pnl
        lines.append(
            f"| {month} | {m.buys} | {m.sells} | {m.buy_jpy:,.0f} | {m.sell_jpy:,.0f} "
            f"| {m.fee_jpy:,.0f} | {m.realized_pnl:+,.0f} | {cumulative:+,.0f} |"
        )
    years = sorted({month[:4] for month in months})
    lines.append("")
    lines.append("## 年間サマリー(税金の目安)")
    lines.append("")
    for year in years:
        total = sum(m.realized_pnl for month, m in months.items() if month.startswith(year))
        lines.append(f"- **{year}年の実現損益: {total:+,.0f}円**(原則、雑所得として課税対象)")
    lines += [
        "",
        "### ⚠️ リマインダー",
        "- 実現損益は「売却して確定した損益」。日本円に出金していなくても課税対象です。",
        "- 給与所得者で年間20万円超なら確定申告が必要(20万円以下でも住民税の申告は必要)。",
        "- 取引所の「年間取引報告書」も必ずダウンロードして保管すること。",
        "- 納税資金として利益の3割を目安に確保しておくこと。",
        "- このレポートをGoogle Driveの「CryptoBot運用」フォルダに保管すること。",
    ]
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="CryptoBot 月次レポート生成")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--journal", help="取引記帳CSV(省略時はconfigのjournal_path)")
    parser.add_argument("--out", default="reports", help="出力ディレクトリ")
    args = parser.parse_args()

    if args.journal:
        journal_path = Path(args.journal)
    else:
        journal_path = Path(load_config(args.config).journal_path)
    if not journal_path.exists():
        raise SystemExit(f"記帳CSVが見つかりません: {journal_path}")

    months = aggregate(journal_path)
    if not months:
        raise SystemExit("取引記録がまだありません")

    markdown = render_markdown(months)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    latest_year = max(months)[:4]
    out_path = out_dir / f"report_{latest_year}.md"
    out_path.write_text(markdown, encoding="utf-8")
    print(markdown)
    print(f"保存しました: {out_path}(Driveの運用フォルダへアップロード推奨)")


if __name__ == "__main__":
    main()
