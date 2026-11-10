"""Opt-in same-service control tools. No task-report format is introduced.

Caller supplies the ORIGINAL authenticated mailbox/client session and original
formal Events instance. All mutations require a fresh NEW-domain host signature.
The ended branch needs the existing-completion-report adapter; absent it fails
closed. Direct publish happens before receiver return; platform v2 acceptance
and actual turn scheduling remain separate, unverified gates.
"""
import asyncio
import json
import secrets
import time

from mcp.types import ToolAnnotations
from mcp_events import signed_headers, subscription_record

from cycle_envelope import make_envelope
from cycle_store import CycleStore, CycleUnknownOutcome
from protocol import (CONTROL_DOMAIN, FORMAL, MAILBOX, canonical, digest, exact_json,
                      notification_id, validate_binding, validate_current)


def validate_policy(policy):
    required = {'wait_seconds', 'round_budget_seconds', 'max_generations', 'min_interval_seconds'}
    if not isinstance(policy, dict) or set(policy) != required or policy['wait_seconds'] != 80:
        raise ValueError('EXPLICIT_CYCLE_POLICY_REQUIRED')
    if any(type(value) is not int for value in policy.values()):
        raise ValueError('INTEGER_CYCLE_POLICY_REQUIRED')
    if (not 85 <= policy['round_budget_seconds'] <= 180 or
            not 2 <= policy['max_generations'] <= 100 or
            not 80 <= policy['min_interval_seconds'] <= 3600):
        raise ValueError('BOUNDED_CYCLE_POLICY_REQUIRED')
    return dict(policy)


