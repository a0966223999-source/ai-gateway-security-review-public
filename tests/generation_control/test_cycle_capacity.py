import json
import unittest
from support import Fixture


class CycleCapacityTests(unittest.IsolatedAsyncioTestCase):
    def fill(self,f,*,expired=True,original_valid=False,missing_expiry=False):
        doc=json.loads(f.db.get(f.inbox.key));doc['continuation_cycles']={}
        for index in range(100):
            eid=f'{index:064x}'
            old={'state':'safety_stopped','reason':'UNKNOWN_QUERY_ONLY',
                 'scope_expires_at':f.time-1,'original_event_expires_at':f.time-1 if expired else f.time+1000}
            if missing_expiry:old.pop('original_event_expires_at')
            doc['continuation_cycles'][eid]=old
            if original_valid:doc['messages'][eid]={'expires_at':f.time+1000}
        f.db.set(f.inbox.key,json.dumps(doc),keepttl=True)

    async def test_naturally_expired_missing_original_records_pruned_on_begin(self):
        f=Fixture(self);self.fill(f);before=f.db.ttl(f.inbox.key)
        self.assertEqual((await f.begin())['status'],'active')
        doc=json.loads(f.db.get(f.inbox.key))
        self.assertEqual(set(doc['continuation_cycles']),{f.event})
        self.assertEqual(f.db.ttl(f.inbox.key),before)

    async def test_valid_unknown_records_not_pruned_by_expired_control_scope(self):
        for change in ({'expired':False},{'original_valid':True},{'missing_expiry':True}):
            f=Fixture(self);self.fill(f,**change);before=f.db.get(f.inbox.key)
            with self.subTest(change=change),self.assertRaisesRegex(ValueError,'CYCLE_LIMIT'):
                await f.begin()
            self.assertEqual(f.db.get(f.inbox.key),before)

    async def test_query_is_read_only_and_does_not_prune(self):
        f=Fixture(self);self.fill(f);before=f.db.get(f.inbox.key)
        self.assertEqual(f.store.transition('query',f.event)['status'],'CYCLE_MISSING')
        self.assertEqual(f.db.get(f.inbox.key),before)

    async def test_expired_original_message_and_control_can_be_pruned_together(self):
        f=Fixture(self);self.fill(f);doc=json.loads(f.db.get(f.inbox.key))
        for eid in doc['continuation_cycles']:doc['messages'][eid]={'expires_at':f.time-1}
        original_messages=doc['messages'].copy();f.db.set(f.inbox.key,json.dumps(doc),keepttl=True)
        self.assertEqual((await f.begin())['status'],'active')
        doc=json.loads(f.db.get(f.inbox.key))
        self.assertEqual(set(doc['continuation_cycles']),{f.event})
        self.assertEqual(doc['messages'],original_messages)
