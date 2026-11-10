"""Public-service-only verifier: no private host paths, Source or native tools.

CycleController MUST first verify the original host's whole observe proof.
Uses only the original same-mailbox row, fixed host key, original parser and
that authenticated observation. This file is deployable beside transport.py.
"""
import copy
import hashlib
import json
from cryptography.exceptions import InvalidSignature
try:
    from .transport import ReplyVerifier
    from .original_agent_result import parse_agent_result
except ImportError:
    from transport import ReplyVerifier
    from original_agent_result import parse_agent_result

def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)

def sha(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()

def parse(raw):
    def pairs(items):
        value = dict(items)
        if len(value) != len(items):
            raise ValueError('DUPLICATE_KEY')
        return value
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=lambda x: (_ for _ in ()).throw(ValueError('NONFINITE_JSON')))

FLAGS = ('original_mailbox_message_read', 'sender_identity_verified', 'source_identity_verified',
         'source_revision_current', 'checkpoint_verified', 'result_contract_verified')
SCOPE_KEYS = ('source_key', 'source_ref', 'source_operation_key', 'producer_thread_id',
              'producer_turn_id', 'checkpoint_sha256', 'next_step_sha256')

def request_result(request):
    if (type(request) is not dict or set(request) != {'v', 'id', 'source', 'method', 'params'}
            or type(request['v']) is not int or request['v'] != 1 or request['method'] != 'save'
            or type(request['id']) is not str or not request['id']
            or type(request['params']) is not dict or set(request['params']) != {'revision', 'document'}):
        raise ValueError('ORIGINAL_SAVE_REQUEST_REQUIRED')
    if len(canonical(request).encode()) > 65536:
        raise ValueError('ORIGINAL_SAVE_REQUEST_TOO_LARGE')
    result = request['params']['document']['continuation']['workerFinalization']['agentResult']
    return parse_agent_result(canonical(result))

def watched_binding(binding, observation, snapshot=None):
    """Public uses host-signed watch; host independently verifies Source receipt."""
    watch = observation.get('watch', dict(mode='producer', thread_id=binding['producer_thread_id'],
        turn_id=binding['producer_turn_id'], platform_receipt_sha256=None))
    if (set(watch) != {'mode', 'thread_id', 'turn_id', 'platform_receipt_sha256'}
            or watch['thread_id'] != binding['producer_thread_id']
            or watch['thread_id'] != observation.get('native_thread_id')
            or watch['turn_id'] != observation.get('native_turn_id')):
        raise ValueError('WATCH_BINDING')
    if watch['mode'] == 'producer':
        if watch['turn_id'] != binding['producer_turn_id'] or watch['platform_receipt_sha256'] is not None:
            raise ValueError('PRODUCER_WATCH_BINDING')
    elif watch['mode'] == 'resumed':
        if (type(watch['turn_id']) is not str or not watch['turn_id']
                or type(watch['platform_receipt_sha256']) is not str):
            raise ValueError('RESUMED_WATCH_BINDING')
        if snapshot is not None:
            scopes = [snapshot['document']['continuation']] + [
                h['continuation'] for h in snapshot['document'].get('continuationHistory', [])]
            matches = []
            for scope in scopes:
                wf = scope.get('workerFinalization', {}); delivery = wf.get('nativeDelivery', {})
                receipt = delivery.get('platformReceipt'); queue = wf.get('queueReceipt', {})
                if (type(receipt) is dict and sha(receipt) == watch['platform_receipt_sha256']
                        and receipt.get('admissionOutcome') == 'started'
                        and receipt.get('threadId') == watch['thread_id'] and receipt.get('turnId') == watch['turn_id']
                        and scope['item'].get('taskRef') == binding['source_key']
                        and scope['item'].get('operationKey') == binding['source_operation_key']
                        and all(delivery.get(k) == queue.get(k) and bool(queue.get(k))
                                for k in ('eventId', 'operationKey', 'payloadSHA256'))):
                    matches.append(receipt)
            if len(matches) != 1:
                raise ValueError('SOURCE_PLATFORM_RECEIPT_WATCH_REQUIRED')
    else:
        raise ValueError('WATCH_MODE')
    return dict(binding, producer_thread_id=watch['thread_id'], producer_turn_id=watch['turn_id'])

