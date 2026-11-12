"""Offline validation contract; no market fetching, Sheets writes, or order API.

The caller must supply independently verified JPX/session/event evidence.
This module does not make unverified inputs trustworthy and is not deployed.
"""
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from math import isfinite
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")


@dataclass(frozen=True)
class Snapshot:
    symbol: str = ""
    rank_symbol: str = ""
    sbi_symbol: str = ""
    rank: str = ""
    rank_checked_at: datetime | None = None
    market_date: date | None = None
    expected_market_date: date | None = None
    trading_day_verified: bool = False
    data_verified: bool = False
    stocks: int = 0
    rows: int = 0
    duplicates: int = -1
    sbi_checked_at: datetime | None = None
    event_checked_at: datetime | None = None
    # None is unknown. 0..3 blocks. 4 means clearance THROUGH next 3 JPX sessions.
    event_business_days: int | None = None
    event_evidence: str = ""
    current_price: float = 0
    vwap: float = 0
    stop: float = 0
    allowed_loss: float = 0
    remaining_risk_budget: float = 0
    available_cash: float = 0
    recommended_shares: int = 0
    cost_per_share: float = 0
    volume_ok: bool = False
    wick_ok: bool = False
    gap_ok: bool = False
    board_ok: bool = False


@dataclass(frozen=True)
class Decision:
    go_allowed: bool
    shares: int
    planned_loss: float
    reasons: tuple[str, ...]


def evaluate(s: Snapshot, now: datetime, watchlist: set[str]) -> Decision:
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    now = now.astimezone(JST)
    reasons = []

    def same_day(ts):
        return isinstance(ts, datetime) and ts.tzinfo is not None and ts <= now and ts.astimezone(JST).date() == now.date()

    if len(watchlist) != 100 or s.symbol not in watchlist or not (s.symbol == s.rank_symbol == s.sbi_symbol):
        reasons.append("SYMBOL_OR_WATCHLIST")
    if s.rank not in {"A", "A+"} or not same_day(s.rank_checked_at):
        reasons.append("RANK_OR_DATE")
    if s.trading_day_verified is not True or not s.expected_market_date or not s.market_date or s.market_date != s.expected_market_date or s.market_date >= now.date():
        reasons.append("MARKET_DATE")
    if s.data_verified is not True or s.stocks != 100 or s.rows != 4500 or s.duplicates != 0:
        reasons.append("DATA_INTEGRITY")
    # Provisional operational TTL: 5 minutes, stricter than merely same day.
    if not same_day(s.sbi_checked_at) or now - s.sbi_checked_at > timedelta(minutes=5):
        reasons.append("SBI_CONFIRMATION_EXPIRED")
    if not same_day(s.event_checked_at) or not s.event_evidence or type(s.event_business_days) is not int or s.event_business_days <= 3:
        reasons.append("EVENT_UNKNOWN_OR_NEAR")
    if not all(v is True for v in (s.volume_ok, s.wick_ok, s.gap_ok, s.board_ok)):
        reasons.append("MANUAL_CHECKS")
    numbers = (s.current_price, s.vwap, s.stop, s.allowed_loss, s.remaining_risk_budget, s.available_cash, s.cost_per_share)
    valid_numbers = all(type(v) in (int, float) and isfinite(v) for v in numbers)
    if not valid_numbers or min(numbers[:-1]) <= 0 or s.cost_per_share < 0:
        reasons.append("INVALID_NUMBERS")
    elif not (s.stop < s.current_price and s.current_price >= s.vwap):
        reasons.append("VWAP_OR_STOP")
    if type(s.recommended_shares) is not int or s.recommended_shares < 100 or s.recommended_shares % 100:
        reasons.append("LOT_SIZE")
    if reasons:
        return Decision(False, 0, 0, tuple(reasons))
    unit_risk = s.current_price - s.stop + s.cost_per_share
    budget = min(s.allowed_loss, s.remaining_risk_budget)
    shares = min(s.recommended_shares, int(budget // (unit_risk * 100)) * 100, int(s.available_cash // ((s.current_price + s.cost_per_share) * 100)) * 100)
    if shares < 100:
        return Decision(False, 0, 0, ("INSUFFICIENT_RISK_OR_CASH",))
    return Decision(True, shares, shares * unit_risk, ())
