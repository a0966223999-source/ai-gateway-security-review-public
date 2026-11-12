"""Opt-in signer cutover on the original RPC; old saved replies are query-only.

Only trusted deployment settings may supply this policy. There is no key
generation, activation, migration, credential loading, or Redis write here.
"""
import hashlib
import json
import os
import re
import time

from transport import ReplyVerifier, canonical

POLICY_ENV = 'XJ_SOURCE_RPC_CUTOVER_POLICY'
PUBLIC_ENV = 'XJ_SOURCE_RPC_HOST_PUBLIC_KEY'
HEX = re.compile(r'[0-9a-f]{64}\Z')


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('DUPLICATE_TRUST_POLICY_FIELD')
        result[key] = value
    return result


class CutoverReplyVerifier(ReplyVerifier):
    """One active signer, with an exact allowlist of already saved old replies."""

    def __init__(self, public_key, policy_raw):
        super().__init__(public_key)
        if not isinstance(policy_raw, str) or len(policy_raw.encode()) > 65536:
            raise ValueError('CUTOVER_POLICY_SIZE')
        policy = json.loads(policy_raw, object_pairs_hook=_unique)
        fields = {'schema', 'mailbox_id', 'cutover_epoch', 'legacy_public_key',
                  'historical_replies'}
        if (not isinstance(policy, dict) or set(policy) != fields or
                policy['schema'] != 'source-rpc-cutover-v1' or
                policy['mailbox_id'] != 'gateway_internal_v1' or
                type(policy['cutover_epoch']) is not int or
                policy['cutover_epoch'] <= 0):
            raise ValueError('CUTOVER_POLICY_SCOPE')
        legacy = policy['legacy_public_key']
        if (not isinstance(legacy, str) or not HEX.fullmatch(legacy) or
                bytes.fromhex(legacy) == public_key):
            raise ValueError('DISTINCT_LEGACY_PUBLIC_KEY_REQUIRED')
        records = policy['historical_replies']
        if not isinstance(records, list) or len(records) > 100:
            raise ValueError('BOUNDED_HISTORICAL_REPLY_LIST_REQUIRED')
        self.historical = {}
        for record in records:
            if (not isinstance(record, dict) or
                    set(record) != {'event_id', 'envelope_sha256'} or
                    any(not isinstance(value, str) or not HEX.fullmatch(value)
                        for value in record.values()) or
                    record['event_id'] in self.historical):
                raise ValueError('EXACT_HISTORICAL_REPLY_PIN_REQUIRED')
            self.historical[record['event_id']] = record['envelope_sha256']
        self.legacy = ReplyVerifier(bytes.fromhex(legacy))
        self.cutover_epoch = policy['cutover_epoch']

    def require_active(self):
        if time.time() < self.cutover_epoch:
            raise ValueError('CUTOVER_NOT_REACHED')

    def _created_at(self, message, context):
        self.require_active()
        created = message.get('created_at')
        if (message.get('event_id') != context['eventId'] or
                message.get('event_name') != 'gateway.continuation.ready' or
                context['mailbox'] != 'gateway_internal_v1' or
                type(created) is not int or created < 0 or created > time.time()):
            raise ValueError('ORIGINAL_EVENT_TIME_REQUIRED')
        return created

    def require_live_message(self, message, context):
        if self._created_at(message, context) < self.cutover_epoch:
            raise ValueError('LEGACY_EVENT_QUERY_ONLY')

    def verify_message(self, envelope, context, message, *, historical=False):
        created = self._created_at(message, context)
        if created >= self.cutover_epoch:
            # Never try the legacy key on new events, even during query.
            return self.verify(envelope, context)
        if historical is not True:
            raise ValueError('LEGACY_EVENT_QUERY_ONLY')
        digest = hashlib.sha256(canonical(envelope).encode()).hexdigest()
        if self.historical.get(context['eventId']) != digest:
            raise ValueError('HISTORICAL_REPLY_NOT_FROZEN')
        return self.legacy.verify(envelope, context)


def deployment_trust_snapshot():
    """Snapshot public-only trusted settings, never request or event payload."""
    return (os.getenv(PUBLIC_ENV, ''), os.getenv(POLICY_ENV, ''))


def load_reply_verifier(snapshot):
    public, policy_raw = snapshot
    if not re.fullmatch(r'[0-9a-f]{64}', public):
        raise ValueError('PINNED_HOST_KEY_REQUIRED')
    if not policy_raw:
        return ReplyVerifier(bytes.fromhex(public))
    verifier = CutoverReplyVerifier(bytes.fromhex(public), policy_raw)
    verifier.require_active()
    return verifier
