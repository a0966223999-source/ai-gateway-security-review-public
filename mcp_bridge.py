"""MCP on existing FastAPI; shares Redis backend, starts no extra service."""
from contextlib import asynccontextmanager
import asyncio
import hashlib
import os
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.settings import AuthSettings
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from oauth_verifier import Auth0Verifier
from inbox import Inbox, InboxError
from mcp_events import Events
from events_transport import install_events_transport
from event_catalog import EventCatalog
from formal_transport import install_formal_transport
from mcp_installer import install_rpc_tools
from rpc_diagnostics import install_rpc_diagnostic
from rpc_host_config import host_rpc_config


def install(api_app, write_event, read_event, event_model):
    verifier = Auth0Verifier()
    hostname = os.environ['VERCEL_URL']
    if not hostname.endswith('.vercel.app') or '/' in hostname:
        raise ValueError('INVALID_RESOURCE_HOST')
    public_hostname = os.getenv('XJ_MCP_PUBLIC_HOST', hostname)
    if not public_hostname.endswith('.vercel.app') or '/' in public_hostname:
        raise ValueError('INVALID_PUBLIC_RESOURCE_HOST')
    resource = 'https://' + public_hostname + '/mcp'
    @api_app.get('/.well-known/oauth-protected-resource', include_in_schema=False)
    async def resource_metadata_root():
        return {'resource': resource, 'authorization_servers': [verifier.issuer],
                'scopes_supported': ['synthetic:events'], 'bearer_methods_supported': ['header']}

    mcp = FastMCP('XJ Synthetic Redis', stateless_http=True, json_response=True,
        token_verifier=verifier,
        auth=AuthSettings(issuer_url=verifier.issuer, resource_server_url=resource,
                          required_scopes=['synthetic:events']),
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=True,
            allowed_hosts=list({hostname, public_hostname}),
            allowed_origins=list({'https://'+hostname,'https://'+public_hostname,'https://chatgpt.com'})))

    def principal():
        token = get_access_token()
        if token is None or token.subject != os.getenv('XJ_AUTH0_ALLOWED_SUBJECT'):
            raise PermissionError('AUTH_REQUIRED')
        return 'oauth:' + hashlib.sha256((verifier.issuer+'|'+token.subject).encode()).hexdigest()

    @mcp.tool(meta={'securitySchemes':[{'type':'oauth2','scopes':['synthetic:events']}]},
              annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False,
                                          idempotentHint=True, openWorldHint=False))
    def redis_test_write(operation_key: str, message: str) -> dict:
        """Write a synthetic event with TTL60. Query same event on uncertain outcome; no blind retry."""
        return write_event(event_model(operation_key=operation_key,message=message),principal())

    @mcp.tool(meta={'securitySchemes':[{'type':'oauth2','scopes':['synthetic:events']}]},
              annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False,
                                          idempotentHint=True, openWorldHint=False))
    def redis_test_read(event_id: str) -> dict:
        """Read the authorized owner's synthetic event and remaining TTL without extending expiry."""
        return read_event(event_id,principal())

    def mailbox(mailbox_id):
        return Inbox(principal(), mailbox_id)

    events = Events(mailbox, verifier)
    oauth_meta = {'securitySchemes': [{'type': 'oauth2', 'scopes': ['synthetic:events']}]}

    @mcp.tool(meta=oauth_meta, annotations=ToolAnnotations(
        readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=True))
    async def redis_inbox_send(mailbox_id: str, operation_key: str, message: str) -> dict:
        """Send a synthetic message, retained 24h. Same key/content deduplicates. Check delivery status on uncertainty."""
        inbox = mailbox(mailbox_id)
        receipt = await asyncio.to_thread(inbox.send, operation_key, message)
        try:
            receipt['deliveries'] = await events.dispatch(inbox, receipt['event_id'])
        except Exception:
            receipt['delivery_status'] = 'unconfirmed_query_delivery_status'
        return {**receipt, 'mailbox_id': mailbox_id, 'synthetic': True}

    @mcp.tool(meta=oauth_meta, annotations=ToolAnnotations(
        readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False))
    async def redis_inbox_list(mailbox_id: str, cursor: str = '', limit: int = 20) -> dict:
        """List this owner's synthetic mailbox in sequence order, including message states. Cursor does not acknowledge."""
        return await asyncio.to_thread(mailbox(mailbox_id).list, cursor, limit)

    @mcp.tool(meta=oauth_meta, annotations=ToolAnnotations(
        readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False))
    async def redis_inbox_claim(mailbox_id: str, event_id: str) -> dict:
        """Claim one message for 120s. Keep claim_token private. A lease is not native Work identity or formal task authority."""
        return await asyncio.to_thread(mailbox(mailbox_id).claim, event_id)

    @mcp.tool(meta=oauth_meta, annotations=ToolAnnotations(
        readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False))
    async def redis_inbox_ack(mailbox_id: str, event_id: str, claim_token: str, version: int) -> dict:
        """Mark a synthetic message completed using the current lease token and version; does not acknowledge a Gateway queue."""
        return await asyncio.to_thread(mailbox(mailbox_id).settle, 'ack', event_id, claim_token, version)

    @mcp.tool(meta=oauth_meta, annotations=ToolAnnotations(
        readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False))
    async def redis_inbox_release(mailbox_id: str, event_id: str, claim_token: str, version: int) -> dict:
        """Release a current synthetic lease without completing the message. It can then be claimed again."""
        return await asyncio.to_thread(mailbox(mailbox_id).settle, 'release', event_id, claim_token, version)

    @mcp.tool(meta=oauth_meta, annotations=ToolAnnotations(
        readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False))
    async def redis_inbox_delivery_status(mailbox_id: str, delivery_id: str = '') -> dict:
        """Read synthetic webhook receipts without exposing callback addresses, credentials or signing secrets. 2xx is not Work completion."""
        return await asyncio.to_thread(mailbox(mailbox_id).delivery_status, delivery_id)

    @mcp.tool(meta=oauth_meta, annotations=ToolAnnotations(
        readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=True))
    async def redis_inbox_retry_delivery(mailbox_id: str, delivery_id: str) -> dict:
        """Retry one pending/uncertain synthetic delivery under the SAME event ID. Query status first; no persistent background worker."""
        inbox = mailbox(mailbox_id)
        status = await asyncio.to_thread(inbox.delivery_status, delivery_id)
        if not delivery_id or not status['items']:
            raise InboxError('delivery_missing')
        return await events.deliver(inbox, delivery_id)

    previous = api_app.router.lifespan_context
    @asynccontextmanager
    async def lifespan(app):
        async with previous(app):
            async with mcp.session_manager.run():
                yield
    api_app.router.lifespan_context = lifespan
    formal = install_formal_transport(mcp, mailbox, verifier, principal, oauth_meta)
    rpc_config = host_rpc_config()
    install_rpc_tools(mcp, mailbox, formal, principal, oauth_meta, host_config=rpc_config)
    install_rpc_diagnostic(mcp, principal, oauth_meta, host_config=rpc_config)
    api_app.mount('/', install_events_transport(mcp, EventCatalog(events, formal)))
    return mcp
