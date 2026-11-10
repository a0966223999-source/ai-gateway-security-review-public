"""NEW versioned control protocol. Does not replace task-completion reports.

The original host must verify native identity and Source before signing this
domain. Sharing its fixed PUBLIC key does not make the domains interchangeable.
"""
import base64
import hashlib
import json
import math
import re

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

CONTROL_DOMAIN = 'gateway-cycle-control-v1'
FORMAL = 'gateway.continuation.ready'
MAILBOX = 'gateway_internal_v1'
HEX = re.compile(r'^[0-9a-f]{64}$')
BINDING_FIELDS = {
    'logical_event_id', 'mailbox_operation_key', 'mailbox_digest',
    'source_operation_key', 'source_key', 'source_ref', 'producer_thread_id',
    'producer_turn_id', 'checkpoint_sha256', 'payload_sha256', 'base_sha256',
    'mapping_sha256', 'next_step_sha256',
}


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'),
                      ensure_ascii=False, allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def exact_json(raw):
    if not isinstance(raw, str) or len(raw.encode()) > 65536:
        raise ValueError('JSON_SIZE')
    value = json.loads(raw)
    if canonical(value) != raw:
        raise ValueError('EXACT_CANONICAL_JSON_REQUIRED')
    return value


def validate_binding(binding, event_id):
    if not isinstance(binding, dict) or set(binding) != BINDING_FIELDS:
        raise ValueError('BINDING_SCHEMA')
    if binding['logical_event_id'] != event_id:
        raise ValueError('LOGICAL_EVENT_BINDING')
    for key, value in binding.items():
        if not isinstance(value, str) or not value or len(value.encode()) > 1024:
            raise ValueError('BINDING_VALUE')
        if (key.endswith('_sha256') or key in ('logical_event_id', 'mailbox_digest')) and not HEX.fullmatch(value):
            raise ValueError('BINDING_DIGEST')
    return binding


def notification_id(owner, mailbox, event_id, generation):
    if type(generation) is not int or generation < 1:
        raise ValueError('NOTIFICATION_GENERATION')
    return digest({'domain': 'gateway-control-notification-v1', 'owner': owner,
                   'mailbox': mailbox, 'logical_event_id': event_id,
                   'generation': generation})


class HostProofVerifier:
    """Verify a NEW exact action proof, signed by the original fixed host only."""

    def __init__(self, public_key):
        self.key = Ed25519PublicKey.from_public_bytes(public_key)

    def verify(self, envelope, *, action, owner, now, domain=CONTROL_DOMAIN):
        if not isinstance(envelope, dict) or set(envelope) != {'body', 'signature'}:
            raise ValueError('PROOF_ENVELOPE')
        body = envelope['body']
        required = {'domain', 'action', 'owner', 'mailbox', 'event_id',
                    'generation', 'issued_at', 'expires_at', 'evidence'}
        if not isinstance(body, dict) or set(body) != required:
            raise ValueError('PROOF_SCHEMA')
        if (body['domain'] != domain or body['action'] != action or
                body['owner'] != owner or body['mailbox'] != MAILBOX or
                not isinstance(body['event_id'], str) or not HEX.fullmatch(body['event_id']) or
                type(body['generation']) is not int or body['generation'] < 0 or
                not isinstance(body['evidence'], dict)):
            raise ValueError('PROOF_SCOPE')
        for key in ('issued_at', 'expires_at'):
            if type(body[key]) not in (int, float) or not math.isfinite(body[key]):
                raise ValueError('PROOF_TIME')
        if not now - 15 <= body['issued_at'] <= now + 2 or not now < body['expires_at'] <= body['issued_at'] + 30:
            raise ValueError('PROOF_EXPIRED_OR_STALE')
        self.key.verify(base64.b64decode(envelope['signature'], validate=True), canonical(body).encode())
        return body


def validate_current(evidence, binding, now):
    """Host attestation is mandatory; ordinary OAuth/evidence booleans cannot sign."""
    if evidence.get('binding') != binding:
        raise ValueError('CURRENT_BINDING_CHANGED')
    if any(evidence.get(key) is not True for key in ('authority_current', 'source_current', 'zero_effects')):
        raise ValueError('CURRENT_AUTHORITY_OR_EFFECT')
    if evidence.get('stop') is not False:
        raise ValueError('STOP_REQUESTED')
    validate_watch(evidence, binding)
    if not isinstance(evidence.get('receiver_run_id'), str) or not evidence['receiver_run_id']:
        raise ValueError('RECEIVER_RUN_REQUIRED')
    observed = evidence.get('observed_at')
    if type(observed) not in (int, float) or not now - 5 <= observed <= now + 2:
        raise ValueError('CURRENT_OBSERVATION_STALE')
    expiry = evidence.get('scope_expires_at')
    if type(expiry) not in (int, float) or not math.isfinite(expiry) or expiry <= now:
        raise ValueError('CURRENT_SCOPE_EXPIRED')



def validate_watch(evidence, binding):
    """Only use a fixed-host signed watch; the host MUST read the actual Source.

    Receipt-derived identity changes observation only. It NEVER grants a Work
    send, claim, save, retry or a new lifetime. Unknown admission is not started.
    """
    watch = evidence.get('watch')
    if not isinstance(watch, dict) or set(watch) != {
            'mode', 'thread_id', 'turn_id', 'platform_receipt_sha256'}:
        raise ValueError('SIGNED_WATCH_REQUIRED')
    if any(not isinstance(watch.get(key), str) or not watch[key] or
           len(watch[key]) > 1024 for key in ('thread_id', 'turn_id')):
        raise ValueError('WATCH_IDENTITY')
    if watch['mode'] == 'producer':
        if (evidence.get('once_state') != 'fresh' or
                watch['thread_id'] != binding['producer_thread_id'] or
                watch['turn_id'] != binding['producer_turn_id'] or
                watch['platform_receipt_sha256'] is not None):
            raise ValueError('PRODUCER_WATCH_BINDING')
    elif watch['mode'] == 'resumed':
        receipt = watch['platform_receipt_sha256']
        if (evidence.get('once_state') != 'started' or
                watch['thread_id'] != binding['producer_thread_id'] or
                watch['turn_id'] == binding['producer_turn_id'] or
                not isinstance(receipt, str) or not HEX.fullmatch(receipt)):
            raise ValueError('ORIGINAL_STARTED_RECEIPT_WATCH_REQUIRED')
    else:
        raise ValueError('WATCH_MODE')
    return watch
