"""Batch-only request/reply on the existing Redis and mailbox namespace.

No existing Source RPC, subscription, old request or legacy signing key is
changed. A single new host generation handles only its explicitly enrolled
parent. Redis is transport; original Gateway SQL remains the batch store.
"""
import asyncio
import base64
import hashlib
import hmac
import json
import os
import time

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations
from typing import Literal

from batch_mcp_entry import Submit, Status, RequestBase, planning_response
from platform_pending_cancel import PendingCancel as Cancel
from fresh_batch_host import canonical, digest, enrollment_active, request_deadline
from platform_codex_batch import ExecuteBatch
from platform_codex_executor import Execute
from platform_codex_stop import Stop


def install_fresh_batch_routes(app, *, env=os.environ, connection_factory=None):
    """The fresh host's signed control port on the EXISTING API service.

    Redis credentials stay in the original Vercel environment. A host cannot
    supply a Redis key, an owner, another generation or an arbitrary operation.
    """
    if env.get('XJ_FRESH_BATCH_HOST_ENABLED') != 'true':
        return
    from fastapi import Request
    from starlette.responses import JSONResponse
    if connection_factory is None:
        from service_extension import original_redis_connection
        connection_factory = original_redis_connection
    config = json.loads(env['XJ_FRESH_BATCH_HOST_CONFIG'])
    rpc = BatchRPC(config=config, ingress_key=bytes.fromhex(env['XJ_FRESH_BATCH_INGRESS_KEY']),
                   connection_factory=connection_factory)

    @app.post('/internal/deer-batch/rpc')
    async def fresh_control(request: Request):
        raw = await request.body()
        if len(raw) > 131072:
            return JSONResponse({'error': 'BATCH_CONTROL_SIZE'}, status_code=413)
        try:
            envelope = json.loads(raw)
            if set(envelope) != {'body', 'signature'}:
                raise ValueError('CONTROL_ENVELOPE')
            body = envelope['body']
            if set(body) != {'domain', 'generation', 'action', 'nonce', 'expires_at', 'payload'}:
                raise ValueError('CONTROL_SCHEMA')
            rpc.verifier.verify(base64.b64decode(envelope['signature'], validate=True), canonical(body))
            now = time.time()
            rpc.check(config['owner'], config['parent_id'])
            if (body['domain'] != 'fresh-deer-batch-control-v1'
                    or body['generation'] != config['generation']
                    or body['action'] not in ('poll', 'respond', 'heartbeat')
                    or not isinstance(body['nonce'], str) or len(body['nonce']) != 32
                    or any(c not in '0123456789abcdef' for c in body['nonce'])
                    or type(body['expires_at']) not in (int, float)
                    or not now < body['expires_at'] <= now + 60
                    or not isinstance(body['payload'], dict)):
                raise ValueError('CONTROL_SCOPE')
        except Exception:
            return JSONResponse({'error': 'BATCH_CONTROL_DENIED'}, status_code=403)

        def perform():
            with rpc.connection_factory() as redis:
                if not redis.set(rpc.prefix + ':control:' + body['nonce'], 'attempted', nx=True, ex=120):
                    raise ValueError('CONTROL_ALREADY_ATTEMPTED')
                payload = body['payload']
                if body['action'] == 'poll':
                    if payload:
                        raise ValueError('CONTROL_POLL_SCOPE')
                    key = redis.lpop(rpc.prefix + ':queue')
                    if key is None:
                        return {'request': None}
                    key = key.decode() if isinstance(key, bytes) else key
                    if not key.startswith(rpc.prefix + ':request:') or len(key) != len(rpc.prefix + ':request:') + 64:
                        raise ValueError('CONTROL_QUEUE_SCOPE')
                    record = redis.get(key)
                    return {'request': json.loads(record)['envelope'] if record else None}
                if body['action'] == 'heartbeat':
                    if set(payload) != {'body', 'signature'}:
                        raise ValueError('HEARTBEAT_SCHEMA')
                    heartbeat = payload['body']
                    rpc.verifier.verify(base64.b64decode(payload['signature'], validate=True), canonical(heartbeat))
                    if (heartbeat.get('domain') != 'fresh-deer-batch-heartbeat-v1'
                            or heartbeat.get('generation') != config['generation']
                            or heartbeat.get('parent_id') != config['parent_id']
                            or not 0 <= time.time() - heartbeat.get('observed_at', 0) <= 30):
                        raise ValueError('HEARTBEAT_SCOPE')
                    redis.set(rpc.prefix + ':heartbeat', canonical(payload), ex=30)
                    return {'heartbeat_saved': True}
                if set(payload) != {'request_id', 'response'}:
                    raise ValueError('CONTROL_RESPONSE_SCHEMA')
                rid = payload['request_id']
                if not isinstance(rid, str) or len(rid) != 64 or any(c not in '0123456789abcdef' for c in rid):
                    raise ValueError('CONTROL_RESPONSE_ID')
                key = rpc.prefix + ':request:' + rid
                stored = redis.get(key)
                original = json.loads(stored)['envelope']['body']
                rpc.verified_result(canonical(payload['response']), original)
                redis.eval(RESPOND, 2, key, key + ':response', stored,
                           canonical(payload['response']).decode(), rid)
                return {'response_saved': True}
        try:
            return await asyncio.to_thread(perform)
        except Exception:
            return JSONResponse({'error': 'BATCH_CONTROL_OUTCOME_UNKNOWN_QUERY_ONLY'}, status_code=409)

