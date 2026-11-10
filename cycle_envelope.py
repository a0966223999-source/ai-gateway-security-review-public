"""Explicit v2 control envelope. Legacy equality guard is not changed."""
from datetime import datetime, timezone

from protocol import FORMAL, MAILBOX, digest, notification_id


def make_envelope(*, owner, event_id, generation, created_at, binding):
    notice = notification_id(owner, MAILBOX, event_id, generation)
    return {'eventId': notice, 'name': FORMAL,
            'timestamp': datetime.fromtimestamp(created_at, timezone.utc).isoformat().replace('+00:00', 'Z'),
            'cursor': None, 'data': {
                'schema': 'gateway-control-notification-v2', 'notification_id': notice,
                'logical_event_id': event_id, 'mailbox_id': MAILBOX,
                'generation': generation, 'binding_sha256': digest(binding),
                'request': 'recheck_bound_producer', 'synthetic': False}}


def verify_received_envelope(envelope, *, owner, binding, expected_generation):
    """Caller must supply verified platform envelope, not untrusted embedded text.

    Returns distinct transport/logical IDs. It does not confer Source authority.
    """
    required = {'eventId', 'name', 'timestamp', 'data'}
    if not isinstance(envelope, dict) or set(envelope) not in (required, required | {'cursor'}):
        raise ValueError('CONTROL_ENVELOPE_SCHEMA')
    data = envelope['data']
    keys = {'schema', 'notification_id', 'logical_event_id', 'mailbox_id',
            'generation', 'binding_sha256', 'request', 'synthetic'}
    if not isinstance(data, dict) or set(data) != keys:
        raise ValueError('CONTROL_DATA_SCHEMA')
    logical = binding['logical_event_id']
    expected = notification_id(owner, MAILBOX, logical, expected_generation)
    if (envelope['name'] != FORMAL or envelope['eventId'] != expected or
            data['notification_id'] != expected or data['logical_event_id'] != logical or
            data['schema'] != 'gateway-control-notification-v2' or data['mailbox_id'] != MAILBOX or
            type(data['generation']) is not int or data['generation'] != expected_generation or data['binding_sha256'] != digest(binding) or
            data['request'] != 'recheck_bound_producer' or data['synthetic'] is not False or
            envelope.get('cursor') is not None or not isinstance(envelope['timestamp'], str)):
        raise ValueError('CONTROL_ENVELOPE_BINDING')
    return {'notification_id': expected, 'logical_event_id': logical,
            'generation': expected_generation, 'authority': False}


def augment_descriptor(original):
    """Opt-in discovery schema union, never mutates legacy descriptor in place."""
    import copy
    out = copy.deepcopy(original)
    if out.get('name') != FORMAL:
        raise ValueError('FORMAL_DESCRIPTOR_REQUIRED')
    legacy = out['payloadSchema']
    out['payloadSchema'] = {'oneOf': [legacy, {
        'type': 'object', 'additionalProperties': False,
        'required': ['schema', 'notification_id', 'logical_event_id', 'mailbox_id',
                     'generation', 'binding_sha256', 'request', 'synthetic'],
        'properties': {
            'schema': {'const': 'gateway-control-notification-v2'},
            'notification_id': {'type': 'string', 'pattern': '^[0-9a-f]{64}$'},
            'logical_event_id': {'type': 'string', 'pattern': '^[0-9a-f]{64}$'},
            'mailbox_id': {'const': MAILBOX}, 'generation': {'type': 'integer', 'minimum': 1},
            'binding_sha256': {'type': 'string', 'pattern': '^[0-9a-f]{64}$'},
            'request': {'const': 'recheck_bound_producer'}, 'synthetic': {'const': False}}}]}
    return out


class CycleAwareEventCatalog:
    """Opt-in discovery wrapper. Subscribe/unsubscribe behavior is unchanged."""

    def __init__(self, original):
        self.original = original

    async def rpc(self, method, params, token):
        result = await self.original.rpc(method, params, token)
        if method != 'events/list':
            return result
        import copy
        result = copy.deepcopy(result)
        result['events'] = [augment_descriptor(event) if event.get('name') == FORMAL else event
                            for event in result['events']]
        return result
