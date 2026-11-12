"""Actual mailbox Lua, offline fakeredis only."""
import json,unittest
import test_request_store
from targeted_locator import parse_target_locator
from transport import canonical,UnknownOutcome

class RouteStoreTests(test_request_store.Tests):
    def target(self,route):return dict(v=3,kind='ordinary-work',threadId='target',route=route,**({'environmentId':'registered-env'}if route=='native-chat'else{}))
    async def submit_target(self,target):
        self.request['method']='register';self.locator['requestSHA256']=self.store.exact(self.request)[1]
        return await self.store.submit(self.request,self.locator,dispatch=self.dispatch,target_locator_json=canonical(target))
    async def test_cloud_lua_roundtrip_and_all_route_mutations_rejected(self):
        loc=self.target('cloud-task');receipt=await self.submit_target(loc);before=await self.redis.get(self.key);row=json.loads(before)['messages'][receipt['event_id']]
        self.assertEqual(row['rpc_target_locator'],canonical(loc));self.assertEqual(row['rpc_request'],canonical(self.request));self.assertEqual(float(row['rpc_deadline']),self.locator['deadline'])
        for changed in (self.target('native-chat'),dict(v=2,kind='ordinary-work',threadId='target',environmentId='registered-env'),dict(loc,threadId='other')):
            with self.assertRaisesRegex(ValueError,'rpc_operation_conflict'):await self.submit_target(changed)
            self.assertEqual(before,await self.redis.get(self.key))
        self.assertEqual(len(self.dispatches),1)
    async def test_native_v3_exact_environment_lua(self):
        loc=self.target('native-chat');receipt=await self.submit_target(loc);row=json.loads(await self.redis.get(self.key))['messages'][receipt['event_id']];self.assertEqual(row['rpc_target_locator'],canonical(loc))
    async def test_unknown_cloud_submission_not_replayed(self):
        old=self.redis.eval
        async def unknown(*args):await old(*args);raise ConnectionError('offline lost return')
        self.redis.eval=unknown
        with self.assertRaises(UnknownOutcome):await self.submit_target(self.target('cloud-task'))
        self.redis.eval=old;before=await self.redis.get(self.key)
        with self.assertRaisesRegex(ValueError,'rpc_operation_conflict'):await self.submit_target(self.target('native-chat'))
        self.assertEqual(before,await self.redis.get(self.key));self.assertEqual(self.dispatches,[])
    async def test_ambiguous_routes_stop_before_redis(self):
        for loc in (dict(self.target('cloud-task'),environmentId=None),dict(self.target('cloud-task'),environmentId='runtime-id'),dict(self.target('cloud-task'),route='auto'),{k:v for k,v in self.target('native-chat').items()if k!='environmentId'}):
            with self.assertRaises(ValueError):await self.submit_target(loc)
            self.assertIsNone(await self.redis.get(self.key))
