# One bounded real Codex execution

Owner explicitly requested actual agent execution testing. A new GPT MCP batch was submitted; original Holder/repository reserved its sole item before any send. One already-installed Codex CLI 0.164.0, using existing ChatGPT login, ran in an empty directory with `--ignore-user-config --skip-git-repo-check --ephemeral --sandbox read-only --json`. The prompt prohibited tools/files/network and requested a nonce and arithmetic answer.

The host observed the real `thread.started` receipt and exit 0, bound its ACK digest with `record_native_ack`, validated the returned JSON, and saved the result with the original `finalize_item`. GPT independently read the original database through `batch_status`: succeeded, attempt 1, result sum 5121, completed 2026-11-12T10:08:38.609278+00:00.

This was a host-invoked one-shot executor for this one batch, not automatic dispatch from `batch_task`, and not a native ChatGPT Work child. The MCP still correctly reports `automatic_dispatch=false` and does not attest live execution. General automatic dispatch, generic execution permissions, self-wake, and cross-Work invocation remain unverified. Management enrollment expires at 2026-11-12T10:23:17Z.

Submit request ID: 7d6f7b35522c1898e7dfb505ed979d786e45a6728d9d7ec94a2cc74c95ee16eb.
Independent result read ID: e5e109ef35456f080c2b63dd5c8b55642abfc68e351c97906baf89aa24720493.
