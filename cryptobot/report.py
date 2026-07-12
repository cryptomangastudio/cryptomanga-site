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
from bot.journal import COL_FEE, COL_JPY, COL_REALIZED, COL_SIDE, COL_TS, HEADER
from bot.portfolio import sub_config


@dataclass
class MonthlySummary:
    buys: int = 0
    sells: int = 0
    buy_jpy: float = 0.0
    sell_jpy: float = 0.0
    fee_jpy: float = 0.0
    realized_pnl: float = 0.0


def load_rows(journal_paths: list[Path]) -> list[list[str]]:
    """全帳簿の明細行を日時順にマージして返す。"""
    rows: list[list[str]] = []
    for journal_path in journal_paths:
        with journal_path.open(newline="", encoding="utf-8") as f:
            reader = csv.reader(f)
            header = next(reader, None)
            if header != HEADER:
                raise SystemExit(f"{journal_path} のヘッダーが想定と異なります")
            rows.extend(r for r in reader if r)
    rows.sort(key=lambda r: r[COL_TS])
    return rows


def soheikin_by_year(rows: list[list[str]]) -> dict[str, float]:
    """総平均法(個人の法定デフォルト)による年別実現損益の概算。

    年内の全買付(前年からの繰越を含む)の平均単価で売却損益を計算する。
    移動平均法(帳簿の記載)とは金額が変わりうるため、両方を表示して比較する。
    """
    from bot.journal import COL_AMOUNT, COL_PRICE

    by_year: dict[str, list[list[str]]] = defaultdict(list)
    for r in rows:
        by_year[r[COL_TS][:4]].append(r)

    result: dict[str, float] = {}
    carry_amount = 0.0
    carry_cost = 0.0
    for year in sorted(by_year):
        buys_cost = carry_cost
        total_amount = carry_amount
        for r in by_year[year]:
            if r[COL_SIDE] == "買":
                amount, price, fee = float(r[COL_AMOUNT]), float(r[COL_PRICE]), float(r[COL_FEE])
                buys_cost += amount * price + fee
                total_amount += amount
        avg = buys_cost / total_amount if total_amount > 0 else 0.0
        realized = 0.0
        sold = 0.0
        for r in by_year[year]:
            if r[COL_SIDE] == "売":
                amount, price, fee = float(r[COL_AMOUNT]), float(r[COL_PRICE]), float(r[COL_FEE])
                realized += (price - avg) * amount - fee
                sold += amount
        result[year] = realized
        carry_amount = total_amount - sold
        carry_cost = carry_amount * avg
    return result


def aggregate(journal_paths: list[Path]) -> dict[str, MonthlySummary]:
    """複数銘柄の記帳CSVを月別に合算する。"""
    months: dict[str, MonthlySummary] = defaultdict(MonthlySummary)
    for journal_path in journal_paths:
        with journal_path.open(newline="", encoding="utf-8") as f:
            reader = csv.reader(f)
            header = next(reader, None)
            if header != HEADER:
                raise SystemExit(f"{journal_path} のヘッダーが想定と異なります")
            for row in reader:
                if not row:
                    continue
                month = row[COL_TS][:7]  # YYYY-MM
                m = months[month]
                jpy = float(row[COL_JPY])
                m.fee_jpy += float(row[COL_FEE])
                if row[COL_SIDE] == "買":
                    m.buys += 1
                    m.buy_jpy += jpy
                else:
                    m.sells += 1
                    m.sell_jpy += jpy
                    m.realized_pnl += float(row[COL_REALIZED])
    return dict(sorted(months.items()))


def render_markdown(
    months: dict[str, MonthlySummary],
    soheikin: dict[str, float] | None = None,
    tax_rate_pct: float = 20.0,
) -> str:
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
    lines.append("| 年 | 移動平均法(帳簿) | 総平均法(概算・法定デフォルト) | 税引後の概算(税率" + f"{tax_rate_pct:.0f}%) |")
    lines.append("|---|---:|---:|---:|")
    for year in years:
        ido = sum(m.realized_pnl for month, m in months.items() if month.startswith(year))
        so = (soheikin or {}).get(year, ido)
        base = so  # 法定デフォルトの総平均法を税額目安の基準にする
        after = base * (1 - tax_rate_pct / 100) if base > 0 else base
        lines.append(f"| {year} | {ido:+,.0f}円 | {so:+,.0f}円 | {after:+,.0f}円 |")
    lines.append("")
    lines.append(
        "※ 個人は届出をしない限り**総平均法**が法定の計算方法(移動平均法は届出制・3年変更不可)。"
        "税率は総合課税のため人により異なり、上記は概算。損失は他所得と通算・繰越不可。"
    )
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

    tax_rate = 20.0
    if args.journal:
        journal_paths = [Path(args.journal)]
    else:
        cfg = load_config(args.config)
        tax_rate = cfg.tax_rate_pct
        journal_paths = [Path(sub_config(cfg, s).journal_path) for s in cfg.symbols]
    journal_paths = [p for p in journal_paths if p.exists()]
    if not journal_paths:
        raise SystemExit("記帳CSVが見つかりません(まだ取引がないかパスが違います)")

    months = aggregate(journal_paths)
    if not months:
        raise SystemExit("取引記録がまだありません")

    markdown = render_markdown(months, soheikin_by_year(load_rows(journal_paths)), tax_rate)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    latest_year = max(months)[:4]
    out_path = out_dir / f"report_{latest_year}.md"
    out_path.write_text(markdown, encoding="utf-8")
    print(markdown)
    print(f"保存しました: {out_path}(Driveの運用フォルダへアップロード推奨)")


if __name__ == "__main__":
    main()
