import json
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime,timedelta,date
from pathlib import Path
from astra.input_budget import Budget,Ledger,evaluate,size
from astra.automatic_preflight import Evidence
from astra.market_data import JST,ROOT
from astra.tv_payload import parse


class InputBudgetTests(unittest.TestCase):
    def setUp(self):
        self.now=datetime(2026,11,12,10,0,2,tzinfo=JST)
        self.codes=json.loads((ROOT/'watchlist.json').read_text())
        self.symbol=self.codes[0]
        self.budget=Budget(1000000,.015,'revision1')
        self.ev={}
        fields={
            'market':('TradingView',dict(price=1000,vwap=999,volume_ok=True,wick_ok=True,gap_ok=True)),
            'rank':('Ver7.5',dict(grade='A',recommended_shares=1000,market_date='2026-11-11')),
            'events':('EventCalendar',dict(coverage_until=date(2026,11,17),issuer_checked=True,macro_checked=True,dates=[])),
            'board':('OrderBook',dict(board_ok=True))}
        for k,(s,v) in fields.items():
            self.ev[k]=Evidence(s,self.symbol,self.now-timedelta(seconds=2),self.now,self.now+timedelta(seconds=8),'fixture',v)

    def decide(self,**kw):
        return evaluate(self.symbol,self.codes,self.ev,self.budget,stop=990,cost_per_share=2,now=self.now,**kw)

    def test_input_budget_does_not_require_sbi(self):
        r=self.decide();self.assertTrue(r['candidate_allowed']);self.assertEqual(r['shares'],900)
        self.assertEqual(r['required_cash'],901800);self.assertFalse(r['balance_verified'])
        self.assertFalse(r['go_allowed']);self.assertFalse(r['broker_order_allowed'])

    def test_b_c_and_small_lots_rejected(self):
        for g in ('B','C'):
            self.ev['rank'].values['grade']=g;self.assertEqual(self.decide()['shares'],0)
        self.ev['rank'].values.update(grade='A',recommended_shares=0)
        self.assertFalse(self.decide()['candidate_allowed'])

    def test_cost_and_risk_rounding(self):
        r=size(Budget(1000000,.015,'v'),price=2153,stop='1948.465',cost_per_share=0,recommended_shares=400)
        self.assertEqual(r['shares'],0)
        r=size(Budget('100000.01',.5,'v'),price=1000,stop=990,cost_per_share='.0002',recommended_shares=100)
        self.assertEqual(r['shares'],0)

    def test_missing_invalid_zero_budget(self):
        for val in (None,True,'NaN',-1):
            self.budget=Budget(val,.015,'v');self.assertFalse(self.decide()['candidate_allowed'])
        self.budget=Budget(0,.015,'v');self.assertEqual(self.decide()['shares'],0)

    def test_missing_board_stays_blocked(self):
        del self.ev['board'];self.assertIn('BOARD_MISSING_OR_INVALID',self.decide()['reasons'])

    def test_expiry_and_future_rejected(self):
        self.ev['market']=replace(self.ev['market'],observed_at=self.now+timedelta(seconds=1))
        self.assertIn('MARKET_STALE',self.decide()['reasons'])
        self.setUp();self.ev['board']=replace(self.ev['board'],expires_at=self.now)
        self.assertIn('BOARD_STALE',self.decide()['reasons'])

    def test_event_coverage_and_three_business_days(self):
        self.ev['events'].values['dates']=[date(2026,11,17)]
        self.assertIn('EVENT_WITHIN_3_SESSIONS',self.decide()['reasons'])
        self.ev['events'].values.update(dates=[],macro_checked=False)
        self.assertIn('EVENT_COVERAGE_MISSING',self.decide()['reasons'])

    def test_technical_false_and_stale_rank(self):
        self.ev['market'].values['volume_ok']='true'
        self.ev['rank'].values['market_date']='2026-11-10'
        self.assertIn('TECHNICAL_OR_BOARD_FAILED',self.decide()['reasons'])
        self.assertIn('RANK_MARKET_DATE_STALE',self.decide()['reasons'])

    def test_ledger_persists_and_does_not_reuse_budget(self):
        with tempfile.TemporaryDirectory() as d:
            path=str(Path(d)/'ledger.db');a=Ledger(path)
            kw=dict(stop=990,cost_per_share=2,now=self.now)
            r=a.decide_and_reserve('1',self.symbol,self.codes,self.ev,self.budget,**kw)
            self.assertTrue(r['notify']);a.close();b=Ledger(path)
            self.assertFalse(b.decide_and_reserve('1',self.symbol,self.codes,self.ev,self.budget,**kw)['notify'])
            self.assertFalse(b.decide_and_reserve('2',self.symbol,self.codes,self.ev,self.budget,**kw)['candidate_allowed'])
            symbol=self.codes[1];ev={k:replace(v,symbol=symbol) for k,v in self.ev.items()}
            r=b.decide_and_reserve('3',symbol,self.codes,ev,self.budget,**kw)
            self.assertEqual(r['shares'],0)
            # A budget revision never silently restores cash committed earlier.
            r=b.decide_and_reserve('4',symbol,self.codes,ev,replace(self.budget,revision='v2'),**kw)
            self.assertEqual(r['shares'],0)
            with self.assertRaises(ValueError):b.release('1',user_confirmed=False)
            b.release('1',user_confirmed=True)
            self.assertTrue(b.decide_and_reserve('5',symbol,self.codes,ev,self.budget,**kw)['candidate_allowed'])
            b.close()

    def payload(self):
        return dict(schema='astra.tv.v1',ticker='TSE:'+self.symbol,timeframe='1',
                    bar_close_ms=int((self.now-timedelta(seconds=2)).timestamp()*1000),
                    price=1000,vwap=999,volume_ok=True,wick_ok=True,gap_ok=True,kind='technical')

    def test_webhook_auth_and_schema(self):
        p=self.payload()
        with self.assertRaises(ValueError):parse(json.dumps(p),self.now)
        r=parse(json.dumps(p),self.now,transport_verified=True)
        self.assertEqual(r['market'].symbol,self.symbol)
        for k,v in [('grade','A+'),('board_ok',True),('available_cash',1000000)]:
            with self.assertRaises(ValueError):parse(json.dumps(dict(p,**{k:v})),self.now,transport_verified=True)

    def test_webhook_rejects_nonfinite_duplicate_old_future(self):
        p=self.payload()
        for body in (json.dumps(dict(p,price=float('nan'))),json.dumps(p)[:-1]+',"price":1}'):
            with self.assertRaises(ValueError):parse(body,self.now,transport_verified=True)
        for delta in (-100,1):
            p['bar_close_ms']=int((self.now+timedelta(seconds=delta)).timestamp()*1000)
            with self.assertRaises(ValueError):parse(json.dumps(p),self.now,transport_verified=True)

    def test_weakness_is_not_entry_or_holdings(self):
        p=self.payload();p['kind']='weakness'
        self.assertFalse(parse(json.dumps(p),self.now,transport_verified=True)['entry_eligible_event'])

    def test_input_not_mutated(self):
        before=dict(self.ev['rank'].values);self.decide()
        self.assertEqual(before,self.ev['rank'].values)


if __name__=='__main__':unittest.main()
