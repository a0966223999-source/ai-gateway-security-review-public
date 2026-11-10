"""Unmodified original R1.2 agent-result parser (source lines20-50).
Source SHA256:f9a6331e788482b0e2c7a0307449c824348b7ad795291195a895430346f9748a.
No report payload or storage schema is added.
"""
import json
from typing import Any

MAX_AGENT_RESULT_CHARS = 16_384
_AGENT_RESULT_FIELDS = frozenset({"status", "next_step", "context_data", "final_output"})


def parse_agent_result(raw: str) -> dict[str, Any]:
    """Validate the opted-in turn result without accepting identity or grants."""
    def unique_pairs(pairs):
        result = dict(pairs)
        if len(result) != len(pairs):
            raise ValueError("DUPLICATE_AGENT_RESULT_KEY")
        return result

    if not isinstance(raw, str) or len(raw) > MAX_AGENT_RESULT_CHARS:
        raise ValueError("INVALID_AGENT_RESULT_SIZE")
    value = json.loads(raw, object_pairs_hook=unique_pairs)
    if not isinstance(value, dict) or value.keys() - _AGENT_RESULT_FIELDS:
        raise ValueError("INVALID_AGENT_RESULT_FIELDS")
    status = value.get("status")
    if status not in ("IN_PROGRESS", "COMPLETED", "FAILED"):
        raise ValueError("INVALID_AGENT_RESULT_STATUS")
    next_step = value.get("next_step")
    context = value.get("context_data", {})
    final_output = value.get("final_output")
    if (next_step is not None and not isinstance(next_step, str)
            or not isinstance(context, dict)
            or final_output is not None and not isinstance(final_output, str)
            or status == "IN_PROGRESS" and (not next_step or not next_step.strip())):
        raise ValueError("INVALID_AGENT_RESULT_CONTENT")
    # Reject non-finite numbers even inside nested model context.
    json.dumps(value, allow_nan=False, ensure_ascii=False).encode("utf-8")
    return {"status": status, "next_step": next_step, "context_data": context, "final_output": final_output}