class CycleController:
    def __init__(self, *, formal, proof_verifier, policy, subscription_id, completed_report_adapter=None,
                 clock=None):
        self.formal = formal
        self.verifier = proof_verifier
        self.policy = validate_policy(policy)
        if (not isinstance(subscription_id, str) or len(subscription_id) != 68 or
                not subscription_id.startswith('sub_') or
                any(char not in '0123456789abcdef' for char in subscription_id[4:])):
            raise ValueError('FIXED_ORIGINAL_SUBSCRIPTION_REQUIRED')
        self.subscription_id = subscription_id
        self.completed_report_adapter = completed_report_adapter
        self.clock = clock or time.time

    async def _call(self, store, action, event_id, **args):
        return await asyncio.to_thread(store.transition, action, event_id, **args)

    async def _proof(self, inbox, raw, action):
        envelope = exact_json(raw)
        body = self.verifier.verify(envelope, action=action, owner=inbox.owner,
                                    now=self.clock(), domain=CONTROL_DOMAIN)
        evidence = body['evidence']
        binding = validate_binding(evidence.get('binding'), body['event_id'])
        store = CycleStore(inbox)
        original = await asyncio.to_thread(store.original, body['event_id'])
        if (not original or original.get('event_name') != FORMAL or
                original.get('operation_key') != binding['mailbox_operation_key'] or
                original.get('digest') != binding['mailbox_digest']):
            raise ValueError('ORIGINAL_MAILBOX_BINDING')
        payload = json.loads(original['message'])
        if not isinstance(payload, dict) or payload.get('kind') is not None:
            raise ValueError('NOT_ORIGINAL_CONTINUATION')
        work, source = payload.get('work'), payload.get('source')
        if (not isinstance(work, dict) or not isinstance(source, dict) or
                not isinstance(work.get('binding'), dict) or
                work['binding'].get('operationKey') != binding['source_operation_key'] or
                source.get('locator') != binding['source_ref'] or
                digest(work.get('nextAction')) != binding['next_step_sha256'] or
                digest(payload) != binding['payload_sha256']):
            raise ValueError('ORIGINAL_PAYLOAD_BINDING')
        if self.formal.authorize_event(action, inbox, body['event_id']) is not True:
            raise ValueError('ORIGINAL_FORMAL_AUTHORITY_REQUIRED')
        return body, evidence, binding, store

    async def _current(self, store, body, evidence, binding):
        try:
            validate_current(evidence, binding, self.clock())
        except ValueError:
            state = await self._call(store, 'query', body['event_id'])
            if state['status'] != 'CYCLE_MISSING':
                return await self._call(store, 'safety_stop', body['event_id'],
                    binding_sha256=digest(binding), reason='CURRENT_SCOPE_STOPPED')
            raise
        return None

    async def begin(self, inbox, raw):
        body, evidence, binding, store = await self._proof(inbox, raw, 'begin')
        stopped = await self._current(store, body, evidence, binding)
        if stopped is not None:
            return stopped
        if evidence.get('policy') != self.policy or evidence.get('trusted_delivery_verified') is not True:
            raise ValueError('HOST_POLICY_AND_DELIVERY_REQUIRED')
        expected = body['event_id'] if body['generation'] == 0 else notification_id(
            inbox.owner, MAILBOX, body['event_id'], body['generation'])
        if evidence.get('notification_id') != expected:
            raise ValueError('TRUSTED_NOTIFICATION_ID')
        subscription = evidence.get('subscription_id')
        if subscription != self.subscription_id:
            raise ValueError('ORIGINAL_SUBSCRIPTION_REQUIRED')
        return await self._call(store, 'begin', body['event_id'], generation=body['generation'],
            binding=binding, binding_sha256=digest(binding), policy=self.policy,
            subscription_id=subscription, receiver_run_id=evidence['receiver_run_id'],
            notification_id=expected, scope_expires_at=evidence['scope_expires_at'],
            watch=evidence['watch'], watch_sha256=digest(evidence['watch']))

    async def observe(self, inbox, raw):
        body, evidence, binding, store = await self._proof(inbox, raw, 'observe')
        stopped = await self._current(store, body, evidence, binding)
        if stopped is not None:
            return stopped
        state = await self._call(store, 'query', body['event_id'])
        if state.get('binding') != binding:
            return await self._call(store, 'safety_stop', body['event_id'],
                binding_sha256=digest(binding), reason='CURRENT_BINDING_CHANGED')
        if (state.get('generation') != body['generation'] or
                state.get('receiver_run_id') != evidence['receiver_run_id']):
            raise ValueError('ROUND_BINDING')
        if state.get('watch') != evidence['watch']:
            return await self._call(store, 'safety_stop', body['event_id'],
                binding_sha256=digest(binding), reason='WATCH_CHANGED')
        if state['status'] != 'active':
            return state
        if self.clock() < state['started_at'] + 80 or evidence['observed_at'] < state['started_at'] + 80:
            raise ValueError('EIGHTY_SECONDS_NOT_ELAPSED')
        # First inspect the verified native status. Active MUST NOT consult a
        # completion/report message, even if one exists and claims completion.
        if (evidence.get('native_thread_id') != evidence['watch']['thread_id'] or
                evidence.get('native_turn_id') != evidence['watch']['turn_id']):
            native = 'unknown'
        elif evidence.get('native_status') in ('running', 'inProgress'):
            native = 'active'
        elif evidence.get('native_status') in ('completed', 'failed', 'interrupted'):
            native = 'ended'
        else:
            native = 'unknown'
        decision = 'unknown'
        if native == 'ended' and self.completed_report_adapter is not None:
            # The adapter must read the ACTUAL original mailbox completion
            # report and validate its EXISTING schema; no Source-only fallback.
            try:
                report = await self.completed_report_adapter(inbox=inbox,
                    binding=json.loads(canonical(binding)), generation=body['generation'],
                    observation=json.loads(canonical(evidence)))
            except Exception:
                report = None  # A missing/failed read is not task completion.
            attestations = {'original_mailbox_message_read', 'sender_identity_verified',
                'source_identity_verified', 'source_revision_current',
                'checkpoint_verified', 'result_contract_verified'}
            if (isinstance(report, dict) and
                    all(report.get(key) is True for key in attestations) and
                    report.get('decision') in ('verified_agent_reported_complete',
                        'verified_continue', 'failed_requires_review')):
                decision = report['decision']
        stopped = await self._current(store, body, evidence, binding)
        if stopped is not None:
            return stopped
        return await self._call(store, 'observe', body['event_id'], generation=body['generation'],
            receiver_run_id=evidence['receiver_run_id'], binding_sha256=digest(binding),
            native_state=native, task_decision=decision, observed_at=evidence['observed_at'],
            watch_sha256=digest(evidence['watch']),
            scope_expires_at=evidence['scope_expires_at'], notification_id=notification_id(
                inbox.owner, MAILBOX, body['event_id'], body['generation'] + 1))

    async def publish(self, inbox, raw):
        body, evidence, binding, store = await self._proof(inbox, raw, 'publish')
        stopped = await self._current(store, body, evidence, binding)
        if stopped is not None:
            return stopped
        state = await self._call(store, 'query', body['event_id'])
        if state.get('watch') != evidence['watch']:
            return await self._call(store, 'safety_stop', body['event_id'],
                binding_sha256=digest(binding), reason='WATCH_CHANGED')
        if state.get('receiver_run_id') != evidence['receiver_run_id']:
            raise ValueError('PUBLISH_RECEIVER_RUN_BINDING')
        notice_id = notification_id(inbox.owner, MAILBOX, body['event_id'], body['generation'])
        lock = secrets.token_hex(16)
        acquired = await self._call(store, 'publish_acquire', body['event_id'],
            generation=body['generation'], notification_id=notice_id, lock=lock,
            binding_sha256=digest(binding), scope_expires_at=evidence['scope_expires_at'],
            watch_sha256=digest(evidence['watch']))
        if acquired.get('status') != 'acquired':
            return acquired
        delivered, status = False, 0
        try:
            subscription = acquired['subscription']
            protected = self.formal.box.open(subscription['protected'], subscription['id'])
            token = await self.formal.verifier.verify_token(protected['access_token'])
            current = subscription_record(await asyncio.to_thread(
                inbox.run, 'subscription_get', id=subscription['id']))
            if (token is None or not current or current['revision'] != subscription['revision'] or
                    current['expires_at'] <= self.clock() or current['access_until'] <= self.clock() or
                    self.clock() >= acquired['publish_deadline'] or
                    self.formal.authorize_event('deliver', inbox, body['event_id']) is not True):
                raise ValueError('DELIVERY_AUTHORITY_CHANGED')
            envelope = make_envelope(owner=inbox.owner, event_id=body['event_id'],
                generation=body['generation'], created_at=acquired['successor']['created_at'], binding=binding)
            payload = canonical(envelope).encode()
            keys = [protected['secret']]
            if protected.get('previous_until', 0) > self.clock():
                keys.append(protected['previous_secret'])
            # One attempt only. A lost callback outcome never causes resending.
            status, _ = await self.formal.sender(subscription['url'], signed_headers(
                subscription['id'], notice_id, payload, keys), payload,
                min(2.5, acquired['publish_deadline'] - self.clock()))
            delivered = 200 <= status < 300
        except asyncio.CancelledError:
            raise
        except Exception:
            delivered = False
        finally:
            result = await asyncio.shield(self._call(store, 'publish_finish', body['event_id'],
                notification_id=notice_id, lock=lock, binding_sha256=digest(binding),
                delivered=delivered, http_status=status))
        return result


