"""Minimal patches preserve existing Gateway hooks and refuse changed anchors."""
import ast
import json
import sqlite3
import tempfile
from pathlib import Path
import unittest
from install_fixed_runtime import gateway_hooks, maintenance_snapshot


class RuntimeInstallGuardsTests(unittest.TestCase):
    def setUp(self):
        self.app='''def configure():
    install_gateway_management_consent(app, enabled=management_environment.get("XJ_AGENT_MANAGEMENT_CONSENT_ENABLED") == "true")
    install_checkpoint_host_route(app)
    preserve_short_memory()
'''
        self.auth='''PUBLIC = {
        "/api/v1/auth/agent-management-consent",  # Static review page only.
}
'''

    def test_only_runtime_hook_and_static_review_page_added(self):
        app,auth=gateway_hooks(self.app,self.auth)
        ast.parse(app);ast.parse(auth)
        self.assertIn(b'preserve_short_memory()',app)
        self.assertIn(b'install_checkpoint_host_route(app)',app)
        self.assertIn(b'XJ_FIXED_AGENT_RUNTIME_CONSENT_ENABLED',app)
        self.assertNotIn(b'/agent-runtime-consents',auth)
        self.assertIn(b'/api/v1/auth/agent-runtime-consent',auth)

    def test_duplicate_or_changed_anchor_is_refused(self):
        for app,auth in ((self.app+self.app,self.auth),(self.app,self.auth+self.auth),
                         (self.app.replace('XJ_AGENT_MANAGEMENT','OTHER'),self.auth)):
            with self.assertRaisesRegex(RuntimeError,'HOOK_CHANGED'):
                gateway_hooks(app,auth)

    def test_second_install_never_duplicates_hook(self):
        app,auth=gateway_hooks(self.app,self.auth)
        with self.assertRaisesRegex(RuntimeError,'HOOK_CHANGED'):
            gateway_hooks(app.decode(),auth.decode())


class PendingMaintenanceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.db=Path(self.temp.name)/'original.sqlite'
        with sqlite3.connect(self.db) as db:
            db.execute('CREATE TABLE subagent_batches(id TEXT,execution_spec TEXT)')
            db.execute('CREATE TABLE subagent_batch_items(id TEXT,batch_id TEXT,status TEXT,attempt INTEGER,lease_owner TEXT,lease_expires_at TEXT,started_at TEXT,cancel_requested_at TEXT,prompt TEXT)')
            db.execute('INSERT INTO subagent_batches VALUES (?,?)',('batch',json.dumps({'native_control':{'items':{}}})))
            db.execute('INSERT INTO subagent_batch_items VALUES (?,?,?,?,?,?,?,?,?)',('item','batch','pending',0,None,None,None,None,'preserve exact task'))

    def test_known_untouched_pending_snapshot_is_readonly(self):
        before=self.db.read_bytes()
        a=maintenance_snapshot(self.db,preserved_items={'item':'batch'})
        b=maintenance_snapshot(self.db,preserved_items={'item':'batch'})
        self.assertEqual(a,b);self.assertEqual(a['counts'],{'pending':1})
        self.assertEqual(self.db.read_bytes(),before)

    def test_unrecognized_pending_cannot_be_adopted(self):
        with self.assertRaisesRegex(RuntimeError,'UNRECOGNIZED'):
            maintenance_snapshot(self.db,preserved_items={})

    def test_running_leased_or_attempted_item_blocks(self):
        for field,value in [('status','running'),('lease_owner','owner'),('attempt',1),('started_at','now')]:
            with sqlite3.connect(self.db) as db:db.execute(f'UPDATE subagent_batch_items SET {field}=?',(value,))
            with self.assertRaisesRegex(RuntimeError,'ACTIVE_OR_UNRECOGNIZED'):
                maintenance_snapshot(self.db,preserved_items={'item':'batch'})
            with sqlite3.connect(self.db) as db:db.execute('UPDATE subagent_batch_items SET status="pending",attempt=0,lease_owner=NULL,started_at=NULL')

    def test_native_fence_blocks_even_if_row_claims_pending(self):
        with sqlite3.connect(self.db) as db:
            db.execute('UPDATE subagent_batches SET execution_spec=?',(json.dumps({'native_control':{'items':{'item':{'phase':'attempted'}}}}),))
        with self.assertRaisesRegex(RuntimeError,'NATIVE_FENCE'):
            maintenance_snapshot(self.db,preserved_items={'item':'batch'})

    def test_task_or_fence_changes_change_pinned_digest(self):
        before=maintenance_snapshot(self.db,preserved_items={'item':'batch'})
        with sqlite3.connect(self.db) as db:db.execute('UPDATE subagent_batch_items SET prompt="concurrent replacement"')
        after=maintenance_snapshot(self.db,preserved_items={'item':'batch'})
        self.assertNotEqual(before['sha256'],after['sha256'])
