"""Yahoo Finance から長期間の日足を取得して、バックテスト用の CSV を作る。

使い方:
    python python/fetch_history.py                    # prices.csv の100銘柄 × 10年 → data/history.csv
    python python/fetch_history.py --years 15
    python python/fetch_history.py --codes 7203 6758  # 銘柄を指定

出力は prices.csv と同じ列名（日付,コード,始値,高値,安値,終値,出来高）なので、
backtest.py の --prices にそのまま渡せる。日経平均（^N225）は data/market.csv に別に保存する。

株式分割: Yahoo の価格は通常は分割調整済みだが、念のため分割イベントの前後で値段が
分割比率どおりに飛んでいないかを確かめ、飛んでいれば分割前の価格を調整する。
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Sequence, Tuple

JST = timezone(timedelta(hours=9))
HEADER = ["日付", "コード", "始値", "高値", "安値", "終値", "出来高"]
MARKET_SYMBOL = "^N225"

Row = Dict[str, float]


def download_json(url: str, retries: int = 3) -> dict:
    last_error: Optional[Exception] = None
    for attempt in range(retries):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.loads(response.read().decode("utf-8"))
        except Exception as error:  # noqa: BLE001 - 通信エラーはまとめて再試行
            last_error = error
            time.sleep(2 ** attempt)
    raise RuntimeError(f"download failed: {last_error}")


def chart_url(symbol: str, years: float, now: Optional[datetime] = None) -> str:
    now = now or datetime.now(timezone.utc)
    start = now - timedelta(days=int(years * 365.25) + 5)
    return (
        "https://query1.finance.yahoo.com/v8/finance/chart/"
        + urllib.parse.quote(symbol)
        + f"?period1={int(start.timestamp())}&period2={int(now.timestamp())}"
        + "&interval=1d&events=div%2Csplits&includeAdjustedClose=true"
    )


def parse_chart(data: dict) -> Tuple[List[Row], List[Tuple[str, float]]]:
    """チャート API の JSON → (日足のリスト, [(分割日, 比率)])。"""
    results = (data.get("chart") or {}).get("result")
    if not results:
        error = (data.get("chart") or {}).get("error")
        raise RuntimeError(f"Yahoo Finance returned no result: {error}")
    result = results[0]
    timestamps = result.get("timestamp") or []
    quote = ((result.get("indicators") or {}).get("quote") or [{}])[0]
    opens = quote.get("open") or []
    highs = quote.get("high") or []
    lows = quote.get("low") or []
    closes = quote.get("close") or []
    volumes = quote.get("volume") or []

    rows: List[Row] = []
    seen = set()
    for i, ts in enumerate(timestamps):
        if i >= len(closes):
            break
        o = opens[i] if i < len(opens) else None
        h = highs[i] if i < len(highs) else None
        lo = lows[i] if i < len(lows) else None
        c = closes[i]
        if o is None or h is None or lo is None or c is None:
            continue
        day = datetime.fromtimestamp(ts, timezone.utc).astimezone(JST).date().isoformat()
        if day in seen:  # 当日分が重複して返ることがある
            rows[-1] = _row(day, o, h, lo, c, volumes, i)
            continue
        seen.add(day)
        rows.append(_row(day, o, h, lo, c, volumes, i))

    splits: List[Tuple[str, float]] = []
    for ev in ((result.get("events") or {}).get("splits") or {}).values():
        num = ev.get("numerator")
        den = ev.get("denominator")
        if not num or not den:
            continue
        day = datetime.fromtimestamp(ev["date"], timezone.utc).astimezone(JST).date().isoformat()
        splits.append((day, float(num) / float(den)))
    splits.sort()
    return rows, splits


def _row(day: str, o: float, h: float, lo: float, c: float, volumes: Sequence, i: int) -> Row:
    v = volumes[i] if i < len(volumes) and volumes[i] is not None else 0
    return {"date": day, "open": float(o), "high": float(h), "low": float(lo), "close": float(c), "volume": float(v)}  # type: ignore[dict-item]


def adjust_splits(rows: List[Row], splits: Sequence[Tuple[str, float]]) -> List[str]:
    """分割日に価格が分割比率どおりに飛んでいたら（未調整なら）、分割前の価格を調整する。"""
    notes: List[str] = []
    for day, ratio in splits:
        if ratio <= 0 or abs(math.log(ratio)) < math.log(1.5):
            continue  # 1.5倍未満の分割は判定が難しいので触らない
        j = next((k for k, r in enumerate(rows) if r["date"] >= day), None)  # type: ignore[operator]
        if j is None or j == 0:
            continue
        jump = rows[j - 1]["close"] / rows[j]["open"] if rows[j]["open"] else 0
        if jump > 0 and abs(math.log(jump / ratio)) < math.log(1.5):
            for r in rows[:j]:
                for key in ("open", "high", "low", "close"):
                    r[key] = r[key] / ratio
                r["volume"] = r["volume"] * ratio
            notes.append(f"{day} 分割 1:{ratio:g} を調整")
        else:
            notes.append(f"{day} 分割 1:{ratio:g} は調整済み")
    return notes


def fetch(symbol: str, years: float) -> Tuple[List[Row], List[str]]:
    rows, splits = parse_chart(download_json(chart_url(symbol, years)))
    notes = adjust_splits(rows, splits)
    return rows, notes


def number(value: float) -> str:
    return f"{value:.6f}".rstrip("0").rstrip(".")


def write_rows(path: str, data: Dict[str, List[Row]]) -> int:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    count = 0
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(HEADER)
        for code, rows in data.items():
            for r in rows:
                writer.writerow([
                    r["date"], code, number(r["open"]), number(r["high"]),
                    number(r["low"]), number(r["close"]), str(int(round(r["volume"]))),
                ])
                count += 1
    return count


def codes_from_prices(path: str) -> List[str]:
    with open(path, encoding="utf-8-sig", newline="") as f:
        return sorted({row["コード"] for row in csv.DictReader(f) if row.get("コード")})


def main(argv: Optional[List[str]] = None) -> int:
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    parser = argparse.ArgumentParser(description="長期間の日足を Yahoo Finance から取得する")
    parser.add_argument("--years", type=float, default=10.0)
    parser.add_argument("--codes", nargs="*", help="銘柄コード（省略時は prices.csv の銘柄）")
    parser.add_argument("--prices", default=os.path.join(root, "prices.csv"), help="銘柄リストの取得元")
    parser.add_argument("--out", default=os.path.join(root, "data", "history.csv"))
    parser.add_argument("--market-out", default=os.path.join(root, "data", "market.csv"))
    parser.add_argument("--sleep", type=float, default=0.3, help="リクエストの間隔（秒）")
    args = parser.parse_args(argv)
    if not 0 < args.years <= 30:
        parser.error("--years は 0〜30")

    codes = args.codes or codes_from_prices(args.prices)
    data: Dict[str, List[Row]] = {}
    failed: List[str] = []
    for n, code in enumerate(codes, 1):
        try:
            rows, notes = fetch(code + ".T", args.years)
            if not rows:
                raise RuntimeError("no rows")
            data[code] = rows
            extra = f" / {'; '.join(notes)}" if notes else ""
            print(f"[{n}/{len(codes)}] OK {code} {len(rows)}本 {rows[0]['date']}〜{rows[-1]['date']}{extra}")
        except Exception as error:  # noqa: BLE001
            failed.append(code)
            print(f"[{n}/{len(codes)}] FAILED {code}: {error}")
        time.sleep(args.sleep)

    if not data:
        print("全銘柄の取得に失敗しました", file=sys.stderr)
        return 1
    count = write_rows(args.out, data)
    print(f"{len(data)}銘柄・{count}行 → {args.out}（失敗 {len(failed)}: {' '.join(failed)}）")

    try:
        rows, _ = fetch(MARKET_SYMBOL, args.years)
        write_rows(args.market_out, {"N225": rows})
        print(f"日経平均 {len(rows)}本 → {args.market_out}")
    except Exception as error:  # noqa: BLE001
        print(f"日経平均の取得に失敗（地合いフィルターなしで検証してください）: {error}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