ENQUEUE = """
local old=redis.call('GET',KEYS[1])
if old then
 local d=cjson.decode(old)
 if d.payload_digest~=ARGV[1] then return redis.error_reply('BATCH_OPERATION_CONFLICT') end
 return old
end
if tonumber(redis.call('TIME')[1])>=tonumber(ARGV[3]) then return redis.error_reply('BATCH_EXPIRED') end
if redis.call('LLEN',KEYS[2])>=1000 then return redis.error_reply('BATCH_QUEUE_FULL') end
redis.call('SET',KEYS[1],ARGV[2]); redis.call('RPUSH',KEYS[2],KEYS[1]); return ARGV[2]
"""
RESPOND = """
if redis.call('GET',KEYS[1])~=ARGV[1] then return redis.error_reply('BATCH_REQUEST_CHANGED') end
local old=redis.call('GET',KEYS[2])
if old and old~=ARGV[2] then return redis.error_reply('BATCH_RESPONSE_CONFLICT') end
if not old then redis.call('SET',KEYS[2],ARGV[2]) end
redis.call('PUBLISH',KEYS[2],ARGV[3]); return 'saved'
"""


def prefix(config):
    generation = config['generation']
    if len(generation) != 32 or any(c not in '0123456789abcdef' for c in generation):
        raise ValueError('FRESH_GENERATION_INVALID')
    mailbox_hash = hashlib.sha256((config['owner'] + '|gateway_internal_v1').encode()).hexdigest()
    return 'xj:synthetic-inbox:v1:{' + mailbox_hash + '}:deer-batch:' + generation


def request_id(config, action, operation_key):
    return digest([config['generation'], config['owner'], action, operation_key])


