"""Same-inbox bounded RPC wait; Pub/Sub wakes, existing event stores response.
Injected redis.asyncio client uses the original approved Redis connection.
No Redis creation, credential loading, background task, or polling.
"""
import asyncio
import base64
import hashlib
import json
import re
import time
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey


def canonical(x):
    return json.dumps(x, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)


def channel(key, operation, nonce):
    return key + ':rpc-reply:' + hashlib.sha256((operation+'|'+nonce).encode()).hexdigest()


# Original inbox key is one JSON document, not a Redis hash. Keep its TTL and
# all transport state. Event message contains original immutable RPC locator.
READ = """
local raw=redis.call('GET',KEYS[1]); if not raw then return false end
local d=cjson.decode(raw); local m=d.messages[ARGV[1]]
if not m then return false end
return cjson.encode(m)
"""
COMMIT = """
local raw=redis.call('GET',KEYS[1]); if not raw then return redis.error_reply('EVENT_MISSING') end
local d=cjson.decode(raw); local m=d.messages[ARGV[1]]
if not m or m.event_name~='gateway.continuation.ready' then return redis.error_reply('EVENT_SCOPE') end
local now=tonumber(redis.call('TIME')[1]); local q=cjson.decode(m.message)
if q.kind~='source-finalize-rpc-v1' or m.operation_key~=ARGV[2] or q.nonce~=ARGV[3]
 or q.requestSHA256~=ARGV[4] or q.deadline~=tonumber(ARGV[5]) then return redis.error_reply('REQUEST_BINDING') end
if now>=q.deadline or now>=m.expires_at then return redis.error_reply('EXPIRED') end
if m.rpc_response then
 if m.rpc_response~=ARGV[6] then return redis.error_reply('RESPONSE_CONFLICT') end
 return 'existing'
end
m.rpc_response=ARGV[6]
redis.call('SET',KEYS[1],cjson.encode(d),'KEEPTTL')
redis.call('PUBLISH',ARGV[7],ARGV[1])
return 'saved'
"""


class UnknownOutcome(Exception):
    pass


class ReplyVerifier:
    def __init__(self, public_key):
        # Pin supplied by original host setup, never a request or reply key.
        self.key = Ed25519PublicKey.from_public_bytes(public_key)

    def verify(self, envelope, context):
        if set(envelope) != {'body', 'signature'}:
            raise ValueError('REPLY_ENVELOPE')
        if len(canonical(envelope).encode()) > 65536:
            raise ValueError('REPLY_SIZE')
        body = envelope['body']
        if set(body) != set(context) | {'result'} or any(body.get(k) != v for k,v in context.items()):
            raise ValueError('REPLY_BINDING')
        if not isinstance(body.get('result'), dict):
            raise ValueError('REPLY_RESULT_REQUIRED')
        self.key.verify(base64.b64decode(envelope['signature'], validate=True), canonical(body).encode())
        return body['result']


def sign_reply(private_key, context, result):
    body = dict(context, result=result)
    return dict(body=body, signature=base64.b64encode(private_key.sign(canonical(body).encode())).decode())


