"""Complete-session daily data. Network/outputs run only via explicit CLI."""
from __future__ import annotations
import argparse
import csv
import json
import math
import os
import sys
import time
import urllib.request
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")
ROOT = Path(__file__).resolve().parent
HEADER = ["日付", "コード", "始値", "高値", "安値", "終値", "5日株価平均", "25日株価平均", "75日株価平均", "SBI VWAP", "出来高", "5日平均出来高", "25日平均出来高", "出来高倍率", "TR", "ATR14", "ATR%"]


class Calendar:
    def __init__(self):
        self.spec = json.loads((ROOT / "jpx_calendar.json").read_text())
        self.closed = set(self.spec["closed_dates"])

    def trading(self, day: date) -> bool:
        if not self.spec["first_year"] <= day.year <= self.spec["last_year"]:
            raise ValueError("JPX calendar coverage missing")
        return day.weekday() < 5 and day.isoformat() not in self.closed

    def latest_complete(self, now: datetime) -> date:
        if now.tzinfo is None:
            raise ValueError("timezone-aware timestamp required")
        now = now.astimezone(JST)
        # Keep existing 18:00 publication buffer; exclude all newer/intraday bars.
        day = now.date() if now.hour >= 18 else now.date() - timedelta(days=1)
        while not self.trading(day):
            day -= timedelta(days=1)
        return day

    def sessions_until(self, today: date, event: date) -> int:
        if event < today:
            return -1
        return sum(self.trading(today + timedelta(days=i)) for i in range(1, (event-today).days + 1))


def parse_chart(payload: dict, cutoff: date) -> list[dict]:
    results = (payload.get("chart") or {}).get("result")
    if not results:
        raise ValueError("market source returned no result")
    src = results[0]
    quote = src["indicators"]["quote"][0]
    rows, seen = [], set()
    for i, ts in enumerate(src.get("timestamp", [])):
        day = datetime.fromtimestamp(ts, JST).date()
        if day > cutoff:
            continue
        if day in seen:
            raise ValueError("duplicate market day")
        seen.add(day)
        try:
            o, h, lo, c, v = [quote[k][i] for k in ("open", "high", "low", "close", "volume")]
        except (KeyError, IndexError) as exc:
            raise ValueError("missing OHLCV") from exc
        if any(x is None for x in (o, h, lo, c, v)):
            if day == cutoff:
                raise ValueError("incomplete latest OHLCV")
            continue
        if not all(type(x) in (int,float) and math.isfinite(x) for x in (o,h,lo,c,v)) or min(o,h,lo,c) <= 0 or v < 0 or v != int(v) or not lo <= min(o,c) <= max(o,c) <= h:
            raise ValueError("invalid OHLCV")
        rows.append(dict(date=day.isoformat(), open=o, high=h, low=lo, close=c, volume=int(v)))
    rows.sort(key=lambda r:r["date"])
    if not rows or rows[-1]["date"] != cutoff.isoformat():
        raise ValueError("latest completed session missing")
    return rows


