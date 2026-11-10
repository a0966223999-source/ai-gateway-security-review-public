"""Allowlisted subscription stage logs. Never serialize request data or exceptions."""
import asyncio
import contextvars
import hashlib
import json
import logging
import secrets
import time

CURRENT = contextvars.ContextVar('subscription_diagnostic', default=None)
STAGES = frozenset('accepted validation authority_passed identity_bound encryption_started encryption_ready lookup_started lookup_returned lookup_complete callback_started callback_response callback_verified save_started save_confirmed cleanup_started cleanup_confirmed response_ready'.split())
ERRORS = frozenset('invalid_params unknown_event invalid_arguments invalid_identifier formal_authority_required unsupported_delivery invalid_callback invalid_signing_secret replay_unsupported invalid_ttl access_expired subscription_encryption_unavailable callback_challenge_failed callback_verification_failed callback_response_too_large non_public_callback subscription_changed subscription_limit redis_configuration_unavailable preview_only outcome_unknown_query_same_operation operation_cancelled subscription_lookup_invalid_container subscription_lookup_missing_field subscription_lookup_invalid_record'.split())
UNEXPECTED_ERRORS = {KeyError: 'unexpected_key_error', TypeError: 'unexpected_type_error',
                     AttributeError: 'unexpected_attribute_error', RuntimeError: 'unexpected_runtime_error',
                     AssertionError: 'unexpected_assertion_error'}
ERRORS = ERRORS | frozenset(UNEXPECTED_ERRORS.values())
LOG = logging.getLogger('gateway.subscription.stages')
LOG.setLevel(logging.INFO)

def emit(stage, *, outcome='progress', error=None, http_status=None, identity=None):
    try:
        ctx = CURRENT.get()
        if ctx is None or stage not in STAGES or outcome not in ('progress','success','failed_unknown'):
            return
        ctx['stage'] = stage
        row = {'kind':'subscription_stage_v1','correlation':ctx['correlation'],
               'method':ctx['method'],'event':ctx['event'],'stage':stage,'outcome':outcome,
               'elapsed_ms':int((time.monotonic()-ctx['start'])*1000)}
        if identity is not None:
            ctx['identity'] = hashlib.sha256(identity.encode()).hexdigest()
        if 'identity' in ctx: row['subscription_fingerprint'] = ctx['identity']
        if error is not None: row['error'] = error if error in ERRORS else 'unclassified'
        if type(http_status) is int and 100 <= http_status <= 599: row['http_status'] = http_status
        LOG.info(json.dumps(row,sort_keys=True,separators=(',',':')))
    except Exception:
        pass  # All observer work is best-effort, including clock/hash/serialization.

async def trace_rpc(method, event, call):
    if CURRENT.get() is not None or method not in ('events/subscribe','events/unsubscribe'):
        return await call()
    try:
        ctx={'correlation':secrets.token_hex(12),'method':method,
             'event':event if event in ('inbox.message.created','gateway.continuation.ready') else 'unknown',
             'start':time.monotonic(),'stage':'accepted'}
        marker=CURRENT.set(ctx)
    except Exception:
        return await call()  # Observer setup failure never prevents business execution.
    try:
        emit('accepted')
        try:
            result=await call()
        except BaseException as exc:
            try:
                from inbox import InboxError
                reason = ('operation_cancelled' if isinstance(exc, asyncio.CancelledError) else
                          str(exc) if type(exc) is InboxError else
                          UNEXPECTED_ERRORS.get(type(exc), 'unclassified'))
                emit(ctx['stage'],outcome='failed_unknown',error=reason)
            except Exception:
                pass  # Preserve the original business exception.
            raise
        emit('response_ready',outcome='success')
        return result
    finally:
        try:
            CURRENT.reset(marker)
        except Exception:
            pass  # Observer teardown must not turn committed success into failure.
