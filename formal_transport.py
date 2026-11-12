"""Same-owner formal notification transport, not native Work authority.

No Source, holder, native caller, new OAuth scope, credential, or public route.
Only the original authorized mailbox is exposed. Receiver still checks original
Source and current task authority before any native Work action.
"""
import asyncio, hashlib, os
from mcp.types import ToolAnnotations
from inbox import InboxError
from mcp_events import Events

FORMAL = 'gateway.continuation.ready'
MAILBOX = 'gateway_internal_v1'

def install_formal_transport(mcp, mailbox, verifier, principal, oauth_meta):
    def box(mailbox_id):
        if mailbox_id != MAILBOX:
            raise InboxError('formal_mailbox_not_allowed')
        return mailbox(mailbox_id).for_event(FORMAL)

    def transport_authority(action, inbox, event_id):
        # OAuth is required by the original endpoint. Events.deliver also
        # verifies the stored proof before each delivery invocation.
        if inbox is None:
            principal()  # actual authenticated request; no constant grant
            return True
        subject = os.getenv('XJ_AUTH0_ALLOWED_SUBJECT', '')
        if not subject or os.getenv('VERCEL_ENV') != 'preview':
            return False
        owner = 'oauth:' + hashlib.sha256((verifier.issuer+'|'+subject).encode()).hexdigest()
        return inbox.owner == owner and inbox.mailbox_id == MAILBOX

    formal = Events(box, verifier, event_name=FORMAL, authorize_event=transport_authority)
    def annotation(read=False, idempotent=False, external=False):
        return ToolAnnotations(readOnlyHint=read, destructiveHint=False,
                               idempotentHint=idempotent, openWorldHint=external)

    @mcp.tool(meta=oauth_meta, annotations=annotation(idempotent=True, external=True))
    async def gateway_inbox_send(mailbox_id: str, operation_key: str, message: str) -> dict:
        """Send an owner-scoped Gateway notification. May trigger the subscribed dot. It grants no Work authority; receiver must check original Source. Query same event on uncertainty."""
        inbox=box(mailbox_id)
        receipt=await asyncio.to_thread(inbox.send, operation_key, message, event_name=FORMAL)
        try:
            receipt['deliveries']=await formal.dispatch(inbox,receipt['event_id'])
        except Exception:
            receipt['delivery_status']='unconfirmed_query_delivery_status'
        return {**receipt,'mailbox_id':mailbox_id,'synthetic':False}

    @mcp.tool(meta=oauth_meta, annotations=annotation(read=True,idempotent=True))
    async def gateway_inbox_list(mailbox_id: str, cursor: str = '', limit: int = 20) -> dict:
        """Read this owner's formal Gateway notifications only. Does not claim, acknowledge or grant native Work access."""
        return await asyncio.to_thread(box(mailbox_id).list,cursor,limit)

    @mcp.tool(meta=oauth_meta, annotations=annotation())
    async def gateway_inbox_claim(mailbox_id: str,event_id: str) -> dict:
        """Lease one formal notification for 120s. Lease is transport ownership, not Source authority or person ACK. Keep token private."""
        return await asyncio.to_thread(box(mailbox_id).claim,event_id)

    @mcp.tool(meta=oauth_meta, annotations=annotation(idempotent=True))
    async def gateway_inbox_ack(mailbox_id: str,event_id: str,claim_token: str,version: int) -> dict:
        """Complete a formal transport message using its current lease. Not person ACK or project acceptance; receiver must first verify and save the result."""
        return await asyncio.to_thread(box(mailbox_id).settle,'ack',event_id,claim_token,version)

    @mcp.tool(meta=oauth_meta, annotations=annotation(idempotent=True))
    async def gateway_inbox_release(mailbox_id: str,event_id: str,claim_token: str,version: int) -> dict:
        """Release a formal transport lease without completing work; may permit another consumer to process it."""
        return await asyncio.to_thread(box(mailbox_id).settle,'release',event_id,claim_token,version)

    @mcp.tool(meta=oauth_meta, annotations=annotation(read=True,idempotent=True))
    async def gateway_inbox_delivery_status(mailbox_id: str,delivery_id: str = '') -> dict:
        """Read formal callback delivery receipts. Does not expose secrets; 2xx is delivery, not native Work completion."""
        return await asyncio.to_thread(box(mailbox_id).delivery_status,delivery_id)
    @mcp.tool(meta=oauth_meta, annotations=annotation(read=True,idempotent=True))
    async def gateway_inbox_subscription_status() -> dict:
        """Read retained subscription metadata for the fixed original owner/mailbox. Includes retained expired entries, not deleted history; absence does not resolve an unknown create. No callbacks or credentials are returned."""
        inbox = box(MAILBOX)
        if transport_authority('subscription_status', inbox, None) is not True:
            raise InboxError('formal_authority_required')
        return await asyncio.to_thread(inbox.subscription_status)

    return formal
