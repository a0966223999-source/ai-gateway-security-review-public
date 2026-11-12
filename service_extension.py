"""Opt-in hook on the ORIGINAL FastMCP, Redis settings and Events instance.

This is code, not setup: importing it performs no connection, subscription,
notification, credential read or environment change. The original installer
must pass explicit host configuration and the original report adapter.
"""
import asyncio
from contextlib import asynccontextmanager
import os
import re
from urllib.parse import urlsplit

from cycle_transport import install_cycle_transport
from protocol import HostProofVerifier, MAILBOX


def original_redis_connection():
    """Same settings/TLS gate as original Inbox.run; no alternate destination.

    Separate short-lived connection to the SAME configured Redis and original
    owner-derived mailbox key. Zero automatic command retries. Never logs URLs.
    """
    from redis import Redis
    from redis.retry import Retry
    from redis.backoff import NoBackoff
    if os.getenv('VERCEL_ENV') != 'preview':
        raise ValueError('preview_only')
    url = os.getenv('XJ_PREVIEW_REDIS_URL') or os.getenv('REDIS_URL', '')
    endpoint = urlsplit(url)
    if endpoint.scheme != 'rediss' or not endpoint.hostname or endpoint.query or endpoint.fragment:
        raise ValueError('redis_configuration_unavailable')
    return Redis.from_url(url, decode_responses=True, ssl_cert_reqs='required',
        ssl_check_hostname=True, socket_connect_timeout=3, socket_timeout=3,
        retry=Retry(NoBackoff(), 0), retry_on_error=[])


def make_original_rpc_completion_adapter(*, public_key, principal, key_current):
    """Read one original row after CycleController verifies the observe proof.

    The dependency is the public-only terminal verifier. No private host import,
    new report, caller-selected Redis key or signed SourceRPC retry is allowed.
    """
    from public_completion_adapter import PublicOriginalRPCCompletionAdapter
    from cycle_store import CycleStore
    from protocol import HEX

    async def verify(*, inbox, binding, generation, observation):
        owner = inbox.owner
        def current():
            try:
                return (inbox.mailbox_id == MAILBOX and principal() == owner and key_current() is True)
            except Exception:
                return False
        async def read_message(**args):
            if not current():
                raise ValueError('ORIGINAL_REPORT_AUTHORITY_CHANGED')
            proof = observation.get('original_rpc_completion', {})
            event = proof.get('message_id')
            if not isinstance(event, str) or not HEX.fullmatch(event):
                raise ValueError('ORIGINAL_RPC_REPORT_LOCATOR_REQUIRED')
            row = await asyncio.to_thread(CycleStore(inbox).original, event)
            if not current():
                raise ValueError('ORIGINAL_REPORT_AUTHORITY_CHANGED')
            return row
        adapter = PublicOriginalRPCCompletionAdapter(read_original_message=read_message,
            public_key=public_key, mailbox_owner=owner, mailbox_id=MAILBOX, current=current)
        return await adapter(inbox=inbox, binding=binding, generation=generation, observation=observation)
    return verify


def install_generation_tools(mcp, mailbox, formal, principal, oauth_meta, *,
                             generation_config=None, completed_report_adapter=None,
                             connection_factory=original_redis_connection):
    """No caller-supplied config. A missing fixed-host setup disables all tools."""
    if not isinstance(generation_config, dict) or generation_config.get('enabled') is not True:
        return {'enabled': False, 'tools': [], 'reason': 'GENERATION_HOST_NOT_ENABLED'}
    if set(generation_config) != {'enabled', 'policy', 'subscription_id'}:
        raise ValueError('EXACT_GENERATION_HOST_CONFIG_REQUIRED')
    public = os.getenv('XJ_SOURCE_RPC_HOST_PUBLIC_KEY', '')
    if not re.fullmatch('[0-9a-f]{64}', public):
        return {'enabled': False, 'tools': [], 'reason': 'PINNED_HOST_KEY_REQUIRED'}
    verifier = HostProofVerifier(bytes.fromhex(public))
    if completed_report_adapter is None:
        try:
            completed_report_adapter = make_original_rpc_completion_adapter(
                public_key=bytes.fromhex(public), principal=principal,
                key_current=lambda: os.getenv('XJ_SOURCE_RPC_HOST_PUBLIC_KEY') == public)
        except ImportError:
            return {'enabled': False, 'tools': [], 'reason': 'ORIGINAL_PUBLIC_COMPLETION_ADAPTER_REQUIRED'}

    @asynccontextmanager
    async def session(mailbox_id):
        if mailbox_id != MAILBOX:
            raise ValueError('ORIGINAL_MAILBOX_REQUIRED')
        owner = principal()
        original = mailbox(MAILBOX).for_event('gateway.continuation.ready')
        if original.owner != owner or original.mailbox_id != MAILBOX:
            raise ValueError('ORIGINAL_OWNER_REQUIRED')
        if os.getenv('XJ_SOURCE_RPC_HOST_PUBLIC_KEY') != public:
            raise ValueError('FIXED_HOST_KEY_CHANGED')
        # Reuse an already-owned original client when available. Do not close it.
        client = original.client
        owns_client = client is None
        if owns_client:
            client = connection_factory()
        original.client = client
        try:
            yield original
        finally:
            if owns_client:
                await asyncio.to_thread(client.close)

    return install_cycle_transport(mcp, original_session=session, formal=formal,
        proof_verifier=verifier, principal=principal, oauth_meta=oauth_meta,
        policy=generation_config['policy'], subscription_id=generation_config['subscription_id'],
        completed_report_adapter=completed_report_adapter, enabled=True)
