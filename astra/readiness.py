"""Offline integration rehearsal. Never writes to Sheets, alerts or an order API.

Raw repository/Sheet data are audited here, rather than accepting a caller's
data_verified flag. Manual evidence remains unverified: even a passing rehearsal
does not grant live GO. No authentication values belong in the input JSON.
"""
from __future__ import annotations

import argparse
import csv
import json
from dataclasses import fields, replace
from datetime import date, datetime, time
from pathlib import Path

from .market_data import Calendar, HEADER, JST, ROOT
from .preflight import Snapshot, evaluate
from .sync_audit import audit

DERIVED = {"market_date", "expected_market_date", "trading_day_verified",
           "data_verified", "stocks", "rows", "duplicates"}
TIMESTAMPS = {"rank_checked_at", "sbi_checked_at", "event_checked_at"}


def parse_manual(payload: dict) -> Snapshot:
    allowed = {field.name for field in fields(Snapshot)} - DERIVED
    if not isinstance(payload, dict) or set(payload) - allowed:
        raise ValueError("unknown or derived input fields")
    parsed = dict(payload)
    for key in TIMESTAMPS & parsed.keys():
        if parsed[key] is not None:
            parsed[key] = datetime.fromisoformat(parsed[key])
            if parsed[key].tzinfo is None:
                raise ValueError("manual timestamp requires timezone")
    return Snapshot(**parsed)


def entry_session(now: datetime) -> bool:
    """Provisional, deliberately narrower windows shared with candidate Pine."""
    if now.tzinfo is None:
        raise ValueError("timezone-aware clock required")
    now = now.astimezone(JST)
    clock = now.time().replace(tzinfo=None)
    return Calendar().trading(now.date()) and (
        time(9, 5) <= clock < time(11, 25) or time(12, 30) <= clock < time(14, 55))


def assess(status: dict, repo_rows: list[list], sheet_rows: list[list],
           codes: list[str], manual: dict, now: datetime) -> dict:
    if now.tzinfo is None:
        raise ValueError("timezone-aware clock required")
    now = now.astimezone(JST)
    synced = audit(status, repo_rows, sheet_rows, codes, now)
    reasons = list(synced["reasons"])
    shares, planned_loss = 0, 0
    try:
        session = entry_session(now)
    except ValueError:
        session = False
    if not session:
        reasons.append("OUTSIDE_VALIDATED_ENTRY_SESSION")
    try:
        snapshot = replace(
            parse_manual(manual),
            market_date=date.fromisoformat(status["latest_market_date"]),
            expected_market_date=date.fromisoformat(synced["expected_market_date"]),
            trading_day_verified=session, data_verified=synced["data_ready"],
            stocks=synced["sheet_stocks"], rows=synced["sheet_rows"],
            duplicates=synced["sheet_duplicate_keys"])
        decision = evaluate(snapshot, now, set(codes))
        reasons.extend(decision.reasons)
        shares, planned_loss = decision.shares, decision.planned_loss
    except (TypeError, ValueError, KeyError, OverflowError):
        reasons.append("MANUAL_INPUT_INVALID")
    ready = not reasons
    return {
        "mode": "OFFLINE_REHEARSAL", "checked_at_jst": now.isoformat(),
        "offline_checks_passed": ready, "go_allowed": False,
        "proposed_shares": shares if ready else 0,
        "planned_loss": planned_loss if ready else 0,
        "reasons": list(dict.fromkeys(reasons)),
        "live_blockers": ["MANUAL_EVIDENCE_SOURCE_NOT_CONNECTED",
                          "LIVE_DEPLOYMENT_NOT_VALIDATED"],
        "sync_audit": synced,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--status", required=True, type=Path)
    parser.add_argument("--prices", required=True, type=Path)
    parser.add_argument("--sheet-rows", required=True, type=Path)
    parser.add_argument("--manual", required=True, type=Path)
    parser.add_argument("--now", help="explicit aware time for replay, never live authorization")
    args = parser.parse_args()
    with args.prices.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle)
        if next(reader) != HEADER:
            raise ValueError("CSV schema mismatch")
        rows = list(reader)
    report = assess(json.loads(args.status.read_text()), rows,
                    json.loads(args.sheet_rows.read_text()),
                    json.loads((ROOT / "watchlist.json").read_text()),
                    json.loads(args.manual.read_text()),
                    datetime.fromisoformat(args.now) if args.now else datetime.now(JST))
    print(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False))
    # This command cannot exit successfully as a live trading authorization.
    raise SystemExit(2)


if __name__ == "__main__":
    main()