class BatchRPC:
    def __init__(self, *, config, ingress_key, connection_factory):
        if (config.get('schema') != 'fresh-deer-batch-host-v1'
                or config.get('automatic_dispatch') is not False
                or len(ingress_key) != 32 or not callable(connection_factory)):
            raise ValueError('FRESH_HOST_CONFIGURATION_REQUIRED')
        self.config, self.key, self.connection_factory = config, ingress_key, connection_factory
        self.prefix = prefix(config)
        self.verifier = Ed25519PublicKey.from_public_bytes(bytes.fromhex(config['public_key']))

    def check(self, owner, parent):
        if (owner != self.config['owner'] or parent != self.config['parent_id']
                or not enrollment_active(self.config, time.time())):
            raise ToolError('FRESH_BATCH_OWNER_PARENT_OR_EXPIRY_DENIED')

    def verified_result(self, raw, body):
        envelope = json.loads(raw)
        if set(envelope) != {'body', 'signature'}:
            raise ValueError('BATCH_RESPONSE_SCHEMA')
        value = envelope['body']
        self.verifier.verify(base64.b64decode(envelope['signature'], validate=True), canonical(value))
        if (value.get('generation') != self.config['generation']
                or value.get('request_id') != body['request_id']
                or value.get('request_sha256') != digest(body)
                or value.get('automatic_dispatch') is not False
                or value.get('native_execution_verified') is not False):
            raise ValueError('BATCH_RESPONSE_SCOPE')
        return value

    def query(self, *, owner, parent, operation_key, action='submit'):
        self.check(owner, parent)
        rid = request_id(self.config, action, operation_key)
        key = self.prefix + ':request:' + rid
        with self.connection_factory() as redis:
            record = redis.get(key)
            if record is None:
                return {'request_id': rid, 'status': 'NOT_FOUND_QUERY_ONLY', 'automatic_dispatch': False}
            body = json.loads(record)['envelope']['body']
            raw = redis.get(key + ':response')
            return self.verified_result(raw, body) if raw else {
                'request_id': rid, 'status': 'PENDING_OR_UNKNOWN_QUERY_SAME_KEY', 'automatic_dispatch': False}

    def call(self, *, owner, parent, action, operation_key, payload):
        self.check(owner, parent)
        if action not in self.config['actions'] or action not in ('submit', 'read', 'cancel', 'execute'):
            raise ValueError('BATCH_ACTION_INVALID')
        body = dict(generation=self.config['generation'], owner=owner, parent_id=parent,
                    action=action, operation_key=operation_key, payload=payload,
                    request_id=request_id(self.config, action, operation_key),
                    expires_at=request_deadline(self.config, time.time(), 60))
        mac = hmac.new(self.key, canonical(body), 'sha256').hexdigest()
        record = dict(payload_digest=digest([owner, parent, action, operation_key, payload]),
                      envelope={'body': body, 'mac': mac})
        raw_record = canonical(record).decode()
        if len(raw_record.encode()) > 65536:
            raise ToolError('BATCH_REQUEST_TOO_LARGE')
        key = self.prefix + ':request:' + body['request_id']
        # Subscribe before enqueue. Unknown writes are never retried here.
        with self.connection_factory() as redis:
            with redis.pubsub() as subscription:
                subscription.subscribe(key + ':response')
                acknowledgement = subscription.get_message(timeout=2)
                if not acknowledgement or acknowledgement.get('type') != 'subscribe':
                    raise ToolError('BATCH_REPLY_SUBSCRIPTION_UNCONFIRMED')
                stored = redis.eval(ENQUEUE, 2, key, self.prefix + ':queue',
                                    record['payload_digest'], raw_record, body['expires_at'])
                original = json.loads(stored)['envelope']['body']
                response = redis.get(key + ':response')
                if response:
                    return self.verified_result(response, original)
                notice = subscription.get_message(ignore_subscribe_messages=True, timeout=3)
                if notice and notice.get('type') == 'message':
                    response = redis.get(key + ':response')
                    if response:
                        return self.verified_result(response, original)
        return {'request_id': body['request_id'], 'status': 'PENDING_OR_UNKNOWN_QUERY_SAME_KEY',
                'automatic_dispatch': False, 'native_execution_verified': False}


class ReceiptQuery(RequestBase):
    operation_kind: Literal['submit', 'cancel', 'execute'] = 'submit'