def install_cycle_transport(mcp, *, original_session, formal, proof_verifier,
                            principal, oauth_meta, policy, enabled=False,
                            completed_report_adapter=None, subscription_id=None):
    """Register on the SAME MCP; no client/secret/env/subscription is created.

    original_session(mailbox_id) is an async context manager yielding the
    original owner-scoped Inbox with its original synchronous Redis client.
    It must be supplied by the original service; no HTTP/alternative Redis path.
    """
    if enabled is not True:
        return {'enabled': False, 'tools': []}
    controller = CycleController(formal=formal, proof_verifier=proof_verifier,
        policy=policy, subscription_id=subscription_id,
        completed_report_adapter=completed_report_adapter)
    names = []
    for action in ('begin', 'observe', 'publish'):
        def register(action=action):
            async def call(mailbox_id: str, envelope_json: str) -> dict:
                if mailbox_id != MAILBOX:
                    raise ValueError('ORIGINAL_MAILBOX_REQUIRED')
                owner = principal()
                async with original_session(mailbox_id) as inbox:
                    if inbox.owner != owner or inbox.mailbox_id != MAILBOX:
                        raise ValueError('ORIGINAL_OWNER_REQUIRED')
                    try:
                        return await getattr(controller, action)(inbox.for_event(FORMAL), envelope_json)
                    except CycleUnknownOutcome:
                        return {'status': 'UNKNOWN_QUERY_ONLY', 'retry': False,
                                'task_completed': False, 'businessAcceptanceVerified': False}
            call.__name__ = 'gateway_cycle_' + action
            call.__doc__ = ('Versioned same-mailbox control ' + action + '. Original host proof required; '
                            'UNKNOWN is query-only. Does not run Work or change signed RPC80.')
            mcp.tool(meta=oauth_meta, annotations=ToolAnnotations(readOnlyHint=False,
                destructiveHint=False, idempotentHint=True, openWorldHint=action == 'publish'))(call)
            names.append(call.__name__)
        register()

    @mcp.tool(meta=oauth_meta, annotations=ToolAnnotations(readOnlyHint=True,
        destructiveHint=False, idempotentHint=True, openWorldHint=False))
    async def gateway_cycle_query(mailbox_id: str, event_id: str) -> dict:
        """Read saved cycle outcome; no publish, report interpretation or retry."""
        if mailbox_id != MAILBOX:
            raise ValueError('ORIGINAL_MAILBOX_REQUIRED')
        owner = principal()
        async with original_session(mailbox_id) as inbox:
            if inbox.owner != owner:
                raise ValueError('ORIGINAL_OWNER_REQUIRED')
            return await asyncio.to_thread(CycleStore(inbox).transition, 'query', event_id)
    return {'enabled': True, 'tools': names + ['gateway_cycle_query'],
            'completion_report_adapter_configured': completed_report_adapter is not None}
