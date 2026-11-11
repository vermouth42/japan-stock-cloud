"""Offline rehearsal for automatic inputs; no feeds, notifications or orders.

Source labels are a schema check, NOT authentication. A future adapter must
establish provenance, permissions and completeness. Even passing synthetic
evidence always returns go_allowed=False. Existing manual gates are unchanged.
"""
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation, ROUND_FLOOR

from .market_data import Calendar, JST
from .readiness import entry_session


@dataclass(frozen=True)
class Evidence:
    source: str
    symbol: str
    observed_at: datetime
    received_at: datetime
    expires_at: datetime
    reference: str
    values: dict = field(default_factory=dict)


@dataclass(frozen=True)
class RiskPlan:
    stop: object = None
    allowed_loss: object = None
    cost_per_share: object = None  # Explicit zero is allowed; missing is not.


SOURCES = {"market": "TradingView", "rank": "Ver7.5", "events": "EventCalendar",
           "account": "SBI"}
# Provisional ceilings, not measured provider latency guarantees.
TTL = {"market": 90, "rank": 8 * 3600, "events": 30 * 60,
       "account": 30, "board": 10}
FIELDS = {
    "market": {"price", "vwap", "volume_ok", "wick_ok", "gap_ok"},
    "rank": {"grade", "recommended_shares"},
    "events": {"coverage_until", "issuer_checked", "macro_checked", "dates"},
    "account": {"available_cash", "remaining_risk", "position_shares",
                "pending_buy_shares", "pending_sell_shares", "positions_complete",
                "orders_complete"},
    "board": {"board_ok"},
}


def aware(value):
    return isinstance(value, datetime) and value.tzinfo is not None and value.utcoffset() is not None


def money(value):
    if type(value) not in (int, float, str, Decimal):
        raise ValueError("invalid money")
    try:
        result = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError("invalid money") from exc
    if not result.is_finite():
        raise ValueError("nonfinite money")
    return result


