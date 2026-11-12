"""Real OS signal/cleanup and supervisor processes, isolated host enrollment."""
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
from tempfile import TemporaryDirectory
import time
import unittest

from fresh_batch_host import initialize


class WorkerSignalTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.code = Path(__file__).resolve().parents[1]

    def signal_worker(self, signum):
        script = self.root / 'signal_worker.py'
        ready, cleanup = self.root / 'ready', self.root / 'cleanup'
        script.write_text('''import asyncio,sys
from pathlib import Path
from worker_signal_lifecycle import run_until_signalled
async def serve(args):
    Path(args[0]).touch()
    try:
        await asyncio.Event().wait()
    finally:
        await asyncio.sleep(.02)
        Path(args[1]).touch()
raise SystemExit(asyncio.run(run_until_signalled(serve,sys.argv[1:])))
''')
        env = dict(os.environ, PYTHONPATH=str(self.code))
        proc = subprocess.Popen([sys.executable, str(script), str(ready), str(cleanup)],
            env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            deadline = time.monotonic() + 5
            while not ready.exists() and proc.poll() is None and time.monotonic() < deadline:
                time.sleep(.01)
            self.assertTrue(ready.exists())
            os.kill(proc.pid, signum)
            stdout, stderr = proc.communicate(timeout=5)
            self.assertEqual(proc.returncode, 128 + signum, stderr.decode())
            self.assertTrue(cleanup.exists())
            self.assertNotIn(b'CancelledError', stderr)
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.communicate()

    def test_actual_sigterm_reports_maintenance_only_after_cleanup(self):
        self.signal_worker(signal.SIGTERM)

    def test_actual_sigint_reports_stop_only_after_cleanup(self):
        self.signal_worker(signal.SIGINT)

    def test_unexpected_cancellation_does_not_report_maintenance(self):
        code = '''import asyncio
from worker_signal_lifecycle import run_until_signalled
async def serve(args): raise asyncio.CancelledError()
raise SystemExit(asyncio.run(run_until_signalled(serve,None)))
'''
        result = subprocess.run([sys.executable, '-c', code],
            env=dict(os.environ, PYTHONPATH=str(self.code)), capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 1)
        self.assertIn(b'CancelledError', result.stderr)

    def manager(self, exits):
        source = self.root / 'source'
        source.mkdir()
        for filename in ('platform_service_manager.py', 'fresh_batch_host.py'):
            shutil.copyfile(self.code / 'integration' / filename, source / filename)
        count = self.root / 'count'
        (source / 'fresh_batch_worker.py').write_text(
            'from pathlib import Path\np=Path('+repr(str(count))+')\n'
            'n=int(p.read_text()) if p.exists() else 0\np.write_text(str(n+1))\n'
            'codes='+repr(exits)+'\nraise SystemExit(codes[n] if n<len(codes) else 0)\n')
        host = self.root / 'host'
        initialize(host, owner='oauth:' + 'a' * 64, platform_service=True)
        result = subprocess.run([sys.executable, str(source / 'platform_service_manager.py'),
            '--root', str(host), '--gateway-backend', str(self.root),
            '--database', str(self.root / 'not-used-by-fixture.sqlite'),
            '--endpoint', 'https://isolated.vercel.app/internal/deer-batch/rpc'],
            capture_output=True, timeout=40)
        return result, int(count.read_text())

    def test_maintenance_reload_does_not_exhaust_three_failure_budget(self):
        result, attempts = self.manager([143, 143, 143, 143, 0])
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(attempts, 5)

    def test_maintenance_does_not_reset_unexpected_failure_budget(self):
        result, attempts = self.manager([1, 143, 1, 143, 1, 143, 1, 0])
        self.assertEqual(result.returncode, 1)
        self.assertEqual(attempts, 7)
        self.assertIn(b'WORKER_RESTART_LIMIT_QUERY_ONLY', result.stderr)
