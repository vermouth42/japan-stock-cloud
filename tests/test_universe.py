import unittest,json,tempfile
from pathlib import Path
from universe import load_universe,save_snapshot,universe_hash
class Tests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.p=Path(self.tmp.name)/'watchlist.json'
  self.x={'schema_version':1,'version':'v1','selection_asof':'2026-11-20','effective_from':'2026-11-24','codes':[str(1000+i) for i in range(99)]+['593A']}
 def tearDown(self):self.tmp.cleanup()
 def load(self):
  self.p.write_text(json.dumps(self.x));return load_universe(self.p,'2026-11-24')
 def test_valid_and_alpha(self):self.assertEqual(len(self.load()['codes']),100)
 def test_duplicate(self):
  self.x['codes'][-1]='1000'
  with self.assertRaises(ValueError):self.load()
 def test_non_string(self):
  self.x['codes'][-1]={}
  with self.assertRaises(ValueError):self.load()
 def test_selection_after_effective(self):
  self.x['selection_asof']='2026-11-25'
  with self.assertRaises(ValueError):self.load()
 def test_future(self):
  self.x['effective_from']='2026-11-25'
  with self.assertRaises(ValueError):self.load()
 def test_reject_non_code(self):
  self.x['codes'][-1]='TSE:593A'
  with self.assertRaises(ValueError):self.load()
 def test_history_immutable(self):
  save_snapshot(self.x,'2026-11-24',self.tmp.name);self.x['codes'][-1]='594A'
  with self.assertRaises(ValueError):save_snapshot(self.x,'2026-11-24',self.tmp.name)
 def test_hash_tracks_order_and_membership(self):
  old=universe_hash(self.x);self.x['codes'].reverse();self.assertNotEqual(old,universe_hash(self.x))
 def test_same_version_on_later_session(self):
  save_snapshot(self.x,'2026-11-24',self.tmp.name);self.x['codes'][-1]='594A'
  with self.assertRaises(ValueError):save_snapshot(self.x,'2026-11-25',self.tmp.name)
 def test_identical_retry(self):
  a=save_snapshot(self.x,'2026-11-24',self.tmp.name)
  self.assertEqual(a,save_snapshot(self.x,'2026-11-24',self.tmp.name))
if __name__=='__main__':unittest.main()
