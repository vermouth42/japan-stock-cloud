import copy
import unittest
from astra.backtest_matrix import run_matrix,simulate,validate

def fixture():
    return {'schema':'astra.backtest.v1','universe':['1000'],'costs':{'fee_bps':5,'slippage_bps':2},'evidence':{},
     'frames':[{'open_at':'2026-11-12T09:05:00+09:00','close_at':'2026-11-12T09:06:00+09:00','session_end':False,'bars':{'1000':{'open':1000,'high':1002,'low':999,'close':1001}}},
               {'open_at':'2026-11-12T09:06:00+09:00','close_at':'2026-11-12T09:07:00+09:00','session_end':True,'bars':{'1000':{'open':1001,'high':1003,'low':998,'close':1002}}}],
     'signals':[{'id':'s1','symbol':'1000','known_at':'2026-11-12T09:04:59+09:00','expires_at':'2026-11-12T09:10:00+09:00','horizon':'day','grade':'A','conditions_verified':True,'max_shares':1000,'score':5,'stop':990,'target':1020}]}

class MatrixTest(unittest.TestCase):
    def test_all_nine_are_research_and_no_probability(self):
        r=run_matrix(fixture());self.assertEqual(len(r['scenarios']),9);self.assertFalse(r['formal_performance_approved']);self.assertFalse(r['go_allowed'])
        for s in r['scenarios']:
            self.assertIsNone(s['metrics']['future_10x_probability']);self.assertIsNone(s['metrics']['annualized_return'])
            self.assertTrue(all(t['shares']>=100 and t['shares']%100==0 for t in s['trades']))
    def test_exact_fee_slippage_pnl(self):
        r=simulate(fixture(),'day','conservative');t=r['trades'][0]
        self.assertEqual(t['shares'],100)
        expected=(1002*(1-.0002)-1000*(1+.0002))*100-(1000*(1+.0002)+1002*(1-.0002))*100*.0005
        self.assertAlmostEqual(t['pnl'],expected);self.assertAlmostEqual(r['final_capital'],1e6+expected)
    def test_future_signal_not_used_early(self):
        d=fixture();d['signals'][0]['known_at']='2026-11-12T09:05:30+09:00'
        r=simulate(d,'day','conservative');self.assertEqual(r['trades'][0]['entry_at'],'2026-11-12T09:06:00+09:00')
    def test_b_and_unverified_and_small_lot_never_enter(self):
        for key,val in [('grade','B'),('grade','C'),('conditions_verified',False),('max_shares',0)]:
            d=fixture();d['signals'][0][key]=val;self.assertEqual(simulate(d,'day','high_risk')['trades'],[])
    def test_stop_wins_ambiguous_bar(self):
        d=fixture();d['frames'][0]['bars']['1000'].update(high=1030,low=980)
        self.assertEqual(simulate(d,'day','conservative')['trades'][0]['reason'],'STOP_FIRST_IF_AMBIGUOUS')
    def test_gap_stop_and_gap_entry(self):
        d=fixture();d['frames'][1]['bars']['1000'].update(open=970,low=960)
        self.assertEqual(simulate(d,'day','conservative')['trades'][0]['reason'],'STOP_GAP')
        d=fixture();d['frames'][0]['bars']['1000'].update(open=980,low=980)
        self.assertEqual(simulate(d,'day','conservative')['trades'],[])
    def test_shared_cash_and_no_double_entry(self):
        d=fixture();s=copy.deepcopy(d['signals'][0]);s['id']='s2';d['signals'].append(s)
        r=simulate(d,'combined','high_risk');self.assertEqual(len(r['trades']),1);self.assertTrue(all(x['cash']>=0 for x in r['equity_curve']))
    def test_close_mark_includes_unrealized_drawdown(self):
        d=fixture();d['signals'][0].update(horizon='swing',stop=950,target=1100);d['frames'][0]['bars']['1000'].update(low=960,close=970)
        r=simulate(d,'swing','high_risk');self.assertGreater(r['metrics']['max_close_drawdown'],.003)
    def test_missing_bar_cost_timestamp_end_fail(self):
        changes=[lambda d:d['frames'][0]['bars'].clear(),lambda d:d['costs'].update(fee_bps=-1),lambda d:d['frames'][-1].update(session_end=False),lambda d:d['signals'][0].update(known_at='2026-11-12T09:04:00')]
        for change in changes:
            d=fixture();change(d)
            with self.assertRaises((ValueError,KeyError)):validate(d)
    def test_profiles_differ_but_no_margin(self):
        d=fixture();qs=[simulate(d,'day',p)['trades'][0]['shares'] for p in ('conservative','aggressive','high_risk')]
        self.assertEqual(qs,sorted(qs));self.assertGreater(qs[-1],qs[0])
    def test_horizon_and_final_liquidation(self):
        d=fixture();d['signals'][0]['horizon']='swing'
        self.assertEqual(simulate(d,'day','conservative')['trades'],[])
        self.assertEqual(simulate(d,'swing','conservative')['trades'][0]['reason'],'TEST_END')
    def test_intrabar_profit_cannot_fund_opening_order(self):
        d=fixture();d['universe'].append('2000')
        for f in d['frames']:f['bars']['2000']=dict(f['bars']['1000'])
        s=copy.deepcopy(d['signals'][0]);s.update(id='s2',symbol='2000',known_at='2026-11-12T09:06:00+09:00');d['signals'].append(s)
        a=simulate(d,'combined','high_risk')
        d['frames'][1]['bars']['1000']['high']=2000
        b=simulate(d,'combined','high_risk')
        self.assertEqual([t['shares'] for t in a['trades'] if t['symbol']=='2000'],[t['shares'] for t in b['trades'] if t['symbol']=='2000'])
