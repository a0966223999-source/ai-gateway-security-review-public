"""Real SQLite and process identity tests; no live service or credentials."""
from pathlib import Path
import sqlite3
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

from install_management_consent_page import idle, alive, stop


class InstallGuardsTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.database = Path(self.temp.name) / 'fixture.sqlite'
        with sqlite3.connect(self.database) as db:
            db.execute('CREATE TABLE subagent_batch_items(status TEXT)')

    def test_terminal_rows_are_idle_without_mutation(self):
        with sqlite3.connect(self.database) as db:
            db.executemany('INSERT INTO subagent_batch_items VALUES(?)', [('cancelled',), ('succeeded',)])
        before = self.database.read_bytes()
        self.assertEqual(idle(self.database), {'cancelled': 1, 'succeeded': 1})
        self.assertEqual(self.database.read_bytes(), before)

    def test_live_pending_or_unknown_state_prevents_reload(self):
        for state in ('running', 'pending', 'leased', 'queued', 'unknown-new-state'):
            with sqlite3.connect(self.database) as db:
                db.execute('DELETE FROM subagent_batch_items')
                db.execute('INSERT INTO subagent_batch_items VALUES(?)', (state,))
            with self.subTest(state=state), self.assertRaisesRegex(RuntimeError, 'NOT_IDLE_STOP'):
                idle(self.database)

    def test_reused_pid_identity_never_receives_signal(self):
        proc = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(10)'])
        try:
            record = {'pid': proc.pid, 'start': 'incorrect-start-time'}
            self.assertFalse(alive(record))
            with self.assertRaisesRegex(RuntimeError, 'IDENTITY_CHANGED_STOP'):
                stop(record)
            self.assertIsNone(proc.poll())
        finally:
            proc.terminate()
            proc.wait(timeout=5)
