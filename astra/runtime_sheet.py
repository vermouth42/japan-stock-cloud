"""Read-only runtime context from authenticated, bounded Sheet exports.

The callback belongs to the server, never to a webhook. Reading a rank proves
only that it was observed then, not its first publication or event completeness.
"""
from dataclasses import dataclass
from datetime import timedelta
import hashlib
import json
import urllib.parse
import urllib.request
from .automatic_preflight import Evidence, money
from .candidate_projection import project, SHEET_ID
from .input_budget import Budget
from .sheet_bridge import table, index_unique, TOP_HEADERS, sheet_time

RANGES = {'ranking': ('監視ランキング', 'A3:R103'),
          'top10': ('実戦候補TOP10', 'A1:Q11'),
          'events': ('イベント管理', 'A1:I100'),
          'budget': ('設定', 'A2:B3'),
          'cost': ('入力予算判定', 'A5:B5')}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):return None


def google_get(url, token):
    if not url.startswith('https://sheets.googleapis.com/v4/spreadsheets/'+SHEET_ID):
        raise ValueError('FIXED_GOOGLE_ENDPOINT_REQUIRED')
    request=urllib.request.Request(url,headers={'Authorization':'Bearer '+token,'Accept':'application/json'})
    with urllib.request.build_opener(NoRedirect()).open(request,timeout=5) as response:
        body=response.read(1_000_001)
        if len(body)>1_000_000:raise ValueError('SHEET_RESPONSE_TOO_LARGE')
        return json.loads(body)


class GoogleReadOnly:
    """Fixed-workbook GET-only adapter. Supply runtime credentials privately.

    token_provider() must refresh a spreadsheets.readonly token. This class
    never obtains credentials from TradingView, Sheets cells, CLI or URLs.
    """
    def __init__(self,token_provider,*,get_json=google_get):
        self.token_provider=token_provider;self.get_json=get_json

    def snapshot(self,clock):
        begin=clock()
        try:
            token=self.token_provider()
            if not isinstance(token,str) or not token or any(c.isspace() for c in token):
                raise ValueError('TOKEN_REQUIRED')
            base='https://sheets.googleapis.com/v4/spreadsheets/'+SHEET_ID
            metadata=self.get_json(base+'?fields=spreadsheetId,properties(timeZone)',token)
            if metadata.get('spreadsheetId')!=SHEET_ID or metadata.get('properties',{}).get('timeZone')!='Asia/Tokyo':
                raise ValueError('WORKBOOK_METADATA_INVALID')
            ranges=[f"'{sheet}'!{area}" for sheet,area in RANGES.values()]
            query=urllib.parse.urlencode([('ranges',r) for r in ranges]+[
                ('majorDimension','ROWS'),('valueRenderOption','UNFORMATTED_VALUE'),('dateTimeRenderOption','SERIAL_NUMBER')])
            data=self.get_json(base+'/values:batchGet?'+query,token)
            items=data.get('valueRanges',[])
            if data.get('spreadsheetId')!=SHEET_ID or len(items)!=len(ranges):
                raise ValueError('RANGE_COVERAGE_INVALID')
            bundle={}
            for (key,(sheet,area)),item in zip(RANGES.items(),items):
                if item.get('range') not in (f"'{sheet}'!{area}",f'{sheet}!{area}') or item.get('majorDimension')!='ROWS':
                    raise ValueError('RANGE_BINDING_INVALID')
                bundle[key]=item.get('values',[])
            bundle.update(spreadsheet_id=SHEET_ID,spreadsheet_timezone='Asia/Tokyo',
                          capture_started_at=begin.isoformat(),captured_at=clock().isoformat())
            return bundle
        except Exception:
            raise ValueError('AUTHENTICATED_SHEET_READ_FAILED') from None


@dataclass(frozen=True)
class Context:
    codes: list
    budget: Budget
    rank: Evidence
    stop: object
    cost_rate: object
    known_events: tuple
    snapshot_sha256: str


def capture(read_values, clock, *, metadata):
    """read_values(tab, range) must use the fixed authorized workbook above.

    No credential handling is performed here. A deployed Google adapter must
    verify workbook ID/timezone, use read-only credentials and bound timeouts.
    """
    if (metadata.get('spreadsheetId')!=SHEET_ID
            or metadata.get('properties',{}).get('timeZone')!='Asia/Tokyo'):
        raise ValueError('WORKBOOK_METADATA_INVALID')
    begin=clock()
    bundle={k:read_values(*v) for k,v in RANGES.items()}
    bundle.update(spreadsheet_id=SHEET_ID,spreadsheet_timezone='Asia/Tokyo',
                  capture_started_at=begin.isoformat(),captured_at=clock().isoformat())
    return bundle


def context(bundle, codes, symbol, now):
    report=project(bundle,codes,now)
    observed=sheet_time(bundle['captured_at'])
    if now-observed >= timedelta(seconds=30):
        raise ValueError('BUDGET_SNAPSHOT_STALE')
    if (not isinstance(bundle.get('budget'),list) or len(bundle['budget'])!=2
            or any(len(r)!=2 for r in bundle['budget'])
            or [r[0] for r in bundle['budget']]!=['運用資金','1取引リスク率']
            or not isinstance(bundle.get('cost'),list) or len(bundle['cost'])!=1
            or len(bundle['cost'][0])!=2 or bundle['cost'][0][0]!='費用・滑りの仮定率'):
        raise ValueError('BUDGET_HEADERS_INVALID')
    cash,rate,cost=map(money,(bundle['budget'][0][1],bundle['budget'][1][1],bundle['cost'][0][1]))
    if cash<0 or not 0<rate<=1 or not 0<=cost<=1:
        raise ValueError('BUDGET_VALUES_INVALID')
    tops=index_unique(table(bundle['top10'],TOP_HEADERS))
    if symbol not in tops:
        raise ValueError('NOT_IN_CURRENT_TOP10')
    row=tops[symbol]
    price,stop_rate=map(money,(row['終値'],row['損切り%']))
    if price<=0 or not 0<stop_rate<100:
        raise ValueError('STOP_SOURCE_INVALID')
    revision=hashlib.sha256(json.dumps([str(cash),str(rate),str(cost)]).encode()).hexdigest()
    rank=Evidence('Ver7.5',symbol,observed,observed,observed+timedelta(seconds=30),
        report['snapshot_sha256'],dict(grade=row['BUY'],recommended_shares=int(row['推奨株数']),
                                      market_date=report['market_date']))
    candidate=next(c for c in report['candidates'] if c['symbol']==symbol)
    return Context(list(codes),Budget(str(cash),str(rate),revision),rank,
        str(price*(1-stop_rate/100)),str(cost),tuple(candidate['known_near_events']),report['snapshot_sha256'])
