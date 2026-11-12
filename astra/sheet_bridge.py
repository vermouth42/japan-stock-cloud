"""Read-only Ver.7.5/7.7 adapter. A read timestamp is NOT a confirmation.

Inputs are UNFORMATTED_VALUE exports with their actual header row. No credentials,
network, writes, order calls, or live GO. Receipts must be captured by a separately
validated confirmation handler; absence is never filled from TODAY/NOW.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from datetime import date, datetime, timedelta
from math import isfinite
from pathlib import Path

from .market_data import Calendar, HEADER, JST, ROOT
from .readiness import assess

TOP_HEADERS = ['順位','コード','最終Score','BUY','信用','終値','ATR%','損切り%','100株資金','100株損失','リスク上限株数','資金上限株数','推奨株数','実戦判定','材料状況','材料カテゴリ','有効材料Score']
SBI_HEADERS = ['順位','コード','BUY','推奨株数','SBI現在値','SBI当日VWAP','VWAP上','出来高継続','上ヒゲ小','ギャップOK','板OK','SBI確認済（タップ）','最終判定','確認記録日時（自動）','イベント警戒（自動）']
EVENT_HEADERS = ['コード','銘柄名','イベント日','種別','時刻','確度','残日数','警戒','メモ']
RECEIPT_KEYS = {'symbol','fingerprint','rank_checked_at','sbi_checked_at','event_checked_at',
                'events_clear_through','event_evidence','stop','allowed_loss',
                'remaining_risk_budget','available_cash','cost_per_share'}


def symbol(value) -> str:
    if type(value) in (int, float) and isfinite(value) and value == int(value):
        value = str(int(value))
    if not isinstance(value, str) or not re.fullmatch(r'[0-9]{4}|[0-9]{3}[A-Z]', value):
        raise ValueError('invalid stock code')
    return value


def table(values, headers):
    if not isinstance(values, list) or not values or values[0] != headers:
        raise ValueError('unexpected sheet headers')
    result = []
    for raw in values[1:]:
        if not isinstance(raw, list) or len(raw) > len(headers):
            raise ValueError('invalid row width')
        if not any(v not in (None, '') for v in raw):
            continue
        row = dict(zip(headers, raw + [None] * (len(headers) - len(raw))))
        row['コード'] = symbol(row['コード'])
        result.append(row)
    return result


def index_unique(rows):
    result = {}
    for row in rows:
        code = row['コード']
        if code in result:
            raise ValueError('duplicate candidate code')
        result[code] = row
    return result


def sheet_time(value) -> datetime:
    # Native serials refer to local spreadsheet time; this workbook is JST.
    if type(value) in (int, float) and isfinite(value) and 0 < value < 100000:
        return datetime(1899, 12, 30, tzinfo=JST) + timedelta(days=value)
    if isinstance(value, str):
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is not None:
            return parsed.astimezone(JST)
    raise ValueError('confirmation timestamp missing or not timezone-aware')


def event_date(value) -> date:
    if type(value) in (int, float):
        if value != int(value):
            raise ValueError('event date must not contain time')
        return sheet_time(value).date()
    if isinstance(value, str) and re.fullmatch(r'\d{4}[-/]\d{2}[-/]\d{2}', value):
        return date.fromisoformat(value.replace('/', '-'))
    raise ValueError('invalid event date')


def fingerprint(top, sbi) -> str:
    # Bind immutable identity, rank, sizing and every manually checked SBI value.
    # Exclude row number and derived presentation so simple sorting is harmless.
    values = [top[k] for k in ('コード','BUY','推奨株数')]
    values += [sbi[k] for k in ('コード','BUY','推奨株数','SBI現在値','SBI当日VWAP',
                               '出来高継続','上ヒゲ小','ギャップOK','板OK',
                               'SBI確認済（タップ）','確認記録日時（自動）')]
    values = [int(v) if type(v) is float and isfinite(v) and v.is_integer() else v for v in values]
    payload = json.dumps(values, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
    return hashlib.sha256(payload.encode()).hexdigest()


def next_three_sessions(today: date) -> date:
    cal, day, count = Calendar(), today, 0
    cal.trading(today)  # Reject unsupported years even for zero-day events.
    while count < 3:
        day += timedelta(days=1)
        count += cal.trading(day)
    return day


def near_events(events, code: str, today: date) -> list[dict]:
    found, cal = [], Calendar()
    cal.trading(today)
    for row in events:
        if row['コード'] not in (code, '0000'):
            continue
        day = event_date(row['イベント日'])
        # Past labels/countdowns may be hardcoded. Compute from the date itself.
        if day < today:
            continue
        days = cal.sessions_until(today, day)
        if days <= 3:
            found.append({'date':day.isoformat(), 'business_days':days,
                          'kind':row['種別'], 'source':row['メモ'] or ''})
    return found


def build_manual(top, sbi, events, receipt, now):
    code, blockers = top['コード'], []
    base = dict(symbol=code, rank_symbol=code, rank=top['BUY'],
                recommended_shares=top['推奨株数'])
    near = near_events(events, code, now.astimezone(JST).date())
    if near:
        blockers.append('KNOWN_EVENT_WITHIN_3_JPX_SESSIONS')
    if sbi is None:
        return base, blockers + ['SBI_SYMBOL_MISSING'], near
    base.update(sbi_symbol=sbi['コード'], current_price=sbi['SBI現在値'],
                vwap=sbi['SBI当日VWAP'], volume_ok=sbi['出来高継続'],
                wick_ok=sbi['上ヒゲ小'], gap_ok=sbi['ギャップOK'], board_ok=sbi['板OK'])
    if top['BUY'] != sbi['BUY'] or top['推奨株数'] != sbi['推奨株数']:
        blockers.append('RANK_OR_SIZE_SOURCE_CONFLICT')
    if not isinstance(receipt, dict) or set(receipt) != RECEIPT_KEYS:
        return base, blockers + ['CONFIRMATION_RECEIPT_MISSING_OR_SCHEMA'], near
    if receipt['symbol'] != code or receipt['fingerprint'] != fingerprint(top, sbi):
        blockers.append('CONFIRMATION_BINDING_MISMATCH')
    try:
        sbi_at = sheet_time(sbi['確認記録日時（自動）'])
        receipt_at = sheet_time(receipt['sbi_checked_at'])
        # Allow serial conversion rounding, never a different confirmation time.
        if abs((sbi_at - receipt_at).total_seconds()) > 0.001:
            blockers.append('SBI_TIMESTAMP_MISMATCH')
        if sbi['SBI確認済（タップ）'] is not True:
            blockers.append('SBI_CONFIRMATION_UNCHECKED')
        coverage = event_date(receipt['events_clear_through'])
        if coverage < next_three_sessions(now.astimezone(JST).date()):
            blockers.append('EVENT_COVERAGE_INCOMPLETE')
        if not isinstance(receipt['event_evidence'], str) or not receipt['event_evidence'].strip():
            blockers.append('EVENT_EVIDENCE_MISSING')
    except (ValueError, TypeError, OverflowError):
        return base, blockers + ['CONFIRMATION_VALUES_INVALID'], near
    manual = dict(symbol=code, rank_symbol=code, sbi_symbol=sbi['コード'],
        rank=top['BUY'], recommended_shares=top['推奨株数'],
        current_price=sbi['SBI現在値'], vwap=sbi['SBI当日VWAP'],
        sbi_checked_at=sbi_at.isoformat(),
        rank_checked_at=receipt['rank_checked_at'], event_checked_at=receipt['event_checked_at'],
        event_business_days=min((e['business_days'] for e in near), default=4),
        event_evidence=receipt['event_evidence'],
        volume_ok=sbi['出来高継続'], wick_ok=sbi['上ヒゲ小'],
        gap_ok=sbi['ギャップOK'], board_ok=sbi['板OK'])
    for key in ('stop','allowed_loss','remaining_risk_budget','available_cash','cost_per_share'):
        manual[key] = receipt[key]
    return manual, blockers, near


def review(bundle: dict, status, repo_rows, sheet_rows, codes, now: datetime) -> dict:
    if now.tzinfo is None:
        raise ValueError('timezone-aware decision clock required')
    capture = sheet_time(bundle['captured_at'])
    if capture > now or now - capture > timedelta(minutes=5):
        raise ValueError('live export is stale or in the future')
    if bundle.get('spreadsheet_timezone') != 'Asia/Tokyo':
        raise ValueError('unexpected spreadsheet timezone')
    tops = index_unique(table(bundle['top10'], TOP_HEADERS))
    sbis = index_unique(table(bundle['sbi'], SBI_HEADERS))
    events = table(bundle['events'], EVENT_HEADERS)
    receipts = bundle.get('receipts', {})
    if not isinstance(receipts, dict) or len(tops) != 10 or set(tops) != set(sbis):
        raise ValueError('candidate table coverage mismatch')
    if set(tops) - set(codes) or set(receipts) - set(tops):
        raise ValueError('unexpected candidate/receipt code')
    results = []
    for code, top in tops.items():
        try:
            manual, blockers, near = build_manual(top, sbis[code], events, receipts.get(code), now)
            result = assess(status, repo_rows, sheet_rows, codes, manual, now)
            reasons = list(dict.fromkeys(blockers + result['reasons']))
            passed = not reasons and result['offline_checks_passed']
            results.append({'symbol':code, 'rank':top['BUY'], 'offline_checks_passed':passed,
                'go_allowed':False, 'proposed_shares':result['proposed_shares'] if passed else 0,
                'reasons':reasons, 'known_near_events':near})
        except (ValueError, TypeError, KeyError, OverflowError):
            results.append({'symbol':code, 'go_allowed':False, 'offline_checks_passed':False,
                            'proposed_shares':0, 'reasons':['SOURCE_VALUES_INVALID']})
    return {'mode':'READ_ONLY_SHEET_BRIDGE', 'checked_at_jst':now.astimezone(JST).isoformat(),
            'source_captured_at':capture.isoformat(), 'go_allowed':False,
            'live_blocker':'CONFIRMATION_HANDLER_AND_LIVE_DEPLOYMENT_NOT_VALIDATED',
            'candidates':results}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for flag in ('bundle','status','prices','sheet-rows'):
        parser.add_argument('--'+flag, type=Path, required=True)
    parser.add_argument('--now', help='aware replay time; never live authorization')
    args = parser.parse_args()
    now = datetime.fromisoformat(args.now) if args.now else datetime.now(JST)
    try:
        with args.prices.open(encoding='utf-8-sig', newline='') as stream:
            reader = csv.reader(stream)
            if next(reader) != HEADER:
                raise ValueError('unexpected CSV schema')
            rows = list(reader)
        result = review(json.loads(args.bundle.read_text()), json.loads(args.status.read_text()),
            rows, json.loads(args.sheet_rows.read_text()),
            json.loads((ROOT/'watchlist.json').read_text()), now)
    except (ValueError, TypeError, KeyError, OverflowError, OSError):
        result = {'mode':'READ_ONLY_SHEET_BRIDGE','go_allowed':False,
                  'reasons':['SOURCE_EXPORT_INVALID']}
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
    raise SystemExit(2)


if __name__ == '__main__':
    main()
