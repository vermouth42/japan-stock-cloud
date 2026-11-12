import unittest
from copy import deepcopy
from dataclasses import replace
from datetime import date, datetime, timedelta

from astra.automatic_preflight import Evidence, RiskPlan, assess, TTL
from astra.market_data import JST


class AutomaticPreflightTest(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 11, 11, 10, 0, tzinfo=JST)
        self.codes = [str(1000+i) for i in range(100)]
        self.risk = RiskPlan(stop=950, allowed_loss=10000, cost_per_share=2)
        self.sync = dict(data_ready=True, expected_market_date='2026-11-10',
                         sheet_stocks=100, sheet_rows=4500, sheet_duplicate_keys=0, reasons=[])
        raw = {
            'market': ('TradingView', dict(price=1000, vwap=1000, volume_ok=True, wick_ok=True, gap_ok=True)),
            'rank': ('Ver7.5', dict(grade='A', recommended_shares=300)),
            'events': ('EventCalendar', dict(coverage_until=date(2026,11,16), issuer_checked=True,
                                            macro_checked=True, dates=[])),
            'account': ('SBI', dict(available_cash=1000000, remaining_risk=10000,
                                   position_shares=0, pending_buy_shares=0, pending_sell_shares=0,
                                   positions_complete=True, orders_complete=True)),
            'board': ('synthetic-board-provider', dict(board_ok=True)),
        }
        self.evidence = {k: Evidence(src, '1000', self.now, self.now,
                                    self.now+timedelta(minutes=10), 'synthetic-only', value)
                         for k,(src,value) in raw.items()}

    def run_case(self, **kwargs):
        args = dict(symbol='1000', watchlist=self.codes, evidence=self.evidence,
                    risk=self.risk, now=self.now, sync_report=self.sync,
                    board_source='synthetic-board-provider')
        args.update(kwargs)
        return assess(**args)

    def with_value(self, stream, **changes):
        data = deepcopy(self.evidence)
        data[stream] = replace(data[stream], values={**data[stream].values, **changes})
        return data

    def test_complete_fixture_sizes_lot_but_never_live_go(self):
        d = self.run_case()
        self.assertTrue(d['offline_checks_passed'], d)
        self.assertFalse(d['go_allowed'])
        self.assertEqual(d['proposed_shares'], 100)
        self.assertEqual(d['planned_loss'], '5200')
        self.assertEqual(d['valid_until'], (self.now+timedelta(seconds=10)).isoformat())

    def test_every_missing_stream_blocks_and_zeroes_size(self):
        for key in self.evidence:
            e = dict(self.evidence); del e[key]
            with self.subTest(key=key):
                d = self.run_case(evidence=e)
                self.assertFalse(d['offline_checks_passed'])
                self.assertEqual(d['proposed_shares'], 0)
                self.assertIn(key.upper()+'_MISSING', d['reasons'])

    def test_source_symbol_and_reference_are_separate(self):
        for key in self.evidence:
            for change in [dict(source='other'), dict(symbol='1001'), dict(reference=' ')]:
                e = dict(self.evidence); e[key] = replace(e[key], **change)
                with self.subTest(key=key, change=change):
                    self.assertIn(key.upper()+'_PROVENANCE', self.run_case(evidence=e)['reasons'])
        self.assertIn('BOARD_SOURCE_NOT_CONFIGURED', self.run_case(board_source=None)['reasons'])

    def test_received_now_cannot_refresh_old_observation(self):
        for key in self.evidence:
            e = dict(self.evidence)
            e[key] = replace(e[key], observed_at=self.now-timedelta(seconds=TTL[key]))
            with self.subTest(key=key):
                self.assertIn(key.upper()+'_STALE_OR_INVALID_TIME', self.run_case(evidence=e)['reasons'])

    def test_future_naive_expired_and_reversed_time_block(self):
        for key in self.evidence:
            for change in [dict(observed_at=self.now+timedelta(seconds=1)),
                           dict(received_at=self.now+timedelta(seconds=1)),
                           dict(received_at=self.now-timedelta(seconds=1)),
                           dict(expires_at=self.now), dict(observed_at=self.now.replace(tzinfo=None))]:
                e = dict(self.evidence); e[key] = replace(e[key], **change)
                with self.subTest(key=key, change=change):
                    self.assertFalse(self.run_case(evidence=e)['offline_checks_passed'])

    def test_old_rank_and_asynchronous_quotes_block(self):
        e = dict(self.evidence)
        e['rank'] = replace(e['rank'], observed_at=self.now-timedelta(days=1))
        self.assertIn('RANK_STALE_OR_INVALID_TIME', self.run_case(evidence=e)['reasons'])
        e = dict(self.evidence)
        e['market'] = replace(e['market'], observed_at=self.now-timedelta(seconds=16))
        self.assertIn('MARKET_BOARD_ACCOUNT_TIME_SKEW', self.run_case(evidence=e)['reasons'])

    def test_rank_and_minimum_lot(self):
        for rank in ['B', 'C', '', None]:
            self.assertIn('RANK_NOT_A', self.run_case(evidence=self.with_value('rank', grade=rank))['reasons'])
        for qty in [99,150,True,'100',100.0]:
            self.assertIn('RECOMMENDED_LOT_INVALID', self.run_case(evidence=self.with_value('rank', recommended_shares=qty))['reasons'])
        self.assertTrue(self.run_case(evidence=self.with_value('rank', grade='A+'))['offline_checks_passed'])

    def test_unknown_is_not_ok(self):
        for stream,key in [('market','volume_ok'),('market','wick_ok'),('market','gap_ok'),('board','board_ok'),
                           ('account','positions_complete'),('account','orders_complete')]:
            for value in [None,False,1,'true']:
                with self.subTest(stream=stream,key=key,value=value):
                    self.assertFalse(self.run_case(evidence=self.with_value(stream, **{key:value}))['offline_checks_passed'])

    def test_pending_orders_and_existing_holdings_block_duplicate_entry(self):
        for key in ['position_shares','pending_buy_shares','pending_sell_shares']:
            for qty in [100,-1,True,'0']:
                self.assertFalse(self.run_case(evidence=self.with_value('account', **{key:qty}))['offline_checks_passed'])

    def test_event_unknown_near_and_incomplete_coverage(self):
        for change in [dict(dates=None), dict(dates=['2026-11-16']), dict(dates=[date(2026,11,16)]),
                       dict(dates=[self.now.date()]), dict(coverage_until=date(2026,11,13)),
                       dict(macro_checked=False), dict(issuer_checked=None)]:
            self.assertFalse(self.run_case(evidence=self.with_value('events', **change))['offline_checks_passed'])
        self.assertTrue(self.run_case(evidence=self.with_value('events', dates=[date(2026,11,17)]))['offline_checks_passed'])

    def test_holiday_horizon_counts_three_jpx_sessions(self):
        now = self.now.replace(day=20)
        e = {k:replace(v,observed_at=now,received_at=now,expires_at=now+timedelta(minutes=10)) for k,v in self.evidence.items()}
        e['events'] = replace(e['events'], values=dict(coverage_until=date(2026,11,25), issuer_checked=True,macro_checked=True,dates=[]))
        d = self.run_case(evidence=e,now=now,sync_report={**self.sync,'expected_market_date':'2026-11-19'})
        self.assertIn('EVENT_COVERAGE_INCOMPLETE',d['reasons'])
        e['events'].values['coverage_until'] = date(2026,11,26)
        e['events'].values['dates'] = [date(2026,11,26)]
        self.assertIn('EVENT_WITHIN_3_SESSIONS',self.run_case(evidence=e,now=now,sync_report={**self.sync,'expected_market_date':'2026-11-19'})['reasons'])

    def test_price_risk_cost_and_cash_limits(self):
        for value in [None,True,float('nan'),float('inf'),0,-1]:
            self.assertFalse(self.run_case(evidence=self.with_value('market', price=value))['offline_checks_passed'])
        for risk in [RiskPlan(),replace(self.risk,stop=1000),replace(self.risk,allowed_loss=100),
                     replace(self.risk,cost_per_share=None),replace(self.risk,cost_per_share=-1)]:
            self.assertFalse(self.run_case(risk=risk)['offline_checks_passed'])
        self.assertIn('VWAP_OR_STOP',self.run_case(evidence=self.with_value('market',price=999))['reasons'])
        self.assertIn('INSUFFICIENT_RISK_OR_CASH',self.run_case(evidence=self.with_value('account',available_cash=100199))['reasons'])
        self.assertTrue(self.run_case(evidence=self.with_value('account',available_cash=100200))['offline_checks_passed'])

    def test_sync_schema_watchlist_and_session(self):
        for change in [dict(data_ready=False),dict(expected_market_date='2026-11-09'),dict(sheet_rows=4499),dict(reasons=['error'])]:
            self.assertIn('SYNC_NOT_VERIFIED',self.run_case(sync_report={**self.sync,**change})['reasons'])
        self.assertIn('SYMBOL_OR_WATCHLIST',self.run_case(watchlist=self.codes[:99]+[self.codes[0]])['reasons'])
        for hour,minute in [(8,59),(11,25),(12,29),(14,55)]:
            self.assertIn('OUTSIDE_ENTRY_SESSION',self.run_case(now=self.now.replace(hour=hour,minute=minute))['reasons'])
        self.assertIn('MARKET_SCHEMA',self.run_case(evidence=self.with_value('market',fake_ok=True))['reasons'])
        with self.assertRaises(ValueError):self.run_case(now=self.now.replace(tzinfo=None))


if __name__ == '__main__':
    unittest.main()
