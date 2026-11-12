from copy import deepcopy
from dataclasses import replace
from datetime import date,timedelta
import json
from pathlib import Path
import tempfile
import unittest
from astra.automatic_preflight import Evidence
from astra.notification_outbox import Outbox,Accepted
from astra.runtime_sheet import context
from astra.webhook_pipeline import Inbox
from astra.tests import test_candidate_projection as projection_tests


def fixture():
    f=projection_tests.CandidateProjectionTest();f.setUp()
    f.bundle['budget']=[['運用資金',1000000],['1取引リスク率',.015]]
    f.bundle['cost']=[['費用・滑りの仮定率',.002]]
    for row in f.bundle['top10'][1:]:row[7]=1
    return f


def payload(f):
    return json.dumps(dict(schema='astra.tv.v1',ticker='TSE:'+f.codes[0],timeframe='1',
        bar_close_ms=int((f.now-timedelta(seconds=2)).timestamp()*1000),price=1000,vwap=999,
        volume_ok=True,wick_ok=True,gap_ok=True,kind='technical'))


class PipelineTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'db.sqlite'
        self.f=fixture();self.inbox=Inbox(self.path);self.body=payload(self.f)
        self.loader=lambda symbol,now:context(self.f.bundle,self.f.codes,symbol,now)
        self.clock=lambda:self.f.now

    def tearDown(self):self.inbox.close();self.tmp.cleanup()

    def evidence(self,source,symbol,values):
        return Evidence(source,symbol,self.f.now-timedelta(seconds=2),self.f.now,
                        self.f.now+timedelta(seconds=8),'SYNTHETIC_TEST_ONLY',values)

    def board(self,symbol,now):return self.evidence('OrderBook',symbol,dict(board_ok=True))
    def events(self,symbol,now):
        return self.evidence('EventCalendar',symbol,dict(coverage_until=date(2026,11,17),issuer_checked=True,macro_checked=True,dates=[]))

    def accept(self,body=None):return self.inbox.accept(body or self.body,now=self.f.now,transport_verified=True)

    def run_worker(self,**kw):
        return self.inbox.process_one(self.loader,clock=self.clock,**kw)

    def test_durable_acceptance_is_not_decision_or_delivery(self):
        r=self.accept();self.assertFalse(r['go_allowed']);self.assertFalse(r['notification_sent'])
        self.inbox.close();self.inbox=Inbox(self.path)
        self.assertTrue(self.accept()['duplicate'])
        self.assertEqual(self.inbox.db.execute('SELECT COUNT(*) FROM webhook_inbox').fetchone()[0],1)

    def test_conflicting_same_event_rejected(self):
        self.accept();p=json.loads(self.body);p['price']=1001
        with self.assertRaisesRegex(ValueError,'CONTENT_CONFLICT'):self.accept(json.dumps(p))

    def test_webhook_cannot_set_budget_rank_or_transport(self):
        for key in ('available_cash','grade','board_ok','transport_verified'):
            p=json.loads(self.body);p[key]=True
            with self.assertRaises(ValueError):self.accept(json.dumps(p))
        with self.assertRaises(ValueError):self.inbox.accept(self.body,now=self.f.now)

    def test_missing_real_adapters_blocks_and_records_reasons(self):
        self.accept();r=self.run_worker()
        self.assertFalse(r['candidate_allowed'])
        self.assertIn('BOARD_MISSING_OR_INVALID',r['reasons'])
        self.assertIn('EVENTS_MISSING_OR_INVALID',r['reasons'])
        self.assertEqual(self.inbox.db.execute('SELECT COUNT(*) FROM notification_outbox').fetchone()[0],0)

    def test_complete_synthetic_pipeline_enqueues_once_and_mock_delivery(self):
        self.accept();r=self.run_worker(board_loader=self.board,event_loader=self.events)
        self.assertTrue(r['candidate_allowed']);self.assertEqual(r['shares'],100);self.assertFalse(r['go_allowed'])
        self.accept();self.assertIsNone(self.run_worker(board_loader=self.board,event_loader=self.events))
        calls=[];out=Outbox(self.path)
        def sender(body,*,idempotency_key):calls.append(body);return Accepted('mock-receipt')
        try:
            self.assertEqual(out.deliver_one(sender,now=self.f.now)['state'],'accepted')
            self.assertIsNone(out.deliver_one(sender,now=self.f.now))
            self.assertEqual(len(calls),1);self.assertFalse(calls[0]['go_allowed'])
        finally:out.close()

    def test_b_rank_is_never_a_candidate(self):
        self.f.bundle['ranking'][1][16]='B';self.f.bundle['top10'][1][3]='B'
        self.accept();r=self.run_worker(board_loader=self.board,event_loader=self.events)
        self.assertIn('RANK_NOT_A',r['reasons']);self.assertEqual(r['shares'],0)

    def test_old_budget_future_capture_and_wrong_headers_stop(self):
        for mode in ('old','future','header'):
            f=fixture()
            if mode=='old':f.now+=timedelta(seconds=31)
            if mode=='future':f.bundle['captured_at']=(f.now+timedelta(seconds=1)).isoformat()
            if mode=='header':f.bundle['budget'][0][0]='買付余力'
            with self.subTest(mode=mode),self.assertRaises(ValueError):context(f.bundle,f.codes,f.codes[0],f.now)

    def test_expired_event_does_not_read_sheet(self):
        self.accept();calls=[]
        result=self.inbox.process_one(lambda *a:calls.append(a),clock=lambda:self.f.now+timedelta(seconds=100))
        self.assertIsNone(result);self.assertEqual(calls,[])
        self.assertEqual(self.inbox.db.execute('SELECT state FROM webhook_inbox').fetchone()[0],'expired')

    def test_adapter_delay_rechecks_time_before_reserving(self):
        self.accept();times=iter([self.f.now,self.f.now+timedelta(seconds=100)])
        r=self.inbox.process_one(self.loader,clock=lambda:next(times),board_loader=self.board,event_loader=self.events)
        self.assertFalse(r['candidate_allowed']);self.assertIn('MARKET_STALE',r['reasons'])

    def test_weakness_does_not_assume_holdings(self):
        p=json.loads(self.body);p['kind']='weakness';self.accept(json.dumps(p))
        r=self.inbox.process_one(lambda *a:self.fail('no entry context for weakness'),clock=self.clock)
        self.assertEqual(r['reasons'],['HOLDINGS_NOT_CONNECTED']);self.assertFalse(r['notify'])

    def test_known_sheet_event_cannot_be_cleared_by_adapter(self):
        self.f.bundle['events'].append([self.f.codes[0],'fixture','2026/11/13','決算','','',0,'',''])
        self.accept();r=self.run_worker(board_loader=self.board,event_loader=self.events)
        self.assertIn('KNOWN_EVENT_WITHIN_3_JPX_SESSIONS',r['reasons'])

    def test_provider_error_text_is_not_persisted(self):
        self.accept()
        def broken(*a):raise RuntimeError('PRIVATE_TOKEN_MUST_NOT_APPEAR')
        r=self.inbox.process_one(broken,clock=self.clock)
        self.assertNotIn('PRIVATE_TOKEN',json.dumps(r))
        self.assertNotIn('PRIVATE_TOKEN',self.inbox.db.execute('SELECT result FROM webhook_inbox').fetchone()[0])
