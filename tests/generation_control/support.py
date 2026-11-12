"""Only synthetic OAuth/native evidence and generated temporary signing keys."""
import base64
import copy
import json
import secrets
from pathlib import Path
import sys
from unittest.mock import patch
import types

CANDIDATE_ROOT = Path(__file__).resolve().parents[2]
LOCAL_BASE = CANDIDATE_ROOT.parent/'shared-rpc-public-release-20261110'/'base'
BASE = LOCAL_BASE if LOCAL_BASE.exists() else CANDIDATE_ROOT
sys.path[:0] = [str(CANDIDATE_ROOT), str(BASE)]

import fakeredis
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from inbox import Inbox, canonical as inbox_canonical
from mcp_events import Events, SecretBox
from protocol import CONTROL_DOMAIN, FORMAL, MAILBOX, HostProofVerifier, canonical, digest, notification_id
from cycle_store import CycleStore
from cycle_transport import CycleController


class Fixture:
    def __init__(self, test, *, report_adapter=None, owner='OFFLINE_OWNER', time_epoch=None):
        self.time = 2_000_000_000 if time_epoch is None else time_epoch
        self.patch = patch('time.time', lambda: self.time)
        self.patch.start(); test.addCleanup(self.patch.stop)
        self.db = fakeredis.FakeRedis(decode_responses=True)
        self.inbox = Inbox(owner, MAILBOX, self.db).for_event(FORMAL)
        self.owner = self.inbox.owner
        self.sid = 'sub_'+secrets.token_hex(32)
        self.box = SecretBox(b'x'*32)
        self.token = types.SimpleNamespace(token='OFFLINE_ONLY', expires_at=self.time+7200)
        self.sent = []
        self.sender_status = 200
        self.sender_unknown = False
        async def verify(_token): return self.token
        async def send(url, headers, body, timeout):
            self.sent.append({'url':url, 'headers':headers, 'body':json.loads(body), 'timeout':timeout})
            if self.sender_unknown: raise TimeoutError('OFFLINE_LOST_CALLBACK')
            return self.sender_status,b'{}'
        self.formal = Events(lambda _:self.inbox, types.SimpleNamespace(verify_token=verify),
            sender=send, box=self.box, event_name=FORMAL, authorize_event=lambda *args:True)
        self.inbox.run('subscribe', expected_revision='', subscription={
            'id':self.sid,'event_name':FORMAL,'url':'https://offline.invalid/callback',
            'expires_at':self.time+7200,'access_until':self.time+7200,'revision':'offline-v1',
            'protected':self.box.seal({'secret':'whsec_'+base64.b64encode(b'x'*32).decode(),
                                     'access_token':'OFFLINE_ONLY'},self.sid)})
        payload={'work':{'binding':{'operationKey':'source-operation'},'nextAction':'next'},
                 'target':'OFFLINE_OWNER','source':{'locator':'OFFLINE_SOURCE','revision':'before'}}
        self.event = self.inbox.send('mailbox-operation', inbox_canonical(payload),event_name=FORMAL)['event_id']
        doc=json.loads(self.db.get(self.inbox.key)); doc['deliveries'][self.event+':'+self.sid]['state']='delivered'
        self.db.set(self.inbox.key,json.dumps(doc),keepttl=True)
        self.original=copy.deepcopy(doc)
        self.binding={'logical_event_id':self.event,'mailbox_operation_key':'mailbox-operation',
            'mailbox_digest':doc['messages'][self.event]['digest'],'source_operation_key':'source-operation',
            'source_key':'OFFLINE_SOURCE_KEY','source_ref':'OFFLINE_SOURCE','producer_thread_id':'producer-thread',
            'producer_turn_id':'producer-turn','checkpoint_sha256':digest({'step':'checkpoint'}),
            'payload_sha256':digest(payload),'base_sha256':'b'*64,'mapping_sha256':'c'*64,
            'next_step_sha256':digest('next')}
        self.policy={'wait_seconds':80,'round_budget_seconds':120,'max_generations':10,'min_interval_seconds':80}
        self.private=Ed25519PrivateKey.generate()
        public=self.private.public_key().public_bytes(Encoding.Raw,PublicFormat.Raw)
        self.verifier=HostProofVerifier(public)
        self.controller=CycleController(formal=self.formal,proof_verifier=self.verifier,
            policy=self.policy,subscription_id=self.sid,completed_report_adapter=report_adapter,clock=lambda:self.time)
        self.store=CycleStore(self.inbox)

    def advance(self, seconds=80): self.time+=seconds

    def evidence(self, receiver='receiver-0', **overrides):
        value={'binding':copy.deepcopy(self.binding),'authority_current':True,'source_current':True,
            'zero_effects':True,'stop':False,'once_state':'fresh','receiver_run_id':receiver,
            'observed_at':self.time,'scope_expires_at':2_000_003_600,
            'native_status':'running','native_thread_id':'producer-thread','native_turn_id':'producer-turn',
            'source_revision':'source-revision',
            'watch':{'mode':'producer','thread_id':'producer-thread','turn_id':'producer-turn',
                     'platform_receipt_sha256':None}}
        value.update(overrides); return value

    def proof(self, action, generation=0, receiver='receiver-0', **extra):
        evidence=self.evidence(receiver, **extra)
        if action=='begin':
            evidence.update(policy=self.policy, trusted_delivery_verified=True, subscription_id=self.sid,
                notification_id=self.event if generation==0 else notification_id(self.owner,MAILBOX,self.event,generation))
        body={'domain':CONTROL_DOMAIN,'action':action,'owner':self.owner,'mailbox':MAILBOX,
            'event_id':self.event,'generation':generation,'issued_at':self.time,
            'expires_at':self.time+20,'evidence':evidence}
        signature=base64.b64encode(self.private.sign(canonical(body).encode())).decode()
        return canonical({'body':body,'signature':signature})

    async def begin(self,generation=0):
        return await self.controller.begin(self.inbox,self.proof('begin',generation,f'receiver-{generation}'))

    async def observe(self,generation=0,**extra):
        return await self.controller.observe(self.inbox,self.proof('observe',generation,f'receiver-{generation}',**extra))

    async def publish(self,generation=1):
        return await self.controller.publish(self.inbox,self.proof('publish',generation,f'receiver-{generation-1}'))


def accepted_report(decision):
    return {'decision':decision, 'original_mailbox_message_read':True,'sender_identity_verified':True,
        'source_identity_verified':True,'source_revision_current':True,'checkpoint_verified':True,
        'result_contract_verified':True,'businessAcceptanceVerified':False,'parentCloseAllowed':False}
