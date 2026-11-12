"""Transactional candidate-observation delivery queue; no live GO or orders.

The injected sender is an adapter, not implemented here. It must use a bounded
timeout, the supplied idempotency key, and a recipient configured outside the
webhook payload. Provider acceptance never proves phone delivery or execution.
"""
from dataclasses import dataclass
from datetime import datetime
import json
import sqlite3

from .automatic_preflight import aware


def initialize(db):
    db.execute('''CREATE TABLE IF NOT EXISTS notification_outbox (
        id TEXT PRIMARY KEY, body TEXT NOT NULL, expires REAL NOT NULL,
        state TEXT NOT NULL DEFAULT 'ready', attempts INTEGER NOT NULL DEFAULT 0,
        next_attempt REAL NOT NULL, started REAL, provider_receipt TEXT)''')


def enqueue(db, event_id, symbol, decision, now):
    """Called inside the reservation transaction; rollback both on failure."""
    if not db.in_transaction or not aware(now):
        raise ValueError('RESERVATION_TRANSACTION_REQUIRED')
    expires = datetime.fromisoformat(decision['valid_until'])
    if not aware(expires) or expires <= now or not decision['candidate_allowed']:
        raise ValueError('INVALID_NOTIFICATION_DEADLINE')
    body = dict(event_id=event_id, symbol=symbol, kind='candidate_observation',
                title='検証用候補・実戦GO未解禁', go_allowed=False,
                message='入力条件の照合結果です。注文・約定・実保有の確認ではありません。',
                shares=decision['shares'], planned_loss=decision['planned_loss'],
                required_cash=decision['required_cash'],
                budget_revision=decision['budget_revision'],
                valid_until=decision['valid_until'])
    db.execute('INSERT INTO notification_outbox(id,body,expires,next_attempt) VALUES (?,?,?,?)',
               (event_id,json.dumps(body,ensure_ascii=False),expires.timestamp(),now.timestamp()))


class DefinitelyNotSent(Exception):
    """Only for confirmed provider rejection before accepting any message.

    Timeouts and connection resets are ambiguous and MUST NOT use this type.
    """


@dataclass(frozen=True)
class Accepted:
    provider_receipt: str


class Outbox:
    def __init__(self, path):
        self.db = sqlite3.connect(path,timeout=10,isolation_level=None)
        initialize(self.db)

    def deliver_one(self, sender, *, now):
        if not aware(now):
            raise ValueError('AWARE_CLOCK_REQUIRED')
        stamp = now.timestamp()
        self.db.execute('BEGIN IMMEDIATE')
        try:
            # Release never erases an audit record. Cancel queued observations.
            self.db.execute("""UPDATE notification_outbox SET state='cancelled'
                WHERE state='ready' AND NOT EXISTS (SELECT 1 FROM reservations r
                WHERE r.id=notification_outbox.id AND r.active=1)""")
            self.db.execute("UPDATE notification_outbox SET state='expired' WHERE state='ready' AND expires<=?",(stamp,))
            row = self.db.execute("""SELECT id,body,attempts FROM notification_outbox
                WHERE state='ready' AND next_attempt<=? ORDER BY next_attempt,id LIMIT 1""",(stamp,)).fetchone()
            if row:
                self.db.execute("UPDATE notification_outbox SET state='sending',attempts=attempts+1,started=? WHERE id=?",(stamp,row[0]))
            self.db.execute('COMMIT')
        except Exception:
            self.db.execute('ROLLBACK')
            raise
        if not row:
            return None
        event_id, body, previous_attempts = row
        # No database lock is held during external delivery. A second worker
        # sees 'sending', so it cannot transmit the same observation.
        try:
            receipt = sender(json.loads(body), idempotency_key=event_id)
            if (not isinstance(receipt,Accepted) or not isinstance(receipt.provider_receipt,str)
                    or not receipt.provider_receipt.strip() or len(receipt.provider_receipt)>200):
                raise ValueError('AMBIGUOUS_PROVIDER_RESULT')
        except DefinitelyNotSent:
            self.db.execute("""UPDATE notification_outbox SET
                state=CASE WHEN attempts>=3 THEN 'failed' ELSE 'ready' END,
                next_attempt=?,started=NULL WHERE id=? AND state='sending'""",
                (stamp+2**(previous_attempts+1),event_id))
        except Exception:
            # Never log exception text: it can contain a token or recipient.
            self.db.execute("UPDATE notification_outbox SET state='uncertain' WHERE id=? AND state='sending'",(event_id,))
        else:
            self.db.execute("UPDATE notification_outbox SET state='accepted',provider_receipt=? WHERE id=? AND state='sending'",
                            (receipt.provider_receipt,event_id))
        return self.status(event_id)

    def recover_interrupted(self, *, now):
        """After 30 seconds, flag abandoned sends for inspection; never resend."""
        if not aware(now):
            raise ValueError('AWARE_CLOCK_REQUIRED')
        self.db.execute("UPDATE notification_outbox SET state='uncertain' WHERE state='sending' AND started<=?",(now.timestamp()-30,))

    def status(self, event_id):
        row = self.db.execute('SELECT state,attempts FROM notification_outbox WHERE id=?',(event_id,)).fetchone()
        return None if row is None else dict(event_id=event_id,state=row[0],attempts=row[1],
                                            phone_delivery_verified=False,go_allowed=False)

    def close(self):
        self.db.close()
