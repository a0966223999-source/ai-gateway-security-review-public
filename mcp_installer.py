"""Install bounded RPC tools on ORIGINAL FastMCP instance; no new service.
One line in existing install: install_rpc_tools(mcp, mailbox, formal, principal,
                                              oauth_meta, host_config=...)
Only native host setup supplies public key and verified runtime budgets.
"""
import asyncio
import hashlib
import json
import math
import os
import re
import time
from contextlib import asynccontextmanager
from urllib.parse import urlsplit
from mcp.types import ToolAnnotations
from request_store import RequestStore, submit_and_wait
from targeted_locator import parse_target_locator
from transport import SameInboxRPC, ReplyVerifier, READ, canonical, UnknownOutcome

MAILBOX='gateway_internal_v1'


def parse_exact(raw):
    if not isinstance(raw,str) or len(raw.encode())>65536:
        raise ValueError('JSON_SIZE_LIMIT')
    def unique(pairs):
        result={}
        for k,v in pairs:
            if k in result:raise ValueError('DUPLICATE_JSON_FIELD')
            result[k]=v
        return result
    result=json.loads(raw,object_pairs_hook=unique,parse_constant=lambda x:(_ for _ in ()).throw(ValueError('NONFINITE_JSON')))
    if canonical(result)!=raw:raise ValueError('EXACT_CANONICAL_JSON_REQUIRED')
    return result


def redis_connection(max_wait):
    """Use same existing approved settings as Inbox, dedicated async connection."""
    from redis.asyncio import Redis
    from redis.asyncio.retry import Retry
    from redis.backoff import NoBackoff
    if os.getenv('VERCEL_ENV')!='preview':raise ValueError('preview_only')
    url=os.getenv('XJ_PREVIEW_REDIS_URL') or os.getenv('REDIS_URL','')
    endpoint=urlsplit(url)
    if endpoint.scheme!='rediss' or not endpoint.hostname or endpoint.query or endpoint.fragment:
        raise ValueError('redis_configuration_unavailable')
    return Redis.from_url(url,decode_responses=True,ssl_cert_reqs='required',ssl_check_hostname=True,
        socket_connect_timeout=3,socket_timeout=max_wait+2,retry=Retry(NoBackoff(),0),max_connections=4)


