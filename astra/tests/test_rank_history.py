import json
import subprocess
import tempfile
import unittest
from datetime import datetime,timedelta
from pathlib import Path
from astra.rank_history import verify,known_rank

class HistoryTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import os
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'rows.json'
            r=subprocess.run(['node','apps_script/test_rank_history.cjs'],cwd=Path(__file__).resolve().parents[2],env={**os.environ,'ASTRA_HISTORY_TEST_EXPORT':str(path)},capture_output=True,text=True,timeout=15)
            if r.returncode:raise AssertionError(r.stdout+r.stderr)
            cls.rows=json.loads(path.read_text());cls.log=r.stdout
    def test_native_source_scenarios(self):self.assertIn('10 rank history scenarios passed',self.log)
    def test_cross_language_hashes(self):self.assertEqual(len(verify(self.rows)),3)
    def test_future_rank_not_backdated(self):
        obs=verify(self.rows);t=obs[0]['observed_at'];symbol=obs[0]['payload']['ranks'][0]['symbol']
        self.assertIsNone(known_rank(obs,symbol,t-timedelta(seconds=1)))
        self.assertEqual(known_rank(obs,symbol,t)['grade'],'A')
        self.assertEqual(known_rank(obs,symbol,t+timedelta(minutes=31))['grade'],'B')
        self.assertIsNone(known_rank(obs,symbol,t+timedelta(days=2)))
    def test_deletion_reorder_and_tamper(self):
        for rows in [self.rows[1:],list(reversed(self.rows)),self.rows[:1]+self.rows[2:]]:
            with self.assertRaises(ValueError):verify(rows)
        rows=json.loads(json.dumps(self.rows));rows[0][5]+=' '
        with self.assertRaises(ValueError):verify(rows)
