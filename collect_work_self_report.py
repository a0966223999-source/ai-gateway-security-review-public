"""Read only two explicit runtime hints; never select a route or create a grant."""
import json
import os


def collect_runtime_identifiers(environment):
    """Return observations, not an official reference or invocation proof."""
    observations = {}
    for key in ('CODEX_THREAD_ID', 'CODEX_ENVIRONMENT_ID'):
        value = environment.get(key)
        if value is not None:
            observations[key] = value
    return observations


if __name__ == '__main__':
    print(json.dumps(collect_runtime_identifiers(os.environ),
                     ensure_ascii=False, sort_keys=True, separators=(',', ':')))
