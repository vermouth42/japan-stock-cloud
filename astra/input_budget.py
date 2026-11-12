"""Manual-budget candidate decisions. No broker API, orders or notifications.

Input cash is the user's assumption, never a verified SBI balance. Market,
rank, event and board evidence remain mandatory. Deployment/authentication
belong to adapters; passing this engine alone does not enable production GO.
"""
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_CEILING
import json
import re
import sqlite3

from .automatic_preflight import Evidence, aware, money
from .market_data import Calendar, JST
from .readiness import entry_session


@dataclass(frozen=True)
class Budget:
    cash: object
    risk_rate: object
    revision: str
    source: str = "設定!B2/B3"


def yen(value):
    return int(value.to_integral_value(rounding=ROUND_CEILING))


def size(budget, *, price, stop, cost_per_share, recommended_shares,
         committed_cash=0):
    """Round total requirements UP to yen; round shares DOWN to 100 lots."""
    cash, rate, p, s, cost, used = map(money, (
        budget.cash, budget.risk_rate, price, stop, cost_per_share, committed_cash))
    if (not budget.revision or not budget.source or cash < 0 or used < 0
            or not 0 < rate <= 1 or not 0 < s < p or cost < 0
            or type(recommended_shares) is not int or recommended_shares < 0
            or recommended_shares % 100):
        raise ValueError("INVALID_BUDGET_OR_PRICE")
    loss_limit = cash * rate
    available = max(Decimal(0), cash-used)
    unit_cash, unit_risk = p+cost, p-s+cost
    qty = min(recommended_shares, int(available // (unit_cash*100))*100,
              int(loss_limit // (unit_risk*100))*100)
    # A rounded-up commitment must also fit a fractional-yen input budget.
    while qty and (yen(qty*unit_cash) > available or yen(qty*unit_risk) > loss_limit):
        qty -= 100
    return dict(shares=qty, required_cash=yen(qty*unit_cash),
                planned_loss=yen(qty*unit_risk), loss_limit=str(loss_limit),
                budget_source=budget.source, budget_revision=budget.revision,
                balance_verified=False, holdings_verified=False)


def evaluate(symbol, codes, evidence, budget, *, stop, cost_per_share, now,
             committed_cash=0):
    """Input-budget decision; account evidence is deliberately NOT required.

    Server-owned adapters must provide Evidence objects. A webhook must not
    provide its own rank, event coverage, board clearance or user budget.
    """
    if not aware(now):
        raise ValueError("aware clock required")
    now = now.astimezone(JST)
    reasons, v, deadlines = [], {}, []
    if (len(codes) != 100 or len(set(codes)) != 100 or symbol not in codes):
        reasons.append("WATCHLIST_INVALID")
    try:
        if not entry_session(now):
            reasons.append("OUTSIDE_ENTRY_SESSION")
    except ValueError:
        reasons.append("CALENDAR_UNAVAILABLE")
    specs = {
        "market": ("TradingView", 90, {"price", "vwap", "volume_ok", "wick_ok", "gap_ok"}),
        "rank": ("Ver7.5", 600, {"grade", "recommended_shares", "market_date"}),
        "events": ("EventCalendar", 1800, {"coverage_until", "issuer_checked", "macro_checked", "dates"}),
        "board": ("OrderBook", 10, {"board_ok"}),
    }
    for key, (source, ttl, fields) in specs.items():
        e = evidence.get(key) if isinstance(evidence, dict) else None
        if (not isinstance(e, Evidence) or e.source != source or e.symbol != symbol
                or not isinstance(e.reference, str) or not e.reference.strip()
                or not isinstance(e.values, dict) or set(e.values) != fields):
            reasons.append(key.upper()+"_MISSING_OR_INVALID")
            continue
        if (not all(aware(t) for t in (e.observed_at,e.received_at,e.expires_at))
                or not e.observed_at <= e.received_at <= now < e.expires_at
                or e.observed_at.astimezone(JST).date() != now.date()
                or now-e.observed_at >= timedelta(seconds=ttl)):
            reasons.append(key.upper()+"_STALE")
            continue
        v[key] = e.values
        deadlines.append(min(e.expires_at,e.observed_at+timedelta(seconds=ttl)))
    sized = dict(shares=0,required_cash=0,planned_loss=0)
    if set(v) == set(specs):
        m,r,e,b = (v[k] for k in specs)
        if r['grade'] not in ('A','A+'):
            reasons.append('RANK_NOT_A')
        try:
            if r['market_date'] != Calendar().latest_complete(now).isoformat():
                reasons.append('RANK_MARKET_DATE_STALE')
            horizon = now.date()
            for _ in range(3):
                horizon += timedelta(days=1)
                while not Calendar().trading(horizon):
                    horizon += timedelta(days=1)
            if (e['issuer_checked'] is not True or e['macro_checked'] is not True
                    or type(e['coverage_until']) is not date or e['coverage_until'] < horizon
                    or not isinstance(e['dates'],(list,tuple))
                    or any(type(d) is not date for d in e['dates'])):
                reasons.append('EVENT_COVERAGE_MISSING')
            elif any(now.date() <= d <= horizon for d in e['dates']):
                reasons.append('EVENT_WITHIN_3_SESSIONS')
        except (ValueError,OverflowError):
            reasons.append('CALENDAR_UNAVAILABLE')
        if not all(x is True for x in (m['volume_ok'],m['wick_ok'],m['gap_ok'],b['board_ok'])):
            reasons.append('TECHNICAL_OR_BOARD_FAILED')
        if abs((evidence['market'].observed_at-evidence['board'].observed_at).total_seconds())>15:
            reasons.append('MARKET_BOARD_TIME_SKEW')
        try:
            if money(m['vwap']) <= 0 or money(m['price']) < money(m['vwap']):
                reasons.append('BELOW_VWAP')
            sized = size(budget,price=m['price'],stop=stop,cost_per_share=cost_per_share,
                         recommended_shares=r['recommended_shares'],committed_cash=committed_cash)
            if sized['shares']<100:
                reasons.append('INSUFFICIENT_INPUT_BUDGET_OR_LOT')
        except (ValueError,TypeError,AttributeError):
            reasons.append('INVALID_BUDGET_OR_PRICE')
    if reasons:
        sized.update(shares=0,required_cash=0,planned_loss=0)
    end_hour,end_minute = (11,25) if now.hour<12 else (14,55)
    deadlines.append(now.replace(hour=end_hour,minute=end_minute,second=0,microsecond=0))
    return dict(**sized,mode='USER_INPUT_BUDGET',decision='見送り' if reasons else '入力予算内・条件通過',
                candidate_allowed=not reasons,go_allowed=False,broker_order_allowed=False,
                valid_until=min(deadlines).isoformat() if not reasons else None,
                reasons=list(dict.fromkeys(reasons)),
                operational_assumption='TradingView利用条件はユーザー判断でクリア扱い',
                live_blockers=['AUTHENTICATED_ADAPTERS_AND_NOTIFICATION_E2E_NOT_VERIFIED'])


class Ledger:
    """Durable notification commitments, NOT executions or actual holdings.

    BEGIN IMMEDIATE serializes funds checks across worker connections.
    No date rollover or timeout releases funds. A user can explicitly release
    a skipped/closed reservation. Balance edits do not silently clear it.
    """
    def __init__(self,path):
        self.db=sqlite3.connect(path,timeout=10,isolation_level=None)
        self.db.execute('CREATE TABLE IF NOT EXISTS reservations (id TEXT PRIMARY KEY, symbol TEXT NOT NULL, cash INTEGER NOT NULL, result TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1)')
        from .notification_outbox import initialize
        initialize(self.db)

    def decide_and_reserve(self,event_id,symbol,codes,evidence,budget,**kwargs):
        if not isinstance(event_id,str) or not re.fullmatch(r'[A-Za-z0-9:._-]{1,150}',event_id):
            raise ValueError('INVALID_EVENT_ID')
        self.db.execute('BEGIN IMMEDIATE')
        try:
            old=self.db.execute('SELECT result FROM reservations WHERE id=?',(event_id,)).fetchone()
            if old:
                result=json.loads(old[0]);result.update(duplicate=True,notify=False)
            else:
                used=self.db.execute('SELECT COALESCE(SUM(cash),0) FROM reservations WHERE active=1').fetchone()[0]
                result=evaluate(symbol,codes,evidence,budget,committed_cash=used,**kwargs)
                if self.db.execute('SELECT 1 FROM reservations WHERE symbol=? AND active=1',(symbol,)).fetchone():
                    result.update(candidate_allowed=False,decision='見送り',shares=0,required_cash=0,planned_loss=0,valid_until=None)
                    result['reasons'].append('SYMBOL_ALREADY_COMMITTED')
                result.update(duplicate=False,notify=result['candidate_allowed'])
                self.db.execute('INSERT INTO reservations VALUES (?,?,?,?,?)',
                                (event_id,symbol,result['required_cash'],json.dumps(result,ensure_ascii=False),int(result['candidate_allowed'])))
                if result['candidate_allowed']:
                    from .notification_outbox import enqueue
                    enqueue(self.db,event_id,symbol,result,kwargs['now'])
            self.db.execute('COMMIT')
            return result
        except Exception:
            self.db.execute('ROLLBACK');raise

    def release(self,event_id,*,user_confirmed):
        if user_confirmed is not True:
            raise ValueError('USER_CONFIRMATION_REQUIRED')
        self.db.execute('UPDATE reservations SET active=0 WHERE id=?',(event_id,))

    def close(self):
        self.db.close()
