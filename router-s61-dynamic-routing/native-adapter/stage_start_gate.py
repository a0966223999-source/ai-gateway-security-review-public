"""Stage a pinned original executor delta; never edit the live module."""
import ast
import hashlib
from pathlib import Path

BASE_SHA = 'c642970debbd44275eaec251f1aee40cdbcc140bffa1accb6718b0d49c1a458c'
GATE = '''    if native_start_gate is not None:
        # Internal held callback only. Public Execute schema is unchanged.
        # The original SQL reservation is durable before this await.
        gate_context = dict(batch_id=request.batch_id, item_id=request.item_id,
            binding=json.loads(canonical(binding)), intent_id=intent,
            prompt_sha256=sha(prompt.encode()),
            dependency_input_sha256=sha(canonical(dependency_data)))
        accepted = await asyncio.wait_for(native_start_gate(gate_context),
            timeout=request.timeout_seconds)
        if accepted is not True:
            raise HostDenied('S61_START_REFUSED_QUERY_ONLY')
'''


def stage(source, destination):
    raw = Path(source).read_bytes()
    if hashlib.sha256(raw).hexdigest() != BASE_SHA:
        raise ValueError('ORIGINAL_EXECUTOR_VERSION_CHANGED')
    text = raw.decode()
    signature = '*, fixed_agent_lease=None):'
    if text.count(signature) != 2:
        raise ValueError('ORIGINAL_SIGNATURE_CHANGED')
    text = text.replace(signature, '*, fixed_agent_lease=None, native_start_gate=None):')
    call = 'request, fixed_agent_lease=fixed_agent_lease)'
    if text.count(call) != 1:
        raise ValueError('ORIGINAL_CALL_CHANGED')
    text = text.replace(call, 'request, fixed_agent_lease=fixed_agent_lease, native_start_gate=native_start_gate)')
    anchor = "    host.check('execute')\n    await check_runtime()\n    cmd = "
    if text.count(anchor) != 1:
        raise ValueError('ORIGINAL_START_BOUNDARY_CHANGED')
    text = text.replace(anchor, GATE + anchor)
    ast.parse(text)
    target = Path(destination)
    with target.open('x') as stream:
        stream.write(text)
    return hashlib.sha256(text.encode()).hexdigest()


if __name__ == '__main__':
    import sys
    print(stage(*sys.argv[1:]))
