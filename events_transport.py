"""Add Events RPC behind the existing SDK OAuth and transport-security gates."""
import json
from types import MethodType

from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.middleware.bearer_auth import RequireAuthMiddleware
from mcp.server.transport_security import TransportSecurityMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from inbox import InboxError


class EventsEndpoint:
    def __init__(self, app, events, security):
        self.app, self.events = app, events
        self.security = TransportSecurityMiddleware(security)

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http' or scope['method'] != 'POST':
            return await self.app(scope, receive, send)
        request = Request(scope, receive)
        # Applies equally to event extensions and ordinary SDK requests.
        rejected = await self.security.validate_request(request, is_post=True)
        if rejected is not None:
            return await rejected(scope, receive, send)
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > 65536:
                return await JSONResponse({'error': 'request_too_large'}, status_code=413)(scope, receive, send)
        try:
            payload = json.loads(body)
        except (ValueError, UnicodeError):
            payload = None
        if isinstance(payload, dict) and str(payload.get('method', '')).startswith('events/'):
            request_id = payload.get('id')
            response = {'jsonrpc': '2.0', 'id': request_id}
            if (payload.get('jsonrpc') != '2.0' or type(request_id) not in (int, str)
                    or payload.get('method') not in ('events/list', 'events/subscribe', 'events/unsubscribe')):
                response['error'] = {'code': -32600, 'message': 'Invalid Events request'}
            else:
                try:
                    response['result'] = await self.events.rpc(payload['method'], payload.get('params', {}), get_access_token())
                except InboxError as error:
                    code = -32015 if str(error).startswith('callback_') or str(error) == 'non_public_callback' else -32602
                    response['error'] = {'code': code, 'message': 'Events request failed', 'data': {'reason': str(error)}}
                except Exception:
                    response['error'] = {'code': -32603, 'message': 'Events operation unconfirmed; query before retry'}
            return await JSONResponse(response)(scope, receive, send)
        sent = False

        async def replay():
            nonlocal sent
            if not sent:
                sent = True
                return {'type': 'http.request', 'body': bytes(body), 'more_body': False}
            return await receive()

        await self.app(scope, replay, send)


def install_events_transport(mcp, events):
    server = mcp._mcp_server
    original = server.get_capabilities

    def capabilities(self, notification_options, experimental_capabilities):
        value = original(notification_options, experimental_capabilities)
        value.model_extra['events'] = {}
        return value

    server.get_capabilities = MethodType(capabilities, server)
    asgi = mcp.streamable_http_app()
    for route in asgi.routes:
        if getattr(route, 'path', None) == mcp.settings.streamable_http_path:
            gate = route.app
            if not isinstance(gate, RequireAuthMiddleware):
                raise RuntimeError('EVENTS_REQUIRE_EXISTING_OAUTH_GATE')
            gate.app = EventsEndpoint(gate.app, events, mcp.settings.transport_security)
            break
    else:
        raise RuntimeError('MCP_ROUTE_MISSING')
    return asgi
