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

from batch_mcp_entry import Submit, Status, Cancel, RequestBase
from fresh_batch_host import canonical, digest

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
                or not self.config['created_at'] <= time.time() < self.config['expires_at']):
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
        if action not in ('submit', 'read', 'cancel'):
            raise ValueError('BATCH_ACTION_INVALID')
        body = dict(generation=self.config['generation'], owner=owner, parent_id=parent,
                    action=action, operation_key=operation_key, payload=payload,
                    request_id=request_id(self.config, action, operation_key),
                    expires_at=min(self.config['expires_at'], time.time() + 60))
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
    operation_kind: Literal['submit', 'cancel'] = 'submit'


def install_fresh_batch_tools(mcp, *, principal, oauth_meta, env=os.environ, connection_factory=None):
    raw = env.get('XJ_FRESH_BATCH_HOST_CONFIG')
    if not raw:
        return {'enabled': False, 'tools': []}
    if env.get('XJ_FRESH_BATCH_HOST_ENABLED') != 'true':
        return {'enabled': False, 'tools': []}
    from service_extension import original_redis_connection
    rpc = BatchRPC(config=json.loads(raw),
                   ingress_key=bytes.fromhex(env['XJ_FRESH_BATCH_INGRESS_KEY']),
                   connection_factory=connection_factory or original_redis_connection)

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
    async def batch_task(request: Submit) -> dict:
        """Record independent child tasks on the newly enrolled original Deer host.

        This stores pending work only. It does not launch agents. On an unknown
        response, retain the same operation key and call batch_receipt.
        """
        if len({i.key for i in request.items}) != len(request.items):
            raise ToolError('DUPLICATE_BATCH_ITEM_KEYS')
        return await invoke('submit', request)

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

    return {'enabled': True, 'tools': ['batch_task', 'batch_status', 'cancel_batch', 'batch_receipt']}


async def run_worker(host, redis, repository):
    """One explicit worker; no replay, scheduler, agent driver or legacy event claim."""
    queue = prefix(host.config) + ':queue'
    with host.single_worker():
        while host.check('read'):
            item = await asyncio.to_thread(redis.blpop, queue, timeout=1)
            if item is None:
                continue
            key = item[1].decode() if isinstance(item[1], bytes) else item[1]
            expected_prefix = prefix(host.config) + ':request:'
            if not key.startswith(expected_prefix) or len(key) != len(expected_prefix) + 64:
                continue
            stored = await asyncio.to_thread(redis.get, key)
            try:
                record = json.loads(stored)
                body = host.verify_request(record['envelope'])
                if key != expected_prefix + body['request_id']:
                    continue
                # A new generation uses a fresh signer. Existing attempts are
                # never rerun even if a response was lost or the worker crashed.
                response = await host.execute(record['envelope'], repository=repository)
                await asyncio.to_thread(redis.eval, RESPOND, 2, key, key + ':response',
                                        stored, canonical(response).decode(), body['request_id'])
            except Exception:
                # Preserve the immutable request/attempt and uncertainty. No
                # fabricated success, ACK or automatic requeue on failure.
                continue
