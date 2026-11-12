import subprocess
import unittest
from pathlib import Path


class FinalGateTests(unittest.TestCase):
    def test_native_decision_and_full_import_audit(self):
        root = Path(__file__).resolve().parents[2]
        result = subprocess.run(['node', 'apps_script/test_final_gate.cjs'], cwd=root,
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('18 final gate scenarios passed', result.stdout)