def read_pair(row, *, binding, mailbox_owner, mailbox_id, verifier):
    """Read historical committed original bytes. Expiry never authorizes replay."""
    if (type(row) is not dict or row.get('event_name') != 'gateway.continuation.ready'
            or row.get('rpc_state') != 'reserved_query_only'
            or type(row.get('rpc_request')) is not str or type(row.get('rpc_response')) is not str):
        raise ValueError('ORIGINAL_REQUEST_AND_SIGNED_OUTCOME_REQUIRED')
    raw = row['rpc_request']
    if len(raw.encode()) > 65536:
        raise ValueError('ORIGINAL_REQUEST_SIZE')
    req = parse(raw)
    if canonical(req) != raw:
        raise ValueError('CANONICAL_ORIGINAL_REQUEST_REQUIRED')
    payload = request_result(req)
    locator = parse(row['message'])
    request_sha = hashlib.sha256(raw.encode()).hexdigest()
    if (req['source'] != binding['source_key'] or row.get('rpc_request_sha256') != request_sha
            or row.get('rpc_nonce') != req['id'] or locator != dict(kind='source-finalize-rpc-v1',
                nonce=req['id'], requestSHA256=request_sha, deadline=row.get('rpc_deadline'))
            or type(row.get('event_id')) is not str or type(row.get('operation_key')) is not str):
        raise ValueError('ORIGINAL_MAILBOX_REQUEST_BINDING')
    context = dict(domain='source-finalize-rpc-reply-v1', owner=mailbox_owner, mailbox=mailbox_id,
        operation=row['operation_key'], nonce=req['id'], requestSHA256=request_sha,
        deadline=row['rpc_deadline'], eventId=row['event_id'])
    result = verifier.verify(parse(row['rpc_response']), context)
    run = dict(threadId=binding['producer_thread_id'], runId=binding['producer_turn_id'],
               userId=result.get('admittedRun', {}).get('userId'))
    final = result.get('finalizerReceipt', {})
    if (result.get('admittedRun') != run or final.get('run') != run
            or final.get('taskBinding', {}).get('taskRef') != binding['source_key']
            or final.get('taskBinding', {}).get('operationKey') != binding['source_operation_key']
            or final.get('checkpointReceipt', {}).get('sourceRef') != binding['source_ref']
            or sha(req['params']['document']['continuation'].get('checkpoint')) != binding['checkpoint_sha256']):
        raise ValueError('SIGNED_RESULT_TASK_NATIVE_BINDING')
    if payload['status'] == 'COMPLETED':
        decision = 'verified_agent_reported_complete'
    elif payload['status'] == 'FAILED':
        decision = 'failed_requires_review'
    else:
        decision = 'verified_continue'
        if sha(payload['next_step']) != binding['next_step_sha256']:
            raise ValueError('LEGAL_NEXT_ACTION_REQUIRED')
    return req, result, payload, decision

def attestation(row, binding, snapshot, result, decision, effective=None):
    return dict(decision=decision, **dict.fromkeys(FLAGS, True), message_id=row['event_id'],
        request_sha256=row['rpc_request_sha256'], signed_result_sha256=sha(result),
        source_revision=snapshot['revision'], saved_source_revision=result['finalizerReceipt']['checkpointReceipt']['revision'],
        binding_sha256=sha(binding),
        result_scope={k:(effective or binding)[k] for k in SCOPE_KEYS},
        businessAcceptanceVerified=False, parentCloseAllowed=False)

class PublicOriginalRPCCompletionAdapter:
    """Observation MUST be the HostProofVerifier-verified observe evidence.

    The owning CycleController is responsible for fresh signature, binding,
    generation and native observation validation before this callback. Arbitrary
    user/MCP dictionaries must never be sent here as verified observations.
    """
    def __init__(self, *, read_original_message, public_key, mailbox_owner, mailbox_id, current):
        self.read_message, self.current = read_original_message, current
        self.verifier, self.owner, self.mailbox = ReplyVerifier(public_key), mailbox_owner, mailbox_id

    async def __call__(self, *, inbox, binding, generation, observation):
        try:
            if not self.current() or observation.get('native_status') not in ('completed', 'failed', 'interrupted'):
                return {'decision': 'unknown'}
            proof = copy.deepcopy(observation['original_rpc_completion'])
            effective = watched_binding(binding, observation)
            scope = proof.get('result_scope')
            if (type(scope) is not dict or set(scope) != set(SCOPE_KEYS)
                    or any(scope[k] != effective[k] for k in ('source_key','source_ref','producer_thread_id','producer_turn_id'))):
                raise ValueError('HOST_ATTESTED_RESULT_SCOPE_REQUIRED')
            effective.update(scope)
            if (any(proof.get(k) is not True for k in FLAGS)
                    or proof.get('businessAcceptanceVerified') is not False
                    or proof.get('parentCloseAllowed') is not False
                    or observation.get('native_thread_id') != effective['producer_thread_id']
                    or observation.get('native_turn_id') != effective['producer_turn_id']):
                raise ValueError('VERIFIED_HOST_COMPLETION_PROOF_REQUIRED')
            row = await self.read_message(inbox=inbox, binding=binding, generation=generation, observation=observation)
            req, result, payload, decision = read_pair(row, binding=effective,
                mailbox_owner=self.owner, mailbox_id=self.mailbox, verifier=self.verifier)
            snapshot = dict(revision=observation.get('source_revision'))
            expected = attestation(row, binding, snapshot, result, decision, effective)
            if proof != expected or proof['source_revision'] != observation.get('source_revision') or not self.current():
                raise ValueError('VERIFIED_HOST_COMPLETION_PROOF_CHANGED')
            return proof
        except (ValueError, KeyError, TypeError, OSError, InvalidSignature):
            return {'decision': 'unknown'}
