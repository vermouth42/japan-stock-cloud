"""prices.csv から最新日のシグナル・スコア一覧を作る。

使い方:
    python python/screen.py                       # signals.csv と signals.md を出力
    python python/screen.py --summary "$GITHUB_STEP_SUMMARY"   # Actions のサマリーにも表示
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
from typing import Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from jpstock.data import load_prices  # noqa: E402
from jpstock.rules import (  # noqa: E402
    LONG_SETUPS,
    SETUP_LABELS,
    SwingParams,
    analyze,
    crossed_below_mid,
    warnings_for,
)

HEADER = [
    "日付", "コード", "終値", "シグナル", "スコア", "トレンド",
    "RSI14", "ATR14", "ATR%", "出来高倍率", "25日線乖離%",
    "5日線", "25日線", "75日線", "200日線", "20日高値", "20日安値",
    "損切り目安", "利確目安(2R)", "100株リスク(円)", "注意",
]


def fmt(value: Optional[float], digits: int = 1) -> str:
    if value is None:
        return ""
    text = f"{value:.{digits}f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


def trend_label(a: Dict[str, List], i: int) -> str:
    if a["perfect_up"][i] and a["up_trend"][i]:
        return "上昇(PO)"
    if a["up_trend"][i]:
        return "上昇"
    if a["perfect_down"][i] and a["down_trend"][i]:
        return "下降(逆PO)"
    if a["down_trend"][i]:
        return "下降"
    if a["ma_m"][i] is None or a["ma_l"][i] is None:
        return "データ不足"
    return "もみ合い"


def build_rows(prices_path: str, p: SwingParams) -> List[Dict[str, object]]:
    data = load_prices(prices_path)
    if not data:
        return []
    latest_date = max(bars[-1].date for bars in data.values() if bars)
    rows: List[Dict[str, object]] = []
    for code, bars in data.items():
        if not bars:
            continue
        a = analyze(bars, p)
        i = len(bars) - 1
        b = bars[i]
        setup = a["setup"][i]
        atr = a["atr"][i]
        notes = warnings_for(a, i)
        closes = [x.close for x in bars]
        if crossed_below_mid(closes, a["ma_m"], i):
            notes.insert(0, "25日線割れ")
        if b.date < latest_date:
            notes.insert(0, f"データ更新なし({b.date})")
        ma_t = a["ma_t"][i]
        if p.trend_ma_len > 0:
            if ma_t is None:
                notes.append(f"{p.trend_ma_len}日線データ不足")
            elif b.close < ma_t:
                notes.append(f"{p.trend_ma_len}日線の下")

        stop = tp = risk100 = None
        if setup is not None and atr is not None:
            side = 1 if setup in LONG_SETUPS else -1
            risk = p.stop_atr * atr
            stop = b.close - side * risk
            tp = b.close + side * p.tp_r * risk
            risk100 = risk * 100

        rows.append({
            "日付": b.date,
            "コード": code,
            "終値": b.close,
            "シグナル": SETUP_LABELS.get(setup, "") if setup else "",
            "_setup": setup,
            "スコア": a["score"][i],
            "トレンド": trend_label(a, i),
            "RSI14": a["rsi"][i],
            "ATR14": atr,
            "ATR%": atr / b.close * 100 if atr is not None and b.close else None,
            "出来高倍率": a["vol_ratio"][i],
            "25日線乖離%": a["dev"][i],
            "5日線": a["ma_s"][i],
            "25日線": a["ma_m"][i],
            "75日線": a["ma_l"][i],
            "200日線": ma_t,
            "20日高値": a["hh_prev"][i],
            "20日安値": a["ll_prev"][i],
            "損切り目安": stop,
            "利確目安(2R)": tp,
            "100株リスク(円)": risk100,
            "注意": " / ".join(notes),
        })

    def sort_key(r: Dict[str, object]):
        has_signal = 0 if r["_setup"] else 1
        score = r["スコア"] if isinstance(r["スコア"], int) else -1
        return (has_signal, -score, str(r["コード"]))

    rows.sort(key=sort_key)
    return rows


def write_csv(rows: List[Dict[str, object]], path: str) -> None:
    digits = {
        "終値": 1, "RSI14": 1, "ATR14": 1, "ATR%": 2, "出来高倍率": 2, "25日線乖離%": 2,
        "5日線": 1, "25日線": 1, "75日線": 1, "200日線": 1, "20日高値": 1, "20日安値": 1,
        "損切り目安": 1, "利確目安(2R)": 1, "100株リスク(円)": 0,
    }
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(HEADER)
        for r in rows:
            out = []
            for col in HEADER:
                v = r[col]
                if col in digits:
                    out.append(fmt(v, digits[col]))  # type: ignore[arg-type]
                elif v is None:
                    out.append("")
                else:
                    out.append(str(v))
            writer.writerow(out)


def build_markdown(rows: List[Dict[str, object]]) -> str:
    if not rows:
        return "# 日本株シグナル\n\nデータがありません。\n"
    date = max(str(r["日付"]) for r in rows)
    lines = [f"# 日本株シグナル（{date} 終値）", ""]

    signals = [r for r in rows if r["_setup"]]
    lines.append(f"## 新規シグナル（{len(signals)}件）")
    lines.append("")
    if signals:
        lines.append("| コード | シグナル | 終値 | スコア | 出来高倍率 | RSI | 損切り目安 | 利確目安(2R) | 100株リスク |")
        lines.append("|---|---|---:|---:|---:|---:|---:|---:|---:|")
        for r in signals:
            lines.append(
                f"| {r['コード']} | {r['シグナル']} | {fmt(r['終値'])} | {r['スコア']} | "  # type: ignore[arg-type]
                f"{fmt(r['出来高倍率'], 2)} | {fmt(r['RSI14'])} | {fmt(r['損切り目安'])} | "  # type: ignore[arg-type]
                f"{fmt(r['利確目安(2R)'])} | {fmt(r['100株リスク(円)'], 0)}円 |"  # type: ignore[arg-type]
            )
    else:
        lines.append("本日の新規シグナルはありません。")
    lines.append("")

    ranked = sorted(
        (r for r in rows if isinstance(r["スコア"], int)),
        key=lambda r: -r["スコア"],  # type: ignore[operator]
    )[:15]
    lines.append("## テクニカルスコア上位15")
    lines.append("")
    lines.append("| コード | スコア | トレンド | 終値 | 25日線乖離% | RSI | 出来高倍率 | ATR% |")
    lines.append("|---|---:|---|---:|---:|---:|---:|---:|")
    for r in ranked:
        lines.append(
            f"| {r['コード']} | {r['スコア']} | {r['トレンド']} | {fmt(r['終値'])} | "  # type: ignore[arg-type]
            f"{fmt(r['25日線乖離%'], 1)} | {fmt(r['RSI14'])} | {fmt(r['出来高倍率'], 2)} | {fmt(r['ATR%'], 1)} |"  # type: ignore[arg-type]
        )
    lines.append("")

    cautions = [r for r in rows if r["注意"]]
    if cautions:
        lines.append("## 注意（保有中なら確認）")
        lines.append("")
        lines.append("| コード | 注意 | 終値 | トレンド |")
        lines.append("|---|---|---:|---|")
        for r in cautions:
            lines.append(f"| {r['コード']} | {r['注意']} | {fmt(r['終値'])} | {r['トレンド']} |")  # type: ignore[arg-type]
        lines.append("")

    lines.append("---")
    lines.append(
        "ルールは docs/strategy_spec.md。買いシグナルは200日線より上の銘柄だけ。損切り目安 = 終値 − 2×ATR14、利確目安 = 2R。"
        "ファンダメンタルズ（決算・財務）は TradingView 版で確認してください。投資判断はご自身で。"
    )
    return "\n".join(lines) + "\n"


def main(argv: Optional[List[str]] = None) -> int:
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    parser = argparse.ArgumentParser(description="日本株スイングのシグナル一覧を作る")
    parser.add_argument("--prices", default=os.path.join(root, "prices.csv"))
    parser.add_argument("--out", default=os.path.join(root, "signals.csv"))
    parser.add_argument("--markdown", default=os.path.join(root, "signals.md"))
    parser.add_argument("--summary", default="", help="追記先（GitHub Actions の $GITHUB_STEP_SUMMARY など）")
    args = parser.parse_args(argv)

    rows = build_rows(args.prices, SwingParams())
    write_csv(rows, args.out)
    md = build_markdown(rows)
    if args.markdown:
        with open(args.markdown, "w", encoding="utf-8") as f:
            f.write(md)
    if args.summary:
        with open(args.summary, "a", encoding="utf-8") as f:
            f.write(md)

    n_signals = sum(1 for r in rows if r["_setup"])
    print(f"{len(rows)} 銘柄を判定、新規シグナル {n_signals} 件 → {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
