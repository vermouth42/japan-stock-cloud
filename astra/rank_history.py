"""Verify exported rank observations; never backdate a captured rank.

Hashes detect accidental changes and broken chains, not authenticity against an
editor capable of recomputing hashes. Source publication time stays unknown.
"""
import hashlib
import json
from datetime import datetime

def compact(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False)

def sha(value):
    return hashlib.sha256(value.encode()).hexdigest()

def verify(rows):
    previous, last_time = 'GENESIS', None
    observations=[]
    for row in rows:
        if len(row)!=6 or not all(isinstance(v,str) for v in row):
            raise ValueError('invalid history row')
        at,day,digest,parent,record_hash,payload=row
        now=datetime.fromisoformat(at.replace('Z','+00:00'))
        if now.tzinfo is None or (last_time is not None and now<last_time):
            raise ValueError('invalid observed time')
        if parent!=previous or digest!=sha(payload) or record_hash!=sha(compact([at,day,digest,parent,payload])):
            raise ValueError('history chain broken')
        p=json.loads(payload)
        if p.get('schema')!='astra.rank-observation.v1' or p.get('market_date')!=day or p.get('go_allowed') is not False:
            raise ValueError('invalid payload')
        universe=p['universe']; ranks=p['ranks']
        if len(universe)!=100 or len(set(universe))!=100 or {r['symbol'] for r in ranks}!=set(universe) or len(ranks)!=100:
            raise ValueError('incomplete universe')
        if any(r['grade'] not in ('A+','A','B','C') for r in ranks):
            raise ValueError('unknown grade')
        observations.append(dict(observed_at=now,payload=p))
        previous,last_time=record_hash,now
    return observations

def known_rank(observations, symbol, decision_at):
    if decision_at.tzinfo is None: raise ValueError('timezone required')
    eligible=[o for o in observations if o['observed_at']<=decision_at]
    if not eligible:return None
    latest=max(eligible,key=lambda o:o['observed_at'])
    # Old-date records are never silently reused as today's A/A+ evidence.
    from .market_data import JST
    if latest['observed_at'].astimezone(JST).date()!=decision_at.astimezone(JST).date():return None
    return next((r for r in latest['payload']['ranks'] if r['symbol']==symbol),None)
