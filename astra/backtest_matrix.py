"""Shared-cash 3x3 research simulator. No data download, broker or live GO.

Inputs are complete synchronized intraday bars and timestamped, precomputed
signals. This is not a reconstruction of missing historical A/A+ or order books.
Parameters are preregistered research assumptions, not trading recommendations.
"""
from __future__ import annotations
import argparse
import calendar
import hashlib
import json
import math
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from .market_data import JST

PROFILES = {
 'conservative': dict(risk=.0025,position=.20,total_risk=.01,positions=2),
 'aggressive': dict(risk=.005,position=.35,total_risk=.02,positions=4),
 'high_risk': dict(risk=.01,position=.50,total_risk=.04,positions=6),
}
MODES=('day','swing','combined')
REQUIRED_EVIDENCE=('point_in_time_universe','rank_history','event_history','intraday_vwap','board_history','corporate_actions','licensed_data')

def stamp(value):
    d=datetime.fromisoformat(value.replace('Z','+00:00'))
    if d.tzinfo is None:raise ValueError('timezone required')
    return d

def number(value,positive=False):
    if type(value) not in (int,float) or not math.isfinite(value) or (positive and value<=0):
        raise ValueError('invalid finite number')
    return float(value)

def validate(data):
    if data.get('schema')!='astra.backtest.v1':raise ValueError('unsupported schema')
    frames=data['frames']; signals=data['signals']; universe=data['universe']
    if not frames or len(universe)!=len(set(universe)) or not universe:raise ValueError('empty/duplicate universe')
    prior=None; per_day={}
    for f in frames:
        start,end=stamp(f['open_at']),stamp(f['close_at'])
        if start>=end or (prior and start<prior):raise ValueError('overlap or unsorted frames')
        if start.astimezone(JST).date()!=end.astimezone(JST).date():raise ValueError('bar crosses session day')
        if type(f['session_end']) is not bool:raise ValueError('session_end must be boolean')
        if set(f['bars'])!=set(universe):raise ValueError('synchronized complete universe required')
        day=start.astimezone(JST).date()
        if day in per_day and per_day[day]:raise ValueError('bars after session end')
        per_day[day]=f['session_end']
        for b in f['bars'].values():
            o,h,l,c=[number(b[k],True) for k in ('open','high','low','close')]
            if not l<=min(o,c)<=max(o,c)<=h:raise ValueError('invalid OHLC')
        prior=end
    if not all(per_day.values()):raise ValueError('missing final session bar')
    ids=set()
    for s in signals:
        if s['id'] in ids:raise ValueError('duplicate signal id')
        ids.add(s['id'])
        if s['symbol'] not in universe or s['horizon'] not in ('day','swing'):raise ValueError('signal identity')
        if s['grade'] not in ('A+','A','B','C'):raise ValueError('unknown rank')
        observed,valid=stamp(s['known_at']),stamp(s['expires_at'])
        if observed>=valid:raise ValueError('signal expiry')
        if type(s['conditions_verified']) is not bool:raise ValueError('conditions boolean required')
        if type(s['max_shares']) is not int or s['max_shares']<0 or s['max_shares']%100:raise ValueError('lot')
        if not 0<number(s['stop'],True)<number(s['target'],True):raise ValueError('levels')
        number(s['score'])
    fee=number(data['costs']['fee_bps']);slip=number(data['costs']['slippage_bps'])
    if not 0<=fee<1000 or not 0<=slip<1000:raise ValueError('explicit nonnegative costs required')
    return frames,signals

@dataclass
class Position:
    symbol:str
    shares:int
    entry:float
    entry_fee:float
    stop:float
    target:float
    horizon:str
    opened_at:str


