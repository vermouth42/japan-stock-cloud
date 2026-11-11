"""Read-only repository/Sheets comparison. An audit never authorizes an order."""
from __future__ import annotations

import argparse
import csv
import json
import math
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path

from .market_data import Calendar, JST, HEADER, validate_dataset
from .sheet_rows import prepare


def _sheet_key(row):
    serial, code = row[0], row[2]
    if type(serial) not in (int, float) or not math.isfinite(serial) or serial != int(serial):
        raise ValueError("invalid sheet date")
    day = date(1899, 12, 30) + timedelta(days=int(serial))
    if type(code) in (int, float):
        if not math.isfinite(code) or code != int(code):
            raise ValueError("invalid sheet code")
        code = str(int(code))
    if not isinstance(code, str) or not code:
        raise ValueError("missing sheet code")
    return day.isoformat(), code


def _same_cell(left, right):
    if left in (None, "") or right in (None, ""):
        return left in (None, "") and right in (None, "")
    if type(left) in (int, float) and type(right) in (int, float):
        return math.isfinite(left) and math.isfinite(right) and math.isclose(left, right, rel_tol=0, abs_tol=1e-8)
    return type(left) is type(right) and left == right


def audit(status: dict, repo_rows: list[list], sheet_rows: list[list], codes: list[str], now: datetime) -> dict:
    if now.tzinfo is None:
        raise ValueError("timezone-aware clock required")
    now = now.astimezone(JST)
    result = dict(checked_at_jst=now.isoformat(timespec="seconds"), go_allowed=False,
                  data_ready=False, expected_market_date=None,
                  repository_market_date=status.get("latest_market_date"), sheet_market_date=None,
                  repository_rows=len(repo_rows), sheet_rows=len(sheet_rows),
                  sheet_stocks=0, sheet_duplicate_keys=0, exact_import_match=False)
    reasons = []
    try:
        expected = Calendar().latest_complete(now).isoformat()
        result["expected_market_date"] = expected
    except ValueError:
        expected = None
        reasons.append("CALENDAR_COVERAGE_MISSING")
    if len(codes) != 100 or len(set(codes)) != 100:
        reasons.append("WATCHLIST_INVALID")
    try:
        updated = datetime.fromisoformat(status["updated_at_jst"])
        if updated.tzinfo is None or updated > now:
            raise ValueError("invalid publication timestamp")
        cutoff = date.fromisoformat(status["latest_market_date"])
        if cutoff.isoformat() != status["latest_market_date"]:
            raise ValueError("noncanonical date")
        if not all(type(status.get(k)) is int and status[k] == v for k, v in
                   (("stocks_total", 100), ("stocks_success", 100), ("stocks_failed", 0), ("rows", len(repo_rows)))) or status.get("failed") != []:
            raise ValueError("status mismatch")
        validate_dataset(repo_rows, codes, cutoff)
        for row in repo_rows:
            o, h, lo, c, volume = [float(row[i]) for i in (2, 3, 4, 5, 10)]
            if not all(math.isfinite(x) for x in (o, h, lo, c, volume)) or min(o, h, lo, c) <= 0 or volume < 0 or volume != int(volume) or not lo <= min(o, c) <= max(o, c) <= h:
                raise ValueError("invalid OHLCV")
        planned = prepare(repo_rows, codes, cutoff)
    except (ValueError, KeyError, TypeError, IndexError, OverflowError):
        planned = None
        reasons.append("REPOSITORY_INVALID")
    if expected and status.get("latest_market_date") != expected:
        reasons.append("REPOSITORY_STALE_OR_FUTURE")
    try:
        if any(not isinstance(r, list) or not 3 <= len(r) <= 18 for r in sheet_rows):
            raise ValueError("invalid row shape")
        keyed = [(_sheet_key(r), r + [None] * (18 - len(r))) for r in sheet_rows]
        counts = Counter(key[1] for key, _ in keyed)
        latest = {code: max(key[0] for key, _ in keyed if key[1] == code) for code in counts}
        result.update(sheet_market_date=max(latest.values(), default=None), sheet_stocks=len(counts),
                      sheet_duplicate_keys=len(keyed) - len({key for key, _ in keyed}))
        if len(sheet_rows) != 4500 or set(counts) != set(codes) or set(counts.values()) != {45} or result["sheet_duplicate_keys"]:
            reasons.append("SHEET_INTEGRITY")
        if expected and (not latest or any(day != expected for day in latest.values())):
            reasons.append("SHEET_STALE_OR_FUTURE")
        if planned is not None:
            source = sorted(((_sheet_key(r), r) for r in planned), key=lambda x: x[0])
            target = sorted(keyed, key=lambda x: x[0])
            result["exact_import_match"] = len(source) == len(target) and all(
                sk == tk and all(_same_cell(a, b) for a, b in zip(sr, tr))
                for (sk, sr), (tk, tr) in zip(source, target))
            if not result["exact_import_match"]:
                reasons.append("IMPORT_CONTENT_MISMATCH")
    except (ValueError, TypeError, IndexError, OverflowError):
        reasons.append("SHEET_INVALID")
    result["reasons"] = list(dict.fromkeys(reasons))
    result["data_ready"] = not reasons
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--status", required=True, type=Path)
    parser.add_argument("--prices", required=True, type=Path)
    parser.add_argument("--sheet-rows", required=True, type=Path, help="A3:R4502 UNFORMATTED_VALUE JSON array; no credentials")
    parser.add_argument("--now", help="optional explicit timezone-aware audit timestamp")
    args = parser.parse_args()
    with args.prices.open(encoding="utf-8-sig", newline="") as file:
        reader = csv.reader(file)
        if next(reader) != HEADER:
            raise ValueError("CSV schema mismatch")
        rows = list(reader)
    result = audit(json.loads(args.status.read_text()), rows, json.loads(args.sheet_rows.read_text()),
                   json.loads(Path(__file__).with_name("watchlist.json").read_text()),
                   datetime.fromisoformat(args.now) if args.now else datetime.now(JST))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result["data_ready"] else 2)


if __name__ == "__main__":
    main()
