import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import textwrap
import unittest


ROOT = Path(__file__).resolve().parents[1]


class CsvManifestTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        workflow = (ROOT / '.github/workflows/main.yml').read_text()
        block = workflow.split('      - name: Bind status to exact CSV contents\n', 1)[1]
        cls.code = textwrap.dedent(block.split("          python - <<'PYTHON'\n", 1)[1].split('          PYTHON', 1)[0])
        compile(cls.code, 'workflow-manifest', 'exec')

    def run_step(self, raw, status):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            if raw is not None:
                (folder / 'prices.csv').write_bytes(raw)
            before = json.dumps(status, ensure_ascii=False).encode()
            (folder / 'status.json').write_bytes(before)
            result = subprocess.run(['python', '-c', self.code], cwd=folder, capture_output=True)
            after = (folder / 'status.json').read_bytes()
            return result.returncode, before, after

    def test_actual_data_preserves_metadata_and_csv_digest(self):
        raw = (ROOT / 'prices.csv').read_bytes()
        status = json.loads((ROOT / 'status.json').read_text())
        rc, _, after = self.run_step(raw, status)
        self.assertEqual(rc, 0)
        result = json.loads(after)
        self.assertEqual(result.pop('prices_sha256'), hashlib.sha256(raw).hexdigest())
        status.pop('prices_sha256', None)
        self.assertEqual(result, status)

    def test_exact_utf8_bom_and_line_endings_are_bound(self):
        status = {'rows': 1, 'latest_market_date': '2026-11-11', 'failed': []}
        samples = ['日付,コード\n2026-11-11,285A\n'.encode(), '\ufeff日付,コード\r\n2026-11-11,285A\r\n'.encode()]
        digests = []
        for raw in samples:
            rc, _, after = self.run_step(raw, status)
            self.assertEqual(rc, 0)
            digest = json.loads(after)['prices_sha256']
            self.assertEqual(digest, hashlib.sha256(raw).hexdigest())
            digests.append(digest)
        self.assertNotEqual(*digests)

    def test_missing_csv_does_not_publish_success_manifest(self):
        rc, before, after = self.run_step(None, {'rows': 1, 'failed': []})
        self.assertNotEqual(rc, 0)
        self.assertEqual(before, after)


if __name__ == '__main__':
    unittest.main()