def assess(symbol, watchlist, evidence, risk, now, *, sync_report=None,
           board_source=None):
    """Evaluate five separate evidence streams for a NEW cash entry only.

    sync_report is the result of sync_audit.audit, not a user editable flag.
    Caller supplied JSON is untrusted until real adapters are independently
    validated. This function cannot authorize production even with all fields.
    """
    if not aware(now):
        raise ValueError("timezone-aware evaluation clock required")
    now = now.astimezone(JST)
    reasons, values, deadlines = [], {}, []
    session_end = time(11, 25) if now.hour < 12 else time(14, 55)
    deadlines.append(datetime.combine(now.date(), session_end, JST))
    if (not isinstance(watchlist, (list, tuple, set)) or len(watchlist) != 100
            or not all(isinstance(x, str) for x in watchlist)
            or len(set(watchlist)) != 100 or symbol not in watchlist):
        reasons.append("SYMBOL_OR_WATCHLIST")
    try:
        if not entry_session(now):
            reasons.append("OUTSIDE_ENTRY_SESSION")
    except ValueError:
        reasons.append("CALENDAR_COVERAGE_MISSING")
    # Require the audit's market date to match this evaluation, not yesterday's
    # successful cached audit. Raw data authentication remains an adapter task.
    try:
        expected = Calendar().latest_complete(now).isoformat()
        if (not isinstance(sync_report, dict) or sync_report.get("data_ready") is not True
                or sync_report.get("expected_market_date") != expected
                or sync_report.get("sheet_stocks") != 100
                or sync_report.get("sheet_rows") != 4500
                or sync_report.get("sheet_duplicate_keys") != 0
                or sync_report.get("reasons") != []):
            reasons.append("SYNC_NOT_VERIFIED")
    except ValueError:
        reasons.append("SYNC_NOT_VERIFIED")
    if not isinstance(evidence, dict):
        evidence = {}
    if set(evidence) - set(FIELDS):
        reasons.append("UNKNOWN_EVIDENCE_STREAM")
    for kind, fields in FIELDS.items():
        item = evidence.get(kind)
        source = board_source if kind == "board" else SOURCES[kind]
        prefix = kind.upper()
        if not source:
            reasons.append(prefix + "_SOURCE_NOT_CONFIGURED")
        if not isinstance(item, Evidence):
            reasons.append(prefix + "_MISSING")
            continue
        if (item.source != source or item.symbol != symbol
                or not isinstance(item.reference, str) or not item.reference.strip()):
            reasons.append(prefix + "_PROVENANCE")
        times = (item.observed_at, item.received_at, item.expires_at)
        if (not all(aware(x) for x in times)
                or not item.observed_at <= item.received_at <= now < item.expires_at
                or item.observed_at.astimezone(JST).date() != now.date()
                or now - item.observed_at >= timedelta(seconds=TTL[kind])):
            reasons.append(prefix + "_STALE_OR_INVALID_TIME")
        else:
            deadlines.append(min(item.expires_at, item.observed_at + timedelta(seconds=TTL[kind])))
        if not isinstance(item.values, dict) or set(item.values) != fields:
            reasons.append(prefix + "_SCHEMA")
        else:
            values[kind] = item.values

    fast = [evidence.get(k) for k in ("market", "board", "account")]
    if all(isinstance(x, Evidence) and aware(x.observed_at) for x in fast):
        stamps = [x.observed_at for x in fast]
        if max(stamps) - min(stamps) > timedelta(seconds=15):
            reasons.append("MARKET_BOARD_ACCOUNT_TIME_SKEW")

    # Never reinterpret a missing stream as normal values.
    if set(values) == set(FIELDS):
        m, r, ev, a, b = (values[k] for k in ("market", "rank", "events", "account", "board"))
        if r["grade"] not in ("A", "A+"):
            reasons.append("RANK_NOT_A")
        if type(r["recommended_shares"]) is not int or r["recommended_shares"] < 100 or r["recommended_shares"] % 100:
            reasons.append("RECOMMENDED_LOT_INVALID")
        if not all(x is True for x in (m["volume_ok"], m["wick_ok"], m["gap_ok"], b["board_ok"])):
            reasons.append("TECHNICAL_OR_BOARD_INCOMPLETE")
        if a["positions_complete"] is not True or a["orders_complete"] is not True:
            reasons.append("ACCOUNT_COVERAGE_INCOMPLETE")
        quantities = [a[k] for k in ("position_shares", "pending_buy_shares", "pending_sell_shares")]
        if any(type(x) is not int or x < 0 for x in quantities):
            reasons.append("ACCOUNT_QUANTITY_INVALID")
        elif any(quantities):
            # Pyramiding/credit and sell decisions need a separate portfolio policy.
            reasons.append("EXISTING_POSITION_OR_ORDER")
        try:
            horizon = now.date()
            count = 0
            while count < 3:
                horizon += timedelta(days=1)
                count += Calendar().trading(horizon)
            dates = ev["dates"]
            if (ev["issuer_checked"] is not True or ev["macro_checked"] is not True
                    or type(ev["coverage_until"]) is not date or ev["coverage_until"] < horizon
                    or not isinstance(dates, (list, tuple)) or any(type(d) is not date for d in dates)):
                reasons.append("EVENT_COVERAGE_INCOMPLETE")
            elif any(now.date() <= d <= horizon for d in dates):
                reasons.append("EVENT_WITHIN_3_SESSIONS")
        except (ValueError, OverflowError):
            reasons.append("EVENT_CALENDAR_UNAVAILABLE")
        try:
            price, vwap, stop, budget, remaining, cash, cost = map(money, (
                m["price"], m["vwap"], risk.stop, risk.allowed_loss,
                a["remaining_risk"], a["available_cash"], risk.cost_per_share))
            if min(price, vwap, stop, budget, remaining, cash) <= 0 or cost < 0:
                raise ValueError("nonpositive budget or price")
            if not stop < price or price < vwap:
                reasons.append("VWAP_OR_STOP")
        except (ValueError, AttributeError):
            reasons.append("INVALID_PRICE_OR_RISK")
    shares, planned_loss = 0, Decimal(0)
    if not reasons:
        unit_risk = price - stop + cost
        risk_lots = int((min(budget, remaining) / (unit_risk * 100)).to_integral_value(rounding=ROUND_FLOOR))
        cash_lots = int((cash / ((price + cost) * 100)).to_integral_value(rounding=ROUND_FLOOR))
        shares = min(r["recommended_shares"], 100 * risk_lots, 100 * cash_lots)
        if shares < 100:
            reasons.append("INSUFFICIENT_RISK_OR_CASH")
            shares = 0
        else:
            planned_loss = shares * unit_risk
    return {
        "mode": "AUTOMATIC_INPUT_OFFLINE_REHEARSAL", "go_allowed": False,
        "offline_checks_passed": not reasons,
        "proposed_shares": shares, "planned_loss": str(planned_loss),
        "valid_until": min(deadlines).isoformat() if not reasons else None,
        "reasons": list(dict.fromkeys(reasons)),
        "live_blockers": ["SOURCE_ADAPTERS_NOT_CONNECTED", "DATA_USE_PERMISSION_NOT_VERIFIED",
                          "LIVE_END_TO_END_NOT_VALIDATED"],
    }
