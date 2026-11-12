"""Durable authenticated observations -> Sheet context -> reservation/outbox.

No network sender or broker. Reception is independent of slow adapters. Replay
uses the original reception timestamp; expired events never become fresh again.
"""
from datetime import datetime, timedelta
import hashlib
import json
import sqlite3
from .automatic_preflight import aware, money
from .input_budget import Ledger
from .runtime_sheet import Context
from .tv_payload import parse


class Inbox:
    def __init__(self,path):
        self.path=str(path)
        self.db=sqlite3.connect(path,timeout=.15,isolation_level=None)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.execute('''CREATE TABLE IF NOT EXISTS webhook_inbox (
            id TEXT PRIMARY KEY, digest TEXT NOT NULL, body TEXT NOT NULL,
            received_at TEXT NOT NULL, expires REAL NOT NULL,
            state TEXT NOT NULL, started REAL, result TEXT)''')

    def accept(self,body,*,now,transport_verified=False):
        event=parse(body,now,transport_verified=transport_verified)
        canonical=json.dumps(json.loads(body),sort_keys=True,separators=(',',':'),allow_nan=False)
        digest=hashlib.sha256(canonical.encode()).hexdigest()
        self.db.execute('BEGIN IMMEDIATE')
        try:
            old=self.db.execute('SELECT digest FROM webhook_inbox WHERE id=?',(event['event_id'],)).fetchone()
            if old and old[0]!=digest:
                raise ValueError('EVENT_ID_CONTENT_CONFLICT')
            if not old:
                self.db.execute('INSERT INTO webhook_inbox VALUES (?,?,?,?,?,?,NULL,NULL)',
                    (event['event_id'],digest,canonical,now.isoformat(),event['market'].expires_at.timestamp(),'queued'))
            self.db.execute('COMMIT')
        except Exception:
            self.db.execute('ROLLBACK');raise
        return dict(accepted=True,duplicate=bool(old),event_id=event['event_id'],
                    go_allowed=False,notification_sent=False)

    def process_one(self,context_loader,*,clock,board_loader=None,event_loader=None):
        now=clock()
        if not aware(now):raise ValueError('AWARE_CLOCK_REQUIRED')
        self.db.execute('BEGIN IMMEDIATE')
        try:
            self.db.execute("UPDATE webhook_inbox SET state='expired' WHERE state IN ('queued','processing') AND expires<=?",(now.timestamp(),))
            # Reprocess an interrupted worker only while the original event is
            # still valid. Ledger event-id uniqueness prevents double funding.
            self.db.execute("UPDATE webhook_inbox SET state='queued' WHERE state='processing' AND started<=?",(now.timestamp()-30,))
            row=self.db.execute("SELECT id,body,received_at FROM webhook_inbox WHERE state='queued' ORDER BY received_at,id LIMIT 1").fetchone()
            if row:self.db.execute("UPDATE webhook_inbox SET state='processing',started=? WHERE id=?",(now.timestamp(),row[0]))
            self.db.execute('COMMIT')
        except Exception:
            self.db.execute('ROLLBACK');raise
        if not row:return None
        event_id,body,received=row
        try:
            event=parse(body,datetime.fromisoformat(received),transport_verified=True)
            if event['kind']=='weakness':
                result=dict(decision='保有情報の照合待ち',reasons=['HOLDINGS_NOT_CONNECTED'],
                            candidate_allowed=False,go_allowed=False,notify=False)
            else:
                symbol=event['market'].symbol
                c=context_loader(symbol,now)
                if not isinstance(c,Context) or c.rank.symbol!=symbol:
                    raise ValueError('CONTEXT_IDENTITY_MISMATCH')
                if c.known_events:
                    result=dict(decision='見送り',reasons=['KNOWN_EVENT_WITHIN_3_JPX_SESSIONS'],
                                candidate_allowed=False,go_allowed=False,notify=False)
                else:
                    evidence={'market':event['market'],'rank':c.rank}
                    if board_loader:evidence['board']=board_loader(symbol,now)
                    if event_loader:evidence['events']=event_loader(symbol,now)
                    ledger=Ledger(self.path)
                    try:
                        result=ledger.decide_and_reserve(event_id,symbol,c.codes,evidence,c.budget,
                            stop=c.stop,cost_per_share=money(event['market'].values['price'])*money(c.cost_rate),now=clock())
                        result['snapshot_sha256']=c.snapshot_sha256
                    finally:ledger.close()
        except Exception:
            # Exceptions can contain credentials/source URLs. Do not persist them.
            result=dict(decision='処理停止',reasons=['SOURCE_ADAPTER_FAILED'],
                        candidate_allowed=False,go_allowed=False,notify=False)
        self.db.execute("UPDATE webhook_inbox SET state='processed',result=? WHERE id=? AND state='processing'",
                        (json.dumps(result,ensure_ascii=False),event_id))
        return result

    def close(self):self.db.close()