def install_fresh_batch_tools(mcp, *, principal, oauth_meta, env=os.environ, connection_factory=None):
    raw = env.get('XJ_FRESH_BATCH_HOST_CONFIG')
    if not raw:
        return {'enabled': False, 'tools': []}
    if env.get('XJ_FRESH_BATCH_HOST_ENABLED') != 'true':
        return {'enabled': False, 'tools': []}
    if connection_factory is None:
        from service_extension import original_redis_connection
        connection_factory = original_redis_connection
    rpc = BatchRPC(config=json.loads(raw),
                   ingress_key=bytes.fromhex(env['XJ_FRESH_BATCH_INGRESS_KEY']),
                   connection_factory=connection_factory)

    # A separate, explicit management gate over this same signed transport.
    # The host still requires original interactive consent and live user policy.
    from deer_management_rpc import install_management_rpc_tools
    install_management_rpc_tools(mcp, rpc=rpc, principal=principal,
        oauth_meta=oauth_meta, enabled=env.get('XJ_AGENT_MANAGEMENT_ENABLED') == 'true')

    async def invoke(action, request):
        owner = principal()
        try:
            result = await asyncio.to_thread(rpc.call, owner=owner, parent=request.parent_id,
                action=action, operation_key=request.operation_key,
                payload=request.model_dump(exclude={'parent_id', 'operation_key'}, exclude_none=True))
            if principal() != owner:
                raise ToolError('BATCH_PRINCIPAL_CHANGED_QUERY_ONLY')
            return result
        except ToolError:
            raise
        except Exception:
            raise ToolError('BATCH_OUTCOME_UNKNOWN_QUERY_SAME_OPERATION') from None

    @mcp.tool(meta=oauth_meta, annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False,
                                                        idempotentHint=True, openWorldHint=True))
    async def batch_task(request: Submit | Execute | Stop | ExecuteBatch) -> dict:
        """Save a goal and child tasks, or explicitly execute ONE saved item.

        For bounded background execution: execution_mode=codex_batch_once, batch_id,
        item_ids (1..3 independent saved pending items), parent_id and operation_key.
        To execute one: execution_mode=codex_once, batch_id, item_id, parent_id,
        operation_key. No new items or prompt accepted in execute mode.
        To stop one owned running item: execution_mode=stop_codex_once with
        batch_id, item_id, parent_id and a fresh operation_key. No PID accepted.
        Execution is explicit and requires operator-enabled host capability.
        Query UNKNOWN with batch_receipt(operation_kind=execute), same key.

        With goal only, return the planning contract without saving or launching.
        This GPT decomposes it, then calls once with the same goal/key and items.
        The original Holder atomically saves goal and children. Verify with
        batch_status(include_plan=true, item_offset=0). No extra model is called.
        Items must be independent. Ordinary goal/items requests only store pending work.
        Unknown commit outcomes: retain the same key and query batch_receipt.
        """
        if isinstance(request, (Execute, Stop, ExecuteBatch)):
            return await invoke('execute', request)

        if request.items is None:
            owner = principal()
            rpc.check(owner, request.parent_id)
            result = planning_response(request)
            if principal() != owner:
                raise ToolError('BATCH_PRINCIPAL_CHANGED_QUERY_ONLY')
            return result
        return await invoke('submit', request)

    if 'execute' in rpc.config['actions']:
        @mcp.tool(meta=oauth_meta, annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True,
                                                            idempotentHint=True, openWorldHint=True))
        async def batch_stop(request: Stop) -> dict:
            """Stop one original owned live Codex group; never the whole host.

            Query an unknown stop with batch_receipt(operation_kind=execute)
            and the exact original stop key. Do not resend with a new key.
            """
            return await invoke('execute', request)

    if 'execute' in rpc.config['actions']:
        @mcp.tool(meta=oauth_meta, annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False,
                                                            idempotentHint=True, openWorldHint=True))
        async def batch_execute_batch(request: ExecuteBatch) -> dict:
            """Run 1..3 saved independent pending items sequentially on the original worker.

            Explicit batch only; no global scanning, retries, DAG or native Work route.
            Query pending/unknown with batch_receipt(operation_kind=execute), same key.
            Each completed result stays in original SQL; stop one with batch_stop.
            """
            return await invoke('execute', request)

    @mcp.tool(meta=oauth_meta, annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False,
                                                        idempotentHint=True, openWorldHint=True))
    async def batch_status(request: Status) -> dict:
        """Read the original Holder's stored batch progress, never native live state.

        Use a new read operation key for a fresh snapshot; repeat keys recover
        the previous exact read. Use batch_receipt when batch_id is unknown.
        """
        return await invoke('read', request)

    @mcp.tool(meta=oauth_meta, annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True,
                                                        idempotentHint=True, openWorldHint=True))
    async def cancel_batch(request: Cancel) -> dict:
        """Cancel unstarted items through the original Holder. No native stop is claimed."""
        return await invoke('cancel', request)

    if 'execute' in rpc.config['actions']:
        @mcp.tool(meta=oauth_meta, annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False,
                                                            idempotentHint=True, openWorldHint=True))
        async def batch_execute(request: Execute) -> dict:
            """Explicitly execute ONE saved pending item with the host's Codex CLI.

            Never automatic. The original Holder reserves once before launch.
            Unknown response: query batch_receipt(operation_kind=execute) with
            the SAME key, then batch_status(include_result=true,item_offset=0).
            No retry, no native Work spawn, no claim of container isolation.
            """
            return await invoke('execute', request)

    @mcp.tool(meta=oauth_meta, annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False,
                                                        idempotentHint=True, openWorldHint=False))
    async def batch_host_status() -> dict:
        """Discover the enrolled parent and verify the fresh batch worker's signed heartbeat."""
        owner = principal()
        rpc.check(owner, rpc.config['parent_id'])
        def read_status():
            with rpc.connection_factory() as redis:
                raw = redis.get(rpc.prefix + ':heartbeat')
            ready = False
            observed = None
            if raw:
                signed = json.loads(raw)
                body = signed['body']
                rpc.verifier.verify(base64.b64decode(signed['signature'], validate=True), canonical(body))
                ready = (body.get('domain') == 'fresh-deer-batch-heartbeat-v1'
                    and body.get('generation') == rpc.config['generation']
                    and body.get('parent_id') == rpc.config['parent_id']
                    and 0 <= time.time() - body.get('observed_at', 0) <= 30)
                observed = body.get('observed_at') if ready else None
            return {'generation': rpc.config['generation'], 'parent_id': rpc.config['parent_id'],
                    'expires_at': rpc.config['expires_at'], 'worker_heartbeat_verified': ready,
                    'lifecycle': rpc.config.get('lifecycle', 'bounded-test'),
                    'execution_available': 'execute' in rpc.config['actions'],
                    'observed_at': observed, 'automatic_dispatch': False}
        try:
            result = await asyncio.to_thread(read_status)
            if principal() != owner:
                raise ToolError('BATCH_PRINCIPAL_CHANGED_QUERY_ONLY')
            return result
        except ToolError:
            raise
        except Exception:
            raise ToolError('BATCH_HOST_STATUS_UNAVAILABLE') from None

    @mcp.tool(meta=oauth_meta, annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False,
                                                        idempotentHint=True, openWorldHint=False))
    async def batch_receipt(request: ReceiptQuery) -> dict:
        """Query one original submit/cancel key without enqueueing, repeating or extending it."""
        owner = principal()
        try:
            result = await asyncio.to_thread(rpc.query, owner=owner, parent=request.parent_id,
                operation_key=request.operation_key, action=request.operation_kind)
            if principal() != owner:
                raise ToolError('BATCH_PRINCIPAL_CHANGED_QUERY_ONLY')
            return result
        except ToolError:
            raise
        except Exception:
            raise ToolError('BATCH_RECEIPT_UNAVAILABLE_QUERY_ONLY') from None

    return {'enabled': True, 'tools': ['batch_task', 'batch_status', 'cancel_batch', 'batch_receipt', 'batch_host_status']}


