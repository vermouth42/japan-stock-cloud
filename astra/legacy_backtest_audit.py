"""Reproduce PR #1 from its immutable Actions artifact; never bless it as formal.

No network, broker access or reconstruction of historical grades. Private rank
exports are read locally; the report only includes aggregate coverage facts.
"""
from __future__ import annotations
import argparse
import csv
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import zipfile
from collections import Counter
from datetime import date, timedelta
from .rank_history import verify
from .backtest_matrix import MODES, PROFILES
from .market_data import JST

SOURCE_SHA = '102cd947ded147177b1f15656b39e6ea46c3a1c2'
ARCHIVE_SHA = '54ac0c56c872fa1479afe7c62628d21fb3da6720adadcdbcb548800782dfcb9e'
MEMBERS = {'history.csv', 'market.csv', 'trades.csv'}


def read_archive(path):
    blob = Path(path).read_bytes()
    if hashlib.sha256(blob).hexdigest() != ARCHIVE_SHA:
        raise ValueError('PR #1 artifact digest mismatch')
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        if set(z.namelist()) != MEMBERS or len(z.namelist()) != 3:
            raise ValueError('unexpected artifact members')
        return {name: z.read(name) for name in sorted(MEMBERS)}


def price_inventory(blob):
    rows = list(csv.DictReader(io.StringIO(blob.decode('utf-8-sig'))))
    counts = Counter(r['コード'] for r in rows)
    dates = [date.fromisoformat(r['日付']) for r in rows]
    if not rows:
        raise ValueError('empty history')
    return dict(rows=len(rows), symbols=len(counts), first=min(dates).isoformat(),
                last=max(dates).isoformat(), minimum_bars=min(counts.values()),
                maximum_bars=max(counts.values()), symbols_below_200_bars=sum(n < 200 for n in counts.values()),
                duplicate_symbol_dates=len(rows)-len({(r['コード'], r['日付']) for r in rows}))


def rank_inventory(rows, history_last):
    observations = verify(rows)
    return dict(observations=len(observations),
        first_observed_at=observations[0]['observed_at'].isoformat() if observations else None,
        last_observed_at=observations[-1]['observed_at'].isoformat() if observations else None,
        latest_grade_counts=dict(Counter(r['grade'] for r in observations[-1]['payload']['ranks'])) if observations else {},
        observations_on_or_before_history_end=sum(o['observed_at'].astimezone(JST).date() <= date.fromisoformat(history_last) for o in observations),
        hash_chain_valid=True, hash_chain_proves_authenticity=False,
        source_first_publication_verified=False)


def formal_status():
    blockers = [
        'historical A/A+ and point-in-time universe unavailable for the price period',
        'daily bars cannot supply intraday VWAP, volume continuation, wick, gap and board evidence',
        'event release timestamps and complete three-business-day event coverage unavailable',
        'corporate-action adjustment is heuristic, not independently reconciled',
        'legacy portfolio omits cash affordability, 100-share lots and unrealized drawdown',
        'legacy PF is R-weighted across all signals, not currency PF of executed portfolio trades',
        'legacy fee=0 and absolute 1-yen slippage are assumptions, not full execution costs',
        'parameters were compared on the same historical period; no untouched holdout',
    ]
    return dict(status='DATA_BLOCKED', formal_performance_approved=False, go_allowed=False,
        blockers=blockers, future_10x_probability=None,
        scenarios=[dict(mode=m, profile=p, status='DATA_BLOCKED', metrics=None)
                   for m in MODES for p in PROFILES])


# Executed only against the pinned, clean legacy checkout. A separate process
# prevents its jpstock imports from contaminating the current application's state.
REPLAY = r'''
import sys, json, dataclasses
from pathlib import Path
sys.path.insert(0, str(Path(sys.argv[1])/'python'))
from jpstock.data import load_prices
from jpstock.rules import SwingParams
from jpstock.backtest import backtest_swing, summarize, simulate_portfolio
from backtest import write_trades
data=load_prices(sys.argv[2]); output=[]
for trend in (200,0):
    p=dataclasses.replace(SwingParams(), trend_ma_len=trend)
    all_trades=[]
    for code,bars in sorted(data.items()):
        all_trades.extend(backtest_swing(code,bars,p,slippage=1.0))
    all_trades.sort(key=lambda t:(t.entry_date,t.code))
    if trend==200:write_trades(all_trades,sys.argv[4])
    trades=[t for t in all_trades if t.entry_date>=sys.argv[3]]
    last=max(b[-1].date for b in data.values())
    portfolio=simulate_portfolio(trades,start=sys.argv[3],end=last,initial=1_000_000,risk_pct=1,max_positions=5)
    output.append(dict(trend_ma=trend,start=sys.argv[3],end=last,
        signal_metrics_R=summarize(trades),legacy_portfolio=dataclasses.asdict(portfolio),
        open_at_end=sum(t.open_at_end for t in trades)))
print(json.dumps(output,allow_nan=False))
'''


def audit(archive, legacy_root, rank_rows):
    legacy_root = Path(legacy_root).resolve()
    sha = subprocess.check_output(['git', '-C', str(legacy_root), 'rev-parse', 'HEAD'], text=True).strip()
    dirty = subprocess.check_output(['git', '-C', str(legacy_root), 'status', '--porcelain', '--untracked-files=no'], text=True)
    if sha != SOURCE_SHA or dirty:
        raise ValueError('pinned clean PR #1 checkout required')
    files = read_archive(archive)
    inventory = price_inventory(files['history.csv'])
    start = (date.fromisoformat(inventory['first']) + timedelta(days=300)).isoformat()
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        for name, blob in files.items():
            (root/name).write_bytes(blob)
        output = subprocess.check_output([sys.executable, '-c', REPLAY, str(legacy_root),
            str(root/'history.csv'), start, str(root/'reproduced.csv')], text=True)
        identical = (root/'reproduced.csv').read_bytes() == files['trades.csv']
    if not identical:
        raise ValueError('archived trades did not reproduce byte-for-byte')
    return dict(schema='astra.legacy-audit.v1', classification='REPRODUCED_RESEARCH_ONLY',
        source_commit=sha, artifact_id=13160687826, workflow_run_id=41484052767,
        archive_sha256=ARCHIVE_SHA, file_sha256={n:hashlib.sha256(b).hexdigest() for n,b in files.items()},
        archived_trades_byte_identical=identical,
        price_inventory=inventory, rank_inventory=rank_inventory(rank_rows, inventory['last']),
        legacy_reproductions=json.loads(output), formal=formal_status())


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--archive', type=Path, required=True)
    ap.add_argument('--legacy-root', type=Path, required=True)
    ap.add_argument('--rank-rows', type=Path, required=True)
    ap.add_argument('--output', type=Path, required=True)
    args=ap.parse_args()
    report=audit(args.archive,args.legacy_root,json.loads(args.rank_rows.read_text()))
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False)+'\n')


if __name__=='__main__':main()