def fetch(code: str, now: datetime, cutoff: date) -> list[dict]:
    start = now - timedelta(days=450)
    query = urllib.parse.urlencode(dict(period1=int(start.timestamp()), period2=int(now.timestamp()), interval="1d", events="history", includeAdjustedClose="true"))
    url = "https://query1.finance.yahoo.com/v8/finance/chart/" + urllib.parse.quote(code + ".T") + "?" + query
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers={"User-Agent":"Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=20) as response:
                return parse_chart(json.load(response), cutoff)
        except (OSError, ValueError, KeyError):
            if attempt == 2:
                raise
            time.sleep(2 ** attempt)
    raise RuntimeError("unreachable")


def calculate(code: str, bars: list[dict]) -> list[list]:
    # Preserve Ver.7.5 SMA-based ATR and 17-column compatibility.
    closes, volumes, trs, output = [], [], [], []
    def avg(xs,n): return sum(xs[-n:])/n if len(xs)>=n else ""
    def num(x): return "" if x=="" else f"{float(x):.6f}".rstrip("0").rstrip(".")
    previous = None
    for r in bars:
        c,v=r["close"],r["volume"]
        tr=r["high"]-r["low"] if previous is None else max(r["high"]-r["low"],abs(r["high"]-previous),abs(r["low"]-previous))
        closes.append(c); volumes.append(v); trs.append(tr)
        v25,atr=avg(volumes,25),avg(trs,14)
        output.append([r["date"],code,num(r["open"]),num(r["high"]),num(r["low"]),num(c),num(avg(closes,5)),num(avg(closes,25)),num(avg(closes,75)),"",str(v),num(avg(volumes,5)),num(v25),num(v/v25 if v25 not in ("",0) else ""),num(tr),num(atr),num(atr/c*100 if atr!="" else "")])
        previous=c
    return output


def validate_dataset(rows: list[list], codes: list[str], cutoff: date) -> None:
    if len(codes)!=100 or len(set(codes))!=100:
        raise ValueError("watchlist must contain exactly 100 unique codes")
    keys, latest, counts = set(), {}, {}
    for r in rows:
        if len(r)!=17 or r[1] not in codes or r[0]>cutoff.isoformat():
            raise ValueError("unexpected row or market date")
        if date.fromisoformat(r[0]).isoformat() != r[0]:
            raise ValueError("noncanonical market date")
        key=(r[0],r[1])
        if key in keys:
            raise ValueError("duplicate row")
        keys.add(key);latest[r[1]]=max(latest.get(r[1],""),r[0]); counts[r[1]]=counts.get(r[1],0)+1
    if set(latest)!=set(codes) or any(d!=cutoff.isoformat() for d in latest.values()) or min(counts.values(),default=0)<45:
        raise ValueError("incomplete watchlist/session/history")


def build(codes: list[str], now: datetime, getter=fetch, progress=None) -> tuple[list[list],dict]:
    cutoff=Calendar().latest_complete(now)
    if len(codes)!=100 or len(set(codes))!=100:
        raise ValueError("invalid watchlist")
    rows=[]
    pool = ThreadPoolExecutor(max_workers=4)
    try:
        futures={pool.submit(getter,c,now,cutoff):c for c in codes}
        for completed, future in enumerate(as_completed(futures), 1):
            code=futures[future]
            try:
                rows.extend(calculate(code,future.result()))
            except Exception as exc:
                # Do not leak upstream response bodies, URLs or credentials.
                raise RuntimeError(f"data acquisition failed for {code}: {type(exc).__name__}") from None
            if progress is not None:
                progress(completed, len(codes), code)
    finally:
        # Cancel work that has not started. Running requests finish under their
        # existing transport timeouts; cancellation is not an instant deadline.
        pool.shutdown(wait=True, cancel_futures=True)
    validate_dataset(rows,codes,cutoff)
    rows.sort(key=lambda r:(r[0],r[1]),reverse=True)
    status=dict(updated_at_jst=now.astimezone(JST).isoformat(timespec="seconds"),latest_market_date=cutoff.isoformat(),stocks_total=100,stocks_success=100,stocks_failed=0,rows=len(rows),failed=[])
    return rows,status


def write_outputs(directory: Path, rows: list[list], status: dict) -> None:
    # Stage all files; the workflow commits the complete set only after success.
    directory.mkdir(parents=True,exist_ok=True)
    latest=[];seen=set()
    for r in rows:
        if r[1] not in seen:
            seen.add(r[1]);latest.append(r[:13])
    for name,header,data in [("prices.csv",HEADER,rows),("latest.csv",None,latest)]:
        with (directory/(name+".tmp")).open("w",encoding="utf-8-sig",newline="") as f:
            w=csv.writer(f)
            if header:w.writerow(header)
            w.writerows(data)
    (directory/"status.json.tmp").write_text(json.dumps(status,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    for name in ("prices.csv","latest.csv","status.json"):
        os.replace(directory/(name+".tmp"),directory/name)


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--out",type=Path,required=True,help="staging output directory")
    args=ap.parse_args()
    now = datetime.now(JST)
    print(f"Fetching 100 stocks through {Calendar().latest_complete(now)}", file=sys.stderr, flush=True)
    def progress(completed, total, code):
        print(f"Fetched {completed}/{total}: {code}", file=sys.stderr, flush=True)
    rows,status=build(json.loads((ROOT/"watchlist.json").read_text()),now,progress=progress)
    write_outputs(args.out,rows,status)
    print(json.dumps(status,ensure_ascii=False))


if __name__=="__main__":main()
