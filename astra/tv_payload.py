"""Validate v1 Pine observations after transport authentication, never orders."""
from datetime import datetime,timedelta
import json
import re
from .automatic_preflight import Evidence,aware,money
from .market_data import JST


def parse(body,received_at,*,transport_verified=False):
    if transport_verified is not True:
        raise ValueError('TRANSPORT_NOT_VERIFIED')
    if not aware(received_at) or not isinstance(body,str) or len(body.encode())>4096:
        raise ValueError('INVALID_ENVELOPE')
    def pairs(items):
        obj={}
        for k,v in items:
            if k in obj:raise ValueError('DUPLICATE_FIELD')
            obj[k]=v
        return obj
    def invalid(_):raise ValueError('NONFINITE_JSON')
    p=json.loads(body,object_pairs_hook=pairs,parse_constant=invalid)
    fields={'schema','ticker','timeframe','bar_close_ms','price','vwap','volume_ok','wick_ok','gap_ok','kind'}
    if (not isinstance(p,dict) or set(p)!=fields or p['schema']!='astra.tv.v1'
            or not isinstance(p['ticker'],str) or not re.fullmatch(r'TSE:[0-9][0-9A-Z]{3}',p['ticker'])
            or p['timeframe'] not in ('1','5') or type(p['bar_close_ms']) is not int
            or p['kind'] not in ('technical','weakness')
            or any(type(p[k]) is not bool for k in ('volume_ok','wick_ok','gap_ok'))):
        raise ValueError('INVALID_PAYLOAD')
    at=datetime.fromtimestamp(p['bar_close_ms']/1000,JST)
    if not at<=received_at<at+timedelta(seconds=90) or at.date()!=received_at.astimezone(JST).date():
        raise ValueError('STALE_OR_FUTURE_PAYLOAD')
    if money(p['price'])<=0 or money(p['vwap'])<=0:
        raise ValueError('INVALID_PRICE')
    symbol=p['ticker'][4:]
    event_id=f"{symbol}:{p['timeframe']}:{p['bar_close_ms']}:{p['kind']}"
    e=Evidence('TradingView',symbol,at,received_at,at+timedelta(seconds=90),event_id,
               {k:p[k] for k in ('price','vwap','volume_ok','wick_ok','gap_ok')})
    return {'event_id':event_id,'kind':p['kind'],'market':e,
            'entry_eligible_event':p['kind']=='technical','go_allowed':False}
