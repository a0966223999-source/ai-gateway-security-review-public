"""Host-only combined catalog; does not mount or enable a production endpoint."""
from inbox import InboxError
from mcp_events import EVENT_NAME

class EventCatalog:
    def __init__(self, synthetic, formal):
        if synthetic.event_name != EVENT_NAME or formal.event_name != 'gateway.continuation.ready':
            raise InboxError('invalid_event_catalog')
        self.handlers = {h.event_name:h for h in (synthetic, formal)}

    async def rpc(self, method, params, token):
        if not isinstance(params, dict):
            raise InboxError('invalid_params')
        if method == 'events/list':
            events = []
            for handler in self.handlers.values():
                try:
                    events.extend((await handler.rpc(method, params, token))['events'])
                except InboxError as error:
                    if str(error) != 'formal_authority_required':
                        raise
            return {'events':events}
        handler = self.handlers.get(params.get('name'))
        if handler is None:
            raise InboxError('unknown_event')
        return await handler.rpc(method, params, token)