def install_rpc_tools(mcp, mailbox, formal, principal, oauth_meta, *, host_config,
                      connection_factory=redis_connection):
    """Missing configuration disables ONLY these tools, not original MCP tools.
    host_config must be host code/config, never request or mailbox data.
    Required verified budget numbers must be measured/retrieved by deployment.
    """
    try:
        if not all(callable(x) for x in (mailbox,principal,connection_factory)) or not callable(getattr(formal,'dispatch',None)):
            raise ValueError('HOST_CAPABILITIES_REQUIRED')
        if not isinstance(host_config,dict) or host_config.get('budgets_verified') is not True:
            raise ValueError('VERIFIED_BUDGETS_REQUIRED')
        budgets=[host_config[k] for k in ('max_wait_seconds','function_budget_seconds','connector_budget_seconds')]
        if any(type(n) not in (int,float) or not math.isfinite(n) for n in budgets):
            raise ValueError('BUDGET_VALUES_REQUIRED')
        wait,function,connector=budgets
        if not 0<wait<=600 or not wait<min(function,connector)-5:
            raise ValueError('BUDGET_MARGIN_REQUIRED')
        # Key comes only from original host setup environment; never caller config.
        public=os.getenv('XJ_SOURCE_RPC_HOST_PUBLIC_KEY','')
        if not re.fullmatch('[0-9a-f]{64}',public):raise ValueError('PINNED_HOST_KEY_REQUIRED')
        verifier=ReplyVerifier(bytes.fromhex(public))
    except (ValueError,KeyError,TypeError):
        return {'enabled':False,'reason':'HOST_RPC_CONFIG_UNVERIFIED','tools':[]}

    @asynccontextmanager
    async def session(mailbox_id):
        if mailbox_id!=MAILBOX:raise ValueError('FIXED_MAILBOX_REQUIRED')
        owner=principal()
        if not isinstance(owner,str) or not owner:raise ValueError('OWNER_REQUIRED')
        inbox=mailbox(MAILBOX).for_event('gateway.continuation.ready')
        if inbox.owner!=owner or inbox.mailbox_id!=MAILBOX:raise ValueError('OWNER_MAILBOX_MISMATCH')
        client=connection_factory(wait)
        def current():
            try:return principal()==owner and os.getenv('XJ_SOURCE_RPC_HOST_PUBLIC_KEY')==public
            except Exception:return False
        rpc=SameInboxRPC(redis=client,inbox_key=inbox.key,owner=owner,mailbox=MAILBOX,
            verifier=verifier,current=current,max_wait_seconds=wait,
            function_budget_seconds=function,connector_budget_seconds=connector)
        try:yield rpc,inbox
        finally:
            try:await asyncio.wait_for(client.aclose(),timeout=1)
            except BaseException:pass

    async def event_context(rpc,event, *, allow_expired=False):
        if not isinstance(event,str) or not re.fullmatch('[0-9a-f]{64}',event):raise ValueError('EVENT_ID')
        request=await RequestStore(rpc.redis,rpc.key,rpc.current).read(event,allow_expired=allow_expired)
        raw=await rpc.redis.eval(READ,1,rpc.key,event)
        if rpc.current() is not True:raise ValueError('AUTHORITY_REVOKED')
        if not raw:raise UnknownOutcome('EVENT_READBACK_UNKNOWN')
        m=json.loads(raw);q=json.loads(m['message'])
        _,sha=RequestStore.exact(request)
        if q['requestSHA256']!=sha:raise ValueError('REQUEST_READBACK_CHANGED')
        return request,m,rpc.context(m['operation_key'],q['nonce'],sha,q['deadline'],event)

    @mcp.tool(meta=oauth_meta, annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=True))
    async def gateway_source_rpc_request(mailbox_id:str,request_json:str,target_locator_json:str='')->dict:
        """Save exact canonical request in original mailbox and await signed host outcome. UNKNOWN is query-only; never repeat under a new nonce."""
        request=parse_exact(request_json)
        RequestStore.exact(request)
        parse_target_locator(target_locator_json, request['method'])
        async with session(mailbox_id) as (rpc,inbox):
            async def dispatch(event):
                # Original events code uses synchronous Inbox commands on its own
                # original configured client; no alternate mailbox/credentials.
                if rpc.current() is not True:raise ValueError('AUTHORITY_REVOKED')
                return await formal.dispatch(inbox,event)
            try:
                result=await submit_and_wait(rpc,request,deadline=time.time()+wait,dispatch=dispatch,target_locator_json=target_locator_json)
                return {'status':'verified_host_response','result':result}
            except UnknownOutcome:
                return {'status':'UNKNOWN_QUERY_ONLY','request_id':request['id'],
                        'requestSHA256':RequestStore.exact(request)[1]}

    @mcp.tool(meta=oauth_meta, annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False))
    async def gateway_source_rpc_read(mailbox_id:str,event_id:str)->dict:
        """Read one same-owner original RPC event's exact request; this does not grant Source or native Work authority."""
        async with session(mailbox_id) as (rpc,_):
            request,message,context=await event_context(rpc,event_id)
            result={'request':request,'context':context,'authority':False}
            if 'rpc_target_locator' in message:
                result['targetLocator']=parse_target_locator(message['rpc_target_locator'],request['method'])
            return result

    @mcp.tool(meta=oauth_meta, annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False))
    async def gateway_source_rpc_respond(mailbox_id:str,event_id:str,envelope_json:str)->dict:
        """Commit a reply signed by the fixed original host only. OAuth owner alone cannot authorize a response."""
        envelope=parse_exact(envelope_json)
        async with session(mailbox_id) as (rpc,_):
            _,_,context=await event_context(rpc,event_id)
            try:
                result=await rpc.commit(envelope=envelope,context=context)
                return {'status':result,'event_id':event_id}
            except UnknownOutcome:
                return {'status':'UNKNOWN_QUERY_ONLY','event_id':event_id}

    @mcp.tool(meta=oauth_meta, annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False))
    async def gateway_source_rpc_query(mailbox_id:str,event_id:str='',request_id:str='')->dict:
        """Read existing signed outcome only, including historical expired request. Never dispatch, extend, claim, ACK, or resend."""
        async with session(mailbox_id) as (rpc,_):
            if bool(event_id)==bool(request_id):raise ValueError('EXACT_ONE_QUERY_LOCATOR')
            if request_id:
                if not re.fullmatch('[0-9a-f]{64}',request_id):raise ValueError('REQUEST_ID')
                operation=hashlib.sha256((rpc.key+'|rpc|'+request_id).encode()).hexdigest()
                raw=await rpc.redis.get(rpc.key)
                if rpc.current() is not True:raise ValueError('AUTHORITY_REVOKED')
                matches=[m for m in json.loads(raw or '{}').get('messages',{}).values()
                         if m.get('operation_key')==operation and m.get('rpc_nonce')==request_id]
                if len(matches)!=1:return {'status':'UNKNOWN_QUERY_ONLY','request_id':request_id}
                event_id=matches[0]['event_id']
            _,m,context=await event_context(rpc,event_id,allow_expired=True)
            if not m.get('rpc_response'):return {'status':'UNKNOWN_QUERY_ONLY','event_id':event_id}
            result=rpc.verifier.verify(json.loads(m['rpc_response']),context)
            return {'status':'verified_host_response','event_id':event_id,'result':result}

    return {'enabled':True,'tools':['gateway_source_rpc_request','gateway_source_rpc_read',
                                    'gateway_source_rpc_respond','gateway_source_rpc_query']}
