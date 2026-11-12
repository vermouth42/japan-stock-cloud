"""Execute production Apps Script source with mocked external services."""
import subprocess
import unittest
from pathlib import Path


class AutomaticCandidateNativeTests(unittest.TestCase):
    def test_projection_scheduler_and_failure_paths(self):
        root = Path(__file__).resolve().parents[2]
        result = subprocess.run(['node', 'apps_script/test_automatic_candidates.cjs'], cwd=root,
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('22 automatic candidate scenarios passed', result.stdout)
