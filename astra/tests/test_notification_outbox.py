import sqlite3
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

from astra.input_budget import Ledger
from astra.notification_outbox import Outbox,Accepted,DefinitelyNotSent
from astra.tests import test_input_budget as fixtures


class OutboxTests(unittest.TestCase):
    def setUp(self):
        fixture=fixtures.InputBudgetTests();fixture.setUp()
        self.f=fixture;self.tmp=tempfile.TemporaryDirectory()
        self.path=str(Path(self.tmp.name)/'ledger.db')
        self.ledger=Ledger(self.path);self.queue=Outbox(self.path)
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(self.ledger.close);self.addCleanup(self.queue.close)

    def reserve(self,event='event1'):
        f=self.f
        return self.ledger.decide_and_reserve(event,f.symbol,f.codes,f.ev,f.budget,
                                              stop=990,cost_per_share=2,now=f.now)

    def test_reservation_and_queue_rollback_together(self):
        self.ledger.db.execute("CREATE TRIGGER fail_queue BEFORE INSERT ON notification_outbox BEGIN SELECT RAISE(ABORT,'fixture'); END")
        with self.assertRaises(sqlite3.IntegrityError):self.reserve()
        self.assertEqual(self.ledger.db.execute('SELECT COUNT(*) FROM reservations').fetchone()[0],0)
        self.assertIsNone(self.queue.status('event1'))

    def test_duplicate_reservation_one_provider_acceptance(self):
        self.reserve();self.reserve()
        calls=[]
        def send(body,**kw):
            calls.append((body,kw));return Accepted('provider-id')
        self.assertEqual(self.queue.deliver_one(send,now=self.f.now)['state'],'accepted')
        self.assertIsNone(self.queue.deliver_one(send,now=self.f.now))
        self.assertEqual(len(calls),1);self.assertFalse(calls[0][0]['go_allowed'])
        self.assertEqual(calls[0][1]['idempotency_key'],'event1')
        self.assertFalse(self.queue.status('event1')['phone_delivery_verified'])

    def test_concurrent_worker_cannot_send_claimed_record(self):
        self.reserve();other=Outbox(self.path)
        self.addCleanup(other.close)
        def send(body,**kw):
            self.assertIsNone(other.deliver_one(lambda *_:self.fail('duplicate'),now=self.f.now))
            return Accepted('accepted')
        self.queue.deliver_one(send,now=self.f.now)

    def test_definite_failure_retries_same_id_with_limit(self):
        self.reserve();calls=[]
        def fail(body,**kw):
            calls.append(kw['idempotency_key']);raise DefinitelyNotSent()
        self.queue.deliver_one(fail,now=self.f.now)
        self.assertIsNone(self.queue.deliver_one(fail,now=self.f.now+timedelta(seconds=1)))
        self.queue.deliver_one(fail,now=self.f.now+timedelta(seconds=2))
        result=self.queue.deliver_one(fail,now=self.f.now+timedelta(seconds=6))
        self.assertEqual(result['state'],'failed');self.assertEqual(calls,['event1']*3)
        self.assertEqual(self.ledger.db.execute('SELECT active FROM reservations').fetchone()[0],1)

    def test_ambiguous_timeout_is_not_resent_after_restart(self):
        self.reserve()
        def timeout(*_,**kw):raise TimeoutError('secret must not be persisted')
        self.assertEqual(self.queue.deliver_one(timeout,now=self.f.now)['state'],'uncertain')
        self.queue.close();self.queue=Outbox(self.path);self.addCleanup(self.queue.close)
        self.assertIsNone(self.queue.deliver_one(timeout,now=self.f.now+timedelta(seconds=2)))
        self.assertNotIn('secret',str(self.queue.db.execute('SELECT * FROM notification_outbox').fetchall()))

    def test_expired_record_never_delivered_and_funds_remain(self):
        self.reserve()
        self.assertIsNone(self.queue.deliver_one(None,now=self.f.now+timedelta(seconds=8)))
        self.assertEqual(self.queue.status('event1')['state'],'expired')
        self.assertEqual(self.ledger.db.execute('SELECT active FROM reservations').fetchone()[0],1)

    def test_user_release_cancels_queued_observation(self):
        self.reserve();self.ledger.release('event1',user_confirmed=True)
        self.assertIsNone(self.queue.deliver_one(None,now=self.f.now))
        self.assertEqual(self.queue.status('event1')['state'],'cancelled')

    def test_crash_after_claim_recovery_does_not_resend(self):
        self.reserve()
        def crash(*_,**kw):raise SystemExit()
        with self.assertRaises(SystemExit):self.queue.deliver_one(crash,now=self.f.now)
        self.assertEqual(self.queue.status('event1')['state'],'sending')
        self.queue.recover_interrupted(now=self.f.now+timedelta(seconds=31))
        self.assertEqual(self.queue.status('event1')['state'],'uncertain')
        self.assertIsNone(self.queue.deliver_one(None,now=self.f.now+timedelta(seconds=31)))

    def test_blocked_candidate_does_not_enqueue(self):
        self.f.ev['rank'].values['grade']='B';self.reserve()
        self.assertIsNone(self.queue.status('event1'))


if __name__=='__main__':unittest.main()
