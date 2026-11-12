# Original Deer AIO rollout checkpoint

Status: **NOT_DEPLOYED / HOST_UI_AUTH_REQUIRED**. No AIO container has been
started and no sandbox execution mode has been published to the shared entry.

## Observed on the original Codespace

- Docker local socket: Engine 29.8.0-1, linux/amd64.
- Original AIO source dependencies and `agent_sandbox` are present. The initial
  missing `ownership.py` diagnosis was a probe defect: upstream provides the
  `ownership/__init__.py` package. Corrected remote preflight returned
  `PRECHECK_COLLECTED_NOT_ADMITTED`, `missing_source_files=[]`.
- Docker storage: 8.5 GiB available; memory approximately 2.1 GiB available.
  `/tmp` is a separate volume with approximately 38 GiB free. No Docker storage
  relocation, daemon restart, prune, permission change, or new service was done.
- Official `ghcr.io/agent-infra/sandbox:1.11.0` manifest was read on the host;
  selected linux/amd64 layer sizes total 3,623,376,159 compressed bytes.
- First prepare attempt used a manually transcribed digest and failed with
  `BLOCKED_PULL_ERROR`, exit 1, category `NOT_FOUND`, containers_started=0.
  Its observed log SHA256 was
  `6a4a16058ec5036a6d5ba6e2c19566054d8cc47863c75d5c3fd2d160e67dd11a`.
  This is an observation transcribed from the terminal, not a downloaded raw log.
- A direct diagnostic pull using the machine-read descriptor began; it was
  interrupted with Ctrl-C before continuing through the disk/time guard.
- Second prepare attempt uses the machine-read manifest descriptor, a 2 GiB
  free-space guard, and a 900-second deadline. It ran as
  `python /tmp/aio-prepare.py`; final result is **UNKNOWN** because the UI terminal
  disconnected. Do not start another pull or infer image caching from progress.
- Reloading the original Codespace UI redirected to GitHub sign-in. No login,
  credential change, authorization bypass, or substitute host was attempted.
- Independent platform tool status after the UI interruption still verified
  the original signed heartbeat at `observed_at=1794489272.2688494`:
  generation `f8a69fac5f1b2acd07918b0afe7348c5`, lifecycle
  `owner-platform-service-v1`, automatic_dispatch=false. Existing execution
  availability refers to the prior Codex-once mode, **not AIO**.

## Continue after original Codespace access is restored

Use the existing original Codespace and construction terminal. First read only:

```bash
cat /tmp/deer-aio-image-prepare.json
pgrep -af '^python /tmp/aio-prepare.py$'
df -h /var/lib/docker
```

Read the original attempt before any subsequent mutation. An in-progress pull
must retain its existing guard; an unknown attempt is not permission to retry.
The executed temporary script is the 50b0c45 preparation script with its image
digest line replaced directly from `/tmp/aio-manifest.json`; its full executed
source SHA has not yet been collected. Preserve it and the original receipt.

The updated repository helper resolves the official semver manifest and selects
the linux/amd64 descriptor programmatically, removing the manual transcription
step. It has not been transferred to the host or run there yet.

If the existing receipt reports IMAGE_CACHED, independently inspect that exact
digest, then validate original provider import without construction. Next use a
dedicated, bounded original provider scope with empty credential/source mounts,
builtin seccomp, loopback control binding, original isolated network policy and
unique container prefix. Do not overwrite live Gateway configuration.

Required real acceptance remains NOT_RUN: container/API startup, command and
file round trip, same-scope reuse, cross-user separation, host-file/socket
denial, raw host/metadata/internet egress denial, deadline/STOP termination, and
destroy plus independent absence readback. `release` only parks a container.
Original AIO command timeout is ignored; `close` can wait on the executing lock,
so the outer deadline must stop the exact owned container before waiting on
client closure, preserving original ownership fencing.

Only after actual isolation acceptance may the existing authenticated batch
entry gain an explicit sandbox mode. Existing Codex mode must retain its present
contract, SQL once fences, result persistence, STOP and authority boundaries.
Gate1 remains DENY; D2 was untouched. No independent QA ACK is claimed.

Local verification: six preflight/descriptor regression tests passed. They do
not establish container startup, isolation, deployment or public availability.