def metrics(curve,trades,initial):
    navs=[initial]+[r['equity'] for r in curve]
    peak=initial;dd=0
    for nav in navs:
        peak=max(peak,nav);dd=max(dd,1-nav/peak)
    profits=[t['pnl'] for t in trades];wins=sum(p>0 for p in profits)
    gain=sum(p for p in profits if p>0);loss=-sum(p for p in profits if p<0)
    elapsed=(stamp(curve[-1]['at'])-stamp(curve[0]['open_at'])).total_seconds()/86400
    monthly={};base=initial;last_month=None;last_nav=initial
    for r in curve:
        month=stamp(r['at']).astimezone(JST).strftime('%Y-%m')
        if last_month is not None and month!=last_month:
            monthly[last_month]=last_nav/base-1;base=last_nav
        last_month,last_nav=month,r['equity']
    monthly[last_month]=last_nav/base-1
    # Non-overlapping calendar months are descriptive; boundary months can be partial.
    windows=[];first_by_day={}
    for i,r in enumerate(curve):first_by_day.setdefault(stamp(r['open_at']).astimezone(JST).date(),i)
    for d,i in first_by_day.items():
        m=d.month+3;y=d.year+(m-1)//12;m=(m-1)%12+1
        end=d.replace(year=y,month=m,day=min(d.day,calendar.monthrange(y,m)[1]))
        j=next((j for j in range(i,len(curve)) if stamp(curve[j]['at']).astimezone(JST).date()>=end),None)
        if j is None:continue
        start=initial if i==0 else curve[i-1]['equity']
        if start<=0:continue
        vals=[r['equity'] for r in curve[i:j+1]]
        windows.append(dict(start=d.isoformat(),end=end.isoformat(),return_3m=vals[-1]/start-1,hit_10x=max(vals)>=start*10))
    return dict(trades=len(trades),win_rate=wins/len(trades) if trades else None,
      profit_factor=gain/loss if loss else None,profit_factor_status='NO_LOSSES' if gain and not loss else 'NO_TRADES' if not trades else 'DEFINED',
      expectancy_yen=sum(profits)/len(profits) if trades else None,max_close_drawdown=dd,
      total_return=navs[-1]/initial-1,annualized_return=(navs[-1]/initial)**(365.25/elapsed)-1 if elapsed>=365.25 and navs[-1]>0 else None,
      annualized_status='AVAILABLE' if elapsed>=365.25 else 'LESS_THAN_ONE_YEAR',monthly_returns=monthly,
      monthly_boundary_periods_may_be_partial=True,rolling_3m=windows,
      observed_10x_frequency=sum(w['hit_10x'] for w in windows)/len(windows) if windows else None,
      observed_10x_windows=len(windows),windows_overlap=True,
      future_10x_probability=None,probability_status='NOT_ESTIMATED_FROM_RESEARCH_SAMPLE')


