"""Atomic original-mailbox request reservation. No new Redis key or store."""
import hashlib
import json
import secrets
import time
from pathlib import Path
from transport import canonical, READ, UnknownOutcome

SCRIPT=Path(__file__).with_name('inbox_rpc.lua').read_text()


class RequestStore:
    def __init__(self, redis, inbox_key, current):
        if not callable(current):raise ValueError('HOST_CURRENT_REQUIRED')
        self.redis,self.key,self.current=redis,inbox_key,current

    def check(self):
        if self.current() is not True:raise ValueError('AUTHORITY_REVOKED')

    @staticmethod
    def exact(request):
        if (not isinstance(request,dict) or set(request)!={'v','id','source','method','params'}
                or type(request['v']) is not int or request['v']!=1
                or request['method'] not in ('register','read','save')
                or not isinstance(request['params'],dict)
                or not isinstance(request['source'],str) or not request['source']):
            raise ValueError('REQUEST_SCHEMA')
        nonce=request['id']
        if not isinstance(nonce,str) or len(nonce)!=64 or any(c not in '0123456789abcdef' for c in nonce):
            raise ValueError('NONCE_REQUIRED')
        raw=canonical(request)
        if len(raw.encode())>65536:raise ValueError('REQUEST_SIZE')
        return raw,hashlib.sha256(raw.encode()).hexdigest()

    async def submit(self, request, locator, *, dispatch):
        self.check()
        raw,sha=self.exact(request)
        if (locator != dict(kind='source-finalize-rpc-v1',nonce=request['id'],requestSHA256=sha,deadline=locator.get('deadline'))
                or not 0<locator['deadline']-time.time()<=600 or not callable(dispatch)):
            raise ValueError('LOCATOR_SCOPE')
        # Same request ID always uses same operation, even if body changed.
        op=hashlib.sha256((self.key+'|rpc|'+request['id']).encode()).hexdigest()
        message=canonical(locator)
        digest=hashlib.sha256(canonical(['gateway.continuation.ready',op,message]).encode()).hexdigest()
        args=dict(event_name='gateway.continuation.ready',operation_key=op,event_id=secrets.token_hex(32),
                  message=message,digest=digest,epoch=secrets.token_hex(16),rpc_request=raw,
                  rpc_sha256=sha,rpc_deadline=locator['deadline'],rpc_deadline_string=str(locator['deadline']),rpc_nonce=request['id'])
        try:
            response=json.loads(await self.redis.eval(SCRIPT,1,self.key,'rpc_submit',canonical(args)))
        except Exception as exc:
            raise UnknownOutcome('SUBMIT_UNKNOWN_QUERY_ORIGINAL_OPERATION') from exc
        if 'error' in response:raise ValueError(response['error'])
        self.check()
        # A duplicate cannot retry notification, Source writes or finalizer.
        if not response['duplicate']:
            await dispatch(response['event_id'])
            self.check()
        return dict(response,operation_key=op)

    async def read(self,event, *, allow_expired=False):
        self.check()
        raw=await self.redis.eval(READ,1,self.key,event)
        self.check()
        if not raw:raise ValueError('REQUEST_EVENT_MISSING')
        m=json.loads(raw)
        if m.get('event_id')!=event or m.get('event_name')!='gateway.continuation.ready':
            raise ValueError('REQUEST_EVENT_SCOPE')
        request=json.loads(m['rpc_request'])
        exact,sha=self.exact(request)
        q=json.loads(m['message'])
        if (m['rpc_request']!=exact or m['rpc_request_sha256']!=sha or q['requestSHA256']!=sha
                or m['rpc_nonce']!=request['id'] or q['nonce']!=request['id']
                or float(m['rpc_deadline'])!=q['deadline'] or (not allow_expired and time.time()>=q['deadline'])
                or m.get('rpc_state')!='reserved_query_only'):
            raise ValueError('REQUEST_BYTES_OR_SCOPE_MISMATCH')
        return request


async def submit_and_wait(transport, request, *, deadline, dispatch):
    """Complete same-event request storage + notification + bounded response wait.
    dispatch is the existing formal.dispatch callback, not a separate service.
    """
    store=RequestStore(transport.redis,transport.key,transport.current)
    _,sha=store.exact(request)
    operation=hashlib.sha256((transport.key+'|rpc|'+request['id']).encode()).hexdigest()
    async def submit(locator):
        return await store.submit(request,locator,dispatch=dispatch)
    return await transport.wait(operation=operation,nonce=request['id'],request_sha=sha,
                                deadline=deadline,send_once=submit)
