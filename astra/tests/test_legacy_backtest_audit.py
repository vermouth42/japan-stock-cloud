import tempfile
import unittest
from pathlib import Path
from astra.legacy_backtest_audit import read_archive, price_inventory, rank_inventory, formal_status


class LegacyAuditTest(unittest.TestCase):
    def test_forged_archive_is_rejected_before_execution(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'fake.zip';p.write_bytes(b'not the Actions artifact')
            with self.assertRaisesRegex(ValueError,'digest mismatch'):read_archive(p)

    def test_missing_rank_history_never_counts_as_zero_performance(self):
        r=rank_inventory([], '2026-10-30')
        self.assertEqual(r['observations'],0)
        self.assertIsNone(r['first_observed_at'])
        report=formal_status()
        self.assertFalse(report['formal_performance_approved'])
        self.assertFalse(report['go_allowed'])
        self.assertEqual(len(report['scenarios']),9)
        self.assertTrue(all(s['metrics'] is None for s in report['scenarios']))

    def test_inventory_detects_duplicate_and_short_warmup(self):
        blob='日付,コード\n2026-01-01,1000\n2026-01-01,1000\n2026-01-02,2000\n'.encode()
        r=price_inventory(blob)
        self.assertEqual(r['duplicate_symbol_dates'],1)
        self.assertEqual(r['symbols_below_200_bars'],2)

    def test_invalid_rank_chain_is_not_downgraded_to_a_warning(self):
        with self.assertRaises(ValueError):rank_inventory([['bad']*6], '2026-10-30')