def simulate(data,mode,profile):
    frames,signals=validate(data)
    if mode not in MODES or profile not in PROFILES:raise ValueError('unknown scenario')
    cfg=PROFILES[profile];cash=initial=1_000_000.0;positions={};used=set();trades=[];curve=[];rejected={}
    fee=data['costs']['fee_bps']/10000;slip=data['costs']['slippage_bps']/10000
    def close(symbol,price,at,reason):
        nonlocal cash
        p=positions.pop(symbol);fill=price*(1-slip);cost=p.shares*fill*fee
        cash+=p.shares*fill-cost
        trades.append(dict(symbol=symbol,shares=p.shares,entry=p.entry,exit=fill,entry_at=p.opened_at,exit_at=at,
          reason=reason,pnl=(fill-p.entry)*p.shares-p.entry_fee-cost,fees=p.entry_fee+cost,horizon=p.horizon))
    def bracket(symbol,b,at):
        p=positions[symbol]
        if b['open']<=p.stop:close(symbol,b['open'],at,'STOP_GAP')
        elif b['open']>=p.target:close(symbol,p.target,at,'TARGET_GAP_CONSERVATIVE')
        elif b['low']<=p.stop:close(symbol,p.stop,at,'STOP_FIRST_IF_AMBIGUOUS')
        elif b['high']>=p.target:close(symbol,p.target,at,'TARGET')
    for idx,f in enumerate(frames):
        now=stamp(f['open_at']);bars=f['bars']
        # Exits are settled before new entry sizing. OHLC ordering is unknowable;
        # a conservative same-bar rule forbids recycling intrabar exit proceeds.
        start_cash=cash;start_held=set(positions);opened_this_bar=set()
        equity_open=cash+sum(p.shares*bars[c]['open'] for c,p in positions.items())
        committed_risk=sum(max(0,p.entry-p.stop*(1-slip)+fee*(p.entry+p.stop*(1-slip)))*p.shares for p in positions.values())
        for symbol in list(positions):bracket(symbol,bars[symbol],f['close_at'])
        available=min(cash,start_cash)
        for s in sorted(signals,key=lambda s:(-s['score'],s['id'])):
            if s['id'] in used or not stamp(s['known_at'])<=now<stamp(s['expires_at']):continue
            used.add(s['id'])
            reason=None;symbol=s['symbol'];entry=bars[symbol]['open']*(1+slip)
            if mode!='combined' and s['horizon']!=mode:reason='MODE'
            elif s['grade'] not in ('A','A+') or not s['conditions_verified']:reason='INELIGIBLE'
            elif symbol in positions or symbol in start_held or symbol in opened_this_bar:reason='ALREADY_HELD_THIS_BAR'
            elif len(start_held|opened_this_bar)>=cfg['positions']:reason='POSITION_LIMIT'
            elif not s['stop']<entry<s['target']:reason='GAP_INVALID_LEVELS'
            if reason:rejected[reason]=rejected.get(reason,0)+1;continue
            equity=equity_open
            stop_fill=s['stop']*(1-slip)
            per_risk=entry-stop_fill+fee*(entry+stop_fill)
            risk_budget=max(0,min(equity*cfg['risk'],equity*cfg['total_risk']-committed_risk))
            q=int(min(s['max_shares'],available/(entry*(1+fee)),equity*cfg['position']/(entry*(1+fee)),risk_budget/per_risk)//100)*100
            if q<100:rejected['BELOW_100']=rejected.get('BELOW_100',0)+1;continue
            cost=q*entry*(1+fee);cash-=cost;available-=cost;committed_risk+=q*per_risk
            positions[symbol]=Position(symbol,q,entry,q*entry*fee,s['stop'],s['target'],s['horizon'],f['open_at'])
            opened_this_bar.add(symbol)
            bracket(symbol,bars[symbol],f['close_at'])
        if f['session_end']:
            for symbol,p in list(positions.items()):
                if p.horizon=='day':close(symbol,bars[symbol]['close'],f['close_at'],'DAY_END')
        if idx==len(frames)-1:
            for symbol in list(positions):close(symbol,bars[symbol]['close'],f['close_at'],'TEST_END')
        equity=cash+sum(p.shares*bars[c]['close'] for c,p in positions.items())
        if cash<-.00001 or not math.isfinite(equity):raise ValueError('cash invariant')
        curve.append(dict(open_at=f['open_at'],at=f['close_at'],equity=equity,cash=cash))
    return dict(mode=mode,profile=profile,assumptions=cfg,initial_capital=initial,final_capital=cash,
      metrics=metrics(curve,trades,initial),rejected=rejected,trades=trades,equity_curve=curve)


def run_matrix(data):
    validate(data)
    evidence=data.get('evidence',{})
    missing=[k for k in REQUIRED_EVIDENCE if evidence.get(k) is not True]
    return dict(schema='astra.matrix-report.v1',classification='RESEARCH_ONLY',go_allowed=False,
      input_sha256=hashlib.sha256(json.dumps(data,sort_keys=True,ensure_ascii=False,allow_nan=False).encode()).hexdigest(),
      missing_evidence=missing,formal_performance_approved=False,
      limitations=['declared evidence is not independently authenticated','OHLC stop-first assumption','close-based drawdown excludes intrabar path','long-only cash; no margin or shorting','no future probability estimate'],
      scenarios=[simulate(data,m,p) for m in MODES for p in PROFILES])


def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--input',type=Path,required=True);ap.add_argument('--output',type=Path,required=True)
    args=ap.parse_args();data=json.loads(args.input.read_text());report=run_matrix(data)
    args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False))
if __name__=='__main__':main()