class SameInboxRPC:
    def __init__(self, *, redis, inbox_key, owner, mailbox, verifier, current,
                 max_wait_seconds, function_budget_seconds, connector_budget_seconds):
        if (not callable(current) or not callable(getattr(verifier,'verify',None))
                or not 0 < max_wait_seconds < min(function_budget_seconds, connector_budget_seconds) - 5):
            raise ValueError('VERIFIED_BOUNDED_HOST_CONFIG_REQUIRED')
        self.redis, self.key, self.owner, self.mailbox = redis, inbox_key, owner, mailbox
        self.verifier, self.current, self.max_wait = verifier, current, max_wait_seconds

    def context(self, operation, nonce, request_sha, deadline, event):
        return dict(domain='source-finalize-rpc-reply-v1', owner=self.owner, mailbox=self.mailbox,
                    operation=operation, nonce=nonce, requestSHA256=request_sha, deadline=deadline, eventId=event)

    async def _read(self, event, context):
        if self.current() is not True:
            raise ValueError('HOST_AUTHORITY_REVOKED')
        raw = await self.redis.eval(READ, 1, self.key, event)
        if self.current() is not True:
            raise ValueError('HOST_AUTHORITY_REVOKED')
        if not raw:
            raise UnknownOutcome('EVENT_MISSING_QUERY_ONLY')
        message = json.loads(raw)
        req = json.loads(message['message'])
        if (message.get('event_id') != event or message.get('operation_key') != context['operation']
                or message.get('event_name') != 'gateway.continuation.ready'
                or req != dict(kind='source-finalize-rpc-v1', nonce=context['nonce'],
                               requestSHA256=context['requestSHA256'], deadline=context['deadline'])):
            raise ValueError('ORIGINAL_EVENT_BINDING')
        response = message.get('rpc_response')
        if response is None:
            return None
        return self.verifier.verify(json.loads(response), context)

    async def wait(self, *, operation, nonce, request_sha, deadline, send_once=None, existing_event=None):
        if (not re.fullmatch('[0-9a-f]{64}', nonce) or not re.fullmatch('[0-9a-f]{64}', request_sha)
                or not 0 < deadline-time.time() <= self.max_wait
                or (send_once is None) == (existing_event is None)):
            raise ValueError('RPC_SCOPE')
        if self.current() is not True:
            raise ValueError('HOST_AUTHORITY_REVOKED')
        ch = channel(self.key, operation, nonce)
        ps = self.redis.pubsub()
        event = existing_event
        send_started = False
        try:
            async with asyncio.timeout(deadline-time.time()):
                await ps.subscribe(ch)
                # consume Redis subscription acknowledgement before dispatch.
                stream = ps.listen()
                ack = await anext(stream)
                if ack.get('type') != 'subscribe' or ack.get('channel') not in (ch, ch.encode()):
                    raise ValueError('SUBSCRIPTION_UNCONFIRMED')
                if event is None:
                    # Must use original atomic Inbox.send, dispatch only on newly
                    # created event, and never retry unknown sends. This callback
                    # returns promptly after dispatch/admission, not host finish.
                    send_started = True
                    receipt = await send_once(dict(kind='source-finalize-rpc-v1', nonce=nonce,
                                                 requestSHA256=request_sha, deadline=deadline))
                    event = receipt['event_id']
                ctx = self.context(operation, nonce, request_sha, deadline, event)
                result = await self._read(event, ctx)
                if result is not None:
                    return result
                notifications = 0
                async for notice in stream:
                    if notice.get('type') == 'message' and notice.get('data') in (event, event.encode()):
                        notifications += 1
                        if notifications > 16:
                            raise UnknownOutcome('NOTICE_LIMIT_QUERY_ONLY')
                        result = await self._read(event, ctx)
                        if result is not None:
                            return result
                raise UnknownOutcome('SUBSCRIPTION_ENDED_QUERY_ONLY')
        except (TimeoutError, asyncio.CancelledError):
            # Event + original native host durable fence remain, no resend.
            raise UnknownOutcome('WAIT_UNKNOWN_QUERY_EXISTING_EVENT') from None
        except Exception as exc:
            if send_started or event is not None:
                raise UnknownOutcome('RPC_UNKNOWN_QUERY_EXISTING_OPERATION') from exc
            raise
        finally:
            # Cleanup failure/cancellation must not replace an UNKNOWN outcome.
            try:
                await asyncio.wait_for(ps.aclose(), timeout=1)
            except BaseException:
                pass

    async def commit(self, *, envelope, context):
        # MCP OAuth alone cannot sign/forge a host result. Verify before writing.
        if self.current() is not True or context['owner'] != self.owner or context['mailbox'] != self.mailbox:
            raise ValueError('HOST_AUTHORITY_REQUIRED')
        self.verifier.verify(envelope, context)
        from request_store import RequestStore
        request = await RequestStore(self.redis, self.key, self.current).read(context['eventId'])
        if hashlib.sha256(canonical(request).encode()).hexdigest() != context['requestSHA256']:
            raise ValueError('EXACT_REQUEST_SHA_MISMATCH')
        if self.current() is not True:
            raise ValueError('HOST_AUTHORITY_REVOKED')
        try:
            result = await self.redis.eval(COMMIT, 1, self.key, context['eventId'], context['operation'],
             context['nonce'], context['requestSHA256'], context['deadline'], canonical(envelope),
             channel(self.key, context['operation'], context['nonce']))
            if self.current() is not True:
                raise ValueError('HOST_AUTHORITY_REVOKED_AFTER_COMMIT')
            # Verify durable reply from original event, independent of eval return.
            observed = await self._read(context['eventId'], context)
            if observed != envelope['body']['result']:
                raise ValueError('RESPONSE_READBACK_MISMATCH')
            return result
        except Exception as exc:
            raise UnknownOutcome('RESPONSE_COMMIT_UNKNOWN_QUERY_ONLY') from exc
