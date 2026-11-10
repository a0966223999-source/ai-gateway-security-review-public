"""Original-key atomic cycle extension, no new Redis connection or credentials."""
import json
from pathlib import Path

from protocol import canonical

SCRIPT = Path(__file__).with_name('cycle.lua').read_text()


class CycleUnknownOutcome(Exception):
    """Never repeat a mutation; query the same existing event/generation."""


class CycleStore:
    def __init__(self, inbox):
        if inbox.client is None:
            raise ValueError('ORIGINAL_INJECTED_CLIENT_REQUIRED')
        self.client = inbox.client
        self.key = inbox.key

    def transition(self, action, event_id, **args):
        request = dict(action=action, event_id=event_id, **args)
        try:
            raw = self.client.eval(SCRIPT, 1, self.key, canonical(request))
            result = json.loads(raw)
        except Exception as error:
            raise CycleUnknownOutcome('CYCLE_OUTCOME_UNKNOWN_QUERY_SAME_EVENT') from error
        if not isinstance(result, dict):
            raise CycleUnknownOutcome('CYCLE_REPLY_UNKNOWN_QUERY_SAME_EVENT')
        if 'error' in result:
            raise ValueError(result['error'])
        return result

    def original(self, event_id):
        """Read only. No inference that a mailbox message grants Source authority."""
        raw = self.client.get(self.key)
        document = json.loads(raw or '{}')
        return document.get('messages', {}).get(event_id)
