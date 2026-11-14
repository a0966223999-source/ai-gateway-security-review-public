"""Same-owner formal notification transport, not native Work authority.

No Source, holder, native caller, new OAuth scope, credential, or public route.
Only the original authorized mailbox is exposed. Receiver still checks original
Source and current task authority before any native Work action.
"""
import asyncio, hashlib, os
from mcp.types import ToolAnnotations
from mcp.server.fastmcp import Context
from inbox import InboxError
from mcp_events import Events

FORMAL = 'gateway.continuation.ready'
MAILBOX = 'gateway_internal_v1'


def caller_context_observation(ctx, owner):
    """Read only host MCP metadata, never payload IDs or authentication claims."""
    result = {'schema': 'caller-context-observation-v1',
              'work_identity_verified': False,
              'evidence': 'NO_PLATFORM_ORIGIN_ATTESTATION',
              'conversation_id_present': False}
    try:
        meta = ctx.request_context.meta if ctx is not None else None
        extra = meta if isinstance(meta, dict) else getattr(meta, 'model_extra', None)
        session = extra.get('openai/session') if isinstance(extra, dict) else None
        if isinstance(session, str) and 0 < len(session) <= 512:
            # Opaque owner-scoped correlation only. Never expose raw metadata,
            # tokens, thread/session IDs, callback URLs or request headers.
            result['conversation_id_present'] = True
            result['conversation_fingerprint'] = hashlib.sha256(
                ('caller-context-v1|' + owner + '|' + session).encode()).hexdigest()
            result['evidence'] = 'CONVERSATION_METADATA_ONLY_NOT_WORK_IDENTITY'
    except Exception:
        result['evidence'] = 'CONTEXT_UNAVAILABLE'
    return result

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
    async def gateway_inbox_subscription_status(ctx: Context = None) -> dict:
        """Read retained subscription metadata for the fixed original owner/mailbox. Includes retained expired entries, not deleted history; absence does not resolve an unknown create. No callbacks or credentials are returned."""
        inbox = box(MAILBOX)
        if transport_authority('subscription_status', inbox, None) is not True:
            raise InboxError('formal_authority_required')
        result = await asyncio.to_thread(inbox.subscription_status)
        result['caller_context'] = caller_context_observation(ctx, inbox.owner)
        return result


    @mcp.tool(meta=oauth_meta, annotations=annotation(idempotent=True, external=True))
    async def gateway_inbox_resume_delivery(mailbox_id: str, event_id: str,
                                            subscription_id: str) -> dict:
        """Resume ONE original, never-attempted pending Gateway callback.
        No new event, replay of uncertain attempts, claim, ACK or Source grant.
        Query delivery status first; callback 2xx is not Work completion.
        """
        import re
        if (not re.fullmatch(r'[0-9a-f]{64}', event_id)
                or not re.fullmatch(r'sub_[0-9a-f]{64}', subscription_id)):
            raise InboxError('invalid_delivery_locator')
        inbox = box(mailbox_id)
        if transport_authority('resume_delivery', inbox, event_id) is not True:
            raise InboxError('formal_authority_required')
        delivery_id = event_id + ':' + subscription_id
        snapshot = await asyncio.to_thread(inbox.delivery_status, delivery_id)
        rows = snapshot.get('items')
        if (not isinstance(rows, list) or len(rows) != 1
                or rows[0].get('event_id') != event_id
                or rows[0].get('delivery_id') != delivery_id
                or rows[0].get('event_name') != FORMAL):
            raise InboxError('original_delivery_not_found_or_mismatched')
        row = rows[0]
        if row.get('state') == 'delivered':
            return {'status': 'ALREADY_DELIVERED', 'delivery': row,
                    'new_event': False, 'work_completion': 'UNVERIFIED'}
        if row.get('state') != 'pending' or type(row.get('attempts')) is not int or row['attempts'] != 0:
            return {'status': 'QUERY_ONLY', 'delivery': row,
                    'new_event': False, 'work_completion': 'UNVERIFIED'}
        # Events.deliver owns the original atomic delivery lock and rechecks
        # stored OAuth/access/revision immediately before callback attempts.
        if transport_authority('resume_delivery', inbox, event_id) is not True:
            raise InboxError('formal_authority_required')
        try:
            outcome = await formal.deliver(inbox, delivery_id)
        except Exception:
            return {'status': 'UNKNOWN_QUERY_ONLY', 'delivery_id': delivery_id,
                    'new_event': False, 'work_completion': 'UNVERIFIED'}
        return {'status': 'DELIVERY_ATTEMPT_FINISHED', 'delivery': outcome,
                'new_event': False, 'work_completion': 'UNVERIFIED'}

    return formal
