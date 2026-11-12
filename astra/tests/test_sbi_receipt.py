"""Exercise Apps Script source with mocked services; not a native deployment test."""
import shutil
import subprocess
import unittest
from pathlib import Path


class SbiReceiptTests(unittest.TestCase):
    def test_edit_and_receipt_contract(self):
        node = shutil.which('node')
        self.assertIsNotNone(node, 'Node is required to validate Apps Script changes')
        root = Path(__file__).resolve().parents[2]
        result = subprocess.run([node, str(root / 'apps_script/test_sbi_receipt.cjs')],
                                cwd=root, capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('14 scenarios passed', result.stdout)


if __name__ == '__main__':
    unittest.main()
