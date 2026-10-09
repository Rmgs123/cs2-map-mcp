"""Core: job results survive the process that started them; Hammer dirty maps are never overwritten."""
import json
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from tests.helpers import ROOT


class JobRecovery(unittest.TestCase):
    def test_result_recovered_after_starter_exits(self):
        from cs2map.jobs import JobManager
        with tempfile.TemporaryDirectory() as tmp:
            # A separate Python process starts a 2 s job and exits at once, like a short script.
            starter = (
                'import sys; from cs2map.jobs import JobManager; from pathlib import Path\n'
                f'j = JobManager(Path(r"{tmp}")).start("compile", "t", [sys.executable, "-c", '
                '"import time; time.sleep(2); print(\'OK: 3 compiled, 0 failed, 0 skipped\')"], Path("."))\n'
                'print(j.id)')
            out = subprocess.run([sys.executable, '-c', starter], capture_output=True, text=True,
                                 env={'PYTHONPATH': str(ROOT / 'src'), 'SYSTEMROOT': 'C:\\Windows'}, timeout=60)
            job_id = out.stdout.strip().splitlines()[-1]
            from cs2map import compiler  # registers the recovery parser for 'compile'  # noqa: F401
            jobs = JobManager(Path(tmp))
            deadline = time.monotonic() + 30
            while jobs.get(job_id).state == 'running' and time.monotonic() < deadline:
                time.sleep(0.3)
            job = jobs.get(job_id)
            self.assertEqual(job.state, 'succeeded', job)
            self.assertTrue(job.result.get('recovered'))
            self.assertEqual(job.result['summary']['compiled'], 3)
            self.assertTrue(job.result['ok'])
            saved = json.loads((Path(tmp) / f'{job_id}.json').read_text(encoding='utf-8'))
            self.assertEqual(saved['state'], 'succeeded')

    def test_foreign_json_logs_and_extra_keys_do_not_break_status(self):
        from cs2map.jobs import JobManager
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / 'script-test-x-1.json').write_text('{"map": "x", "ok": true}', encoding='utf-8')
            (Path(tmp) / 'old-1.json').write_text(json.dumps({
                'id': 'old-1', 'kind': 'cubemaps', 'title': 't', 'argv': [], 'cwd': '.', 'log': 'old-1.log',
                'started': '2026-10-02T00:00:00', 'state': 'succeeded', 'map': 'b_kinds'}), encoding='utf-8')
            jobs = JobManager(Path(tmp))
            recent = jobs.recent()
            self.assertEqual([j['id'] for j in recent], ['old-1'])
            self.assertEqual(jobs.get('old-1').meta['map'], 'b_kinds')
            with self.assertRaises(KeyError):
                jobs.get('script-test-x-1')


class HammerDirty(unittest.TestCase):
    def test_force_never_overwrites_unsaved_hammer_edits(self):
        from cs2map.project import MapError, Project
        p = Project()
        dirty = {'open': True, 'dirty': True, 'titles': ['Hammer - [x.vmap *]']}
        clean = {'open': True, 'dirty': False, 'titles': ['Hammer - [x.vmap]']}
        with mock.patch('cs2map.system.hammer_state', return_value=dirty):
            for force in (False, True):
                with self.assertRaises(MapError):
                    p._check_hammer('x', force)
        with mock.patch('cs2map.system.hammer_state', return_value=clean):
            with self.assertRaises(MapError):
                p._check_hammer('x', False)
            self.assertTrue(p._check_hammer('x', True)['open'])


if __name__ == '__main__':
    unittest.main()
