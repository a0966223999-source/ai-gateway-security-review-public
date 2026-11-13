"""Receiver adapter for original, host-resolved checkpoint references.

Never pass payload-supplied versions as host references. This adapter does not
create a latest registry, alter a Source, delete an event, dispatch or ACK.
Original Receiver/Source must supply both references under current authority.
"""
import copy


class CheckpointNoticeGate:
    def __init__(self, *, read_bound_references, current):
        if not callable(read_bound_references) or not callable(current):
            raise ValueError('ORIGINAL_SOURCE_PORTS_REQUIRED')
        self.read_bound_references = read_bound_references
        self.current = current

    async def inspect(self, event_id):
        if self.current() is not True:
            return {'status': 'REVOKED_OR_NOT_BOUND', 'execute': False}
        try:
            # Original host resolves exact event -> original task/source; no
            # mailbox-wide ordering, self-reported task mapping or new authority.
            raw = await self.read_bound_references(event_id)
            refs = copy.deepcopy(raw)
        except Exception:
            return {'status': 'UNKNOWN_QUERY_ONLY', 'execute': False}
        if self.current() is not True:
            return {'status': 'REVOKED_OR_NOT_BOUND', 'execute': False}
        if not isinstance(refs, dict) or set(refs) != {'event', 'current'}:
            return {'status': 'SOURCE_REFERENCE_UNCONFIRMED', 'execute': False}
        event, latest = refs['event'], refs['current']
        fields = {'event_id', 'owner', 'thread_id', 'task_id', 'source_key',
                  'checkpoint_revision', 'operation_key', 'checkpoint_digest'}
        if any(not isinstance(r, dict) or set(r) != fields or
               any(not isinstance(v, str) or not v for v in r.values())
               for r in (event, latest)) or event['event_id'] != event_id:
            return {'status': 'SOURCE_REFERENCE_UNCONFIRMED', 'execute': False}
        scope = ('owner', 'thread_id', 'task_id', 'source_key')
        if any(event[k] != latest[k] for k in scope):
            return {'status': 'DIFFERENT_TASK_OR_SOURCE', 'execute': False}
        # Revisions are opaque Source/CAS references. Never sort them or let an
        # arbitrary larger payload version make an old notice current.
        if event['checkpoint_revision'] != latest['checkpoint_revision']:
            return {'status': 'SUPERSEDED_NO_EXECUTION', 'execute': False,
                    'event_id': event_id, 'current_event_id': latest['event_id'],
                    'delete_event': False, 'transport_ack': 'NOT_RUN'}
        exact = ('event_id', 'operation_key', 'checkpoint_digest')
        if any(event[k] != latest[k] for k in exact):
            return {'status': 'SAME_REVISION_CONFLICT_QUERY_ONLY', 'execute': False}
        return {'status': 'CURRENT_CHECKPOINT', 'execute': False,
                'eligible_for_original_receiver_revalidation': True,
                'event_id': event_id, 'checkpoint_revision': latest['checkpoint_revision']}
