"""Project a captured Ver7.5 snapshot without manual SBI inputs or live GO.

Capture time is transport metadata, never a rank/event confirmation time.
This adapter does not acquire quotes, authorize orders, or send notifications.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timedelta
from math import isfinite
from pathlib import Path

from .market_data import Calendar, JST, ROOT
from .sheet_bridge import TOP_HEADERS, EVENT_HEADERS, table, index_unique, event_date, near_events, sheet_time, symbol

SHEET_ID = '1AQ0CcqOnyLHWSOnHR_m0FsvcvNFZIbJ8dRYzKuKU6Zk'
RANK_HEADERS = ['順位','コード','最新日','終値','前日比%','出来高倍率','VWAP判定','25日高値','ATR%','売買代金','監視Score','アクション','材料確認','材料カテゴリ','有効材料Score','最終Score','BUY','信用']
LIVE_BLOCKERS = ['RANK_UPDATE_TIME_UNVERIFIED', 'EVENT_COVERAGE_UNVERIFIED',
                 'TRADINGVIEW_QUOTE_NOT_CONNECTED', 'ACCOUNT_AND_BOARD_NOT_CONNECTED',
                 'LIVE_END_TO_END_NOT_VALIDATED']


def number(value):
    if type(value) not in (int, float) or not isfinite(value):
        raise ValueError('invalid sheet number')
    return value


def project(bundle: dict, codes: list[str], now: datetime) -> dict:
    if now.tzinfo is None:
        raise ValueError('timezone-aware clock required')
    if bundle.get('spreadsheet_id') != SHEET_ID or bundle.get('spreadsheet_timezone') != 'Asia/Tokyo':
        raise ValueError('unexpected workbook')
    begin, end = sheet_time(bundle['capture_started_at']), sheet_time(bundle['captured_at'])
    if not begin <= end <= now or end-begin > timedelta(seconds=30) or now-begin >= timedelta(minutes=5):
        raise ValueError('stale, future, or non-coherent capture')
    expected = Calendar().latest_complete(now)
    if len(codes) != 100 or len(set(codes)) != 100 or any(symbol(c) != c for c in codes):
        raise ValueError('invalid 100-stock watchlist')
    ranks = index_unique(table(bundle['ranking'], RANK_HEADERS))
    tops = index_unique(table(bundle['top10'], TOP_HEADERS))
    events = table(bundle['events'], EVENT_HEADERS)
    if set(ranks) != set(codes) or len(tops) != 10 or set(tops)-set(ranks):
        raise ValueError('ranking/candidate coverage mismatch')
    for row in ranks.values():
        if event_date(row['最新日']) != expected:
            raise ValueError('incomplete or stale ranking market date')
        number(row['最終Score'])
        if row['BUY'] not in ('A+', 'A', 'B', 'C'):
            raise ValueError('unknown grade')
    # Validate the complete event table: a corrupt unrelated row can hide a
    # mistyped symbol. Unknown calendar years are never treated as clear.
    for row in events:
        if row['コード'] not in set(codes) | {'0000'}:
            raise ValueError('event symbol outside watchlist')
        Calendar().trading(event_date(row['イベント日']))
    result, scores = [], []
    for code, top in tops.items():
        rank = ranks[code]
        if any(top[k] != rank[k] for k in ('BUY','最終Score','終値','信用')):
            raise ValueError('ranking and candidate sources conflict')
        qty = number(top['推奨株数'])
        if qty < 0 or qty != int(qty) or qty % 100:
            raise ValueError('invalid recommended lot')
        scores.append(number(top['最終Score']))
        near = near_events(events, code, now.astimezone(JST).date())
        blockers = []
        if top['BUY'] not in ('A', 'A+'):
            blockers.append('RANK_NOT_A')
        if qty < 100:
            blockers.append('RECOMMENDED_SHARES_BELOW_100')
        if near:
            blockers.append('KNOWN_EVENT_WITHIN_3_JPX_SESSIONS')
        result.append(dict(symbol=code, grade=top['BUY'], recommended_shares=int(qty),
            screening_conditions_met=not blockers, screening_blockers=blockers,
            known_near_events=near, live_blockers=list(LIVE_BLOCKERS), go_allowed=False))
    # Preserve ties, but reject a TOP10 which omitted a higher score, including
    # the former rows 101:103 bug. Display order must be descending.
    if scores != sorted(scores, reverse=True) or scores != sorted((r['最終Score'] for r in ranks.values()), reverse=True)[:10]:
        raise ValueError('TOP10 is not selected from all 100 stocks')
    payload = json.dumps(bundle, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
    return dict(mode='READ_ONLY_AUTOMATIC_CANDIDATE_PROJECTION', go_allowed=False,
        captured_at=end.isoformat(), checked_at=now.astimezone(JST).isoformat(),
        market_date=expected.isoformat(), watchlist_count=100,
        snapshot_sha256=hashlib.sha256(payload.encode()).hexdigest(),
        rank_observed_at=None, event_coverage_verified=False,
        screening_candidate_count=sum(r['screening_conditions_met'] for r in result), candidates=result)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle', type=Path, required=True)
    parser.add_argument('--now', help='aware replay clock; not live authorization')
    args = parser.parse_args()
    try:
        now = datetime.fromisoformat(args.now) if args.now else datetime.now(JST)
        result = project(json.loads(args.bundle.read_text()), json.loads((ROOT/'watchlist.json').read_text()), now)
    except (ValueError, TypeError, KeyError, OverflowError, OSError):
        print(json.dumps(dict(go_allowed=False, reasons=['SNAPSHOT_INVALID']), ensure_ascii=False))
        raise SystemExit(2)
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
    # Successful read-only projection is not a trading authorization.


if __name__ == '__main__':
    main()
