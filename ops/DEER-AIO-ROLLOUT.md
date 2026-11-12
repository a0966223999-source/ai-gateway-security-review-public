# Deer AIO sandbox rollout — started, not deployed

Scope: preserve the original SandboxProvider/Sandbox and original AIO backend.
This is a separate workstream from the completed GPT goal/child-task interface.
Reference: bytedance/deer-flow v2.1, commit
345f08be00c8a9495079b732a39b46aa9af1584e; the original neural-graph five-block
design identifies Sandbox as the command/file environment, not task authority.

## Actual current result

The original Codespace's existing desktop terminal returned `/usr/bin/docker`
to `command -v docker`. Only CLI existence is established. Daemon access,
original AIO files/SDK, cached image digests, current configuration and runtime
isolation remain UNKNOWN. No provider was constructed and no container launched.

The cloud-browser inventory does not expose the desktop's Codespace tab. The
native desktop is also being used by another construction line. A read-only
probe encountered input-focus contention and tool timeouts; its incomplete
command was cleared, and the blank shell prompt was observed. It did not return
SANDBOX_PREFLIGHT. Therefore neither its Docker daemon nor its source-file
queries are counted as executed. Existing Gateway files and processes were
not changed by this workstream. The workspace remains in Restricted Mode;
the trust prompt for a new terminal was cancelled.

## Ready-to-run read-only preflight

After exclusive access to an idle original terminal is available, use the
original backend Python to run:

```bash
/home/codespace/gateway-r12-restore-yfl4xryg/source/backend/.venv/bin/python \
  ops/deer_aio_preflight.py inspect \
  --backend /home/codespace/gateway-r12-restore-yfl4xryg/source/backend
```

It reads selected original source hashes, checks the installed agent-sandbox
SDK, queries Docker server/images/current Deer containers, and emits JSON.
It never imports a product provider, pulls images, starts/stops containers,
changes permissions, reads credentials or writes configuration. It cannot
authorize a launch or claim isolation PASS. Run it from the repository holding
this ops file after retrieving the pinned commit through the existing Git path.

The render subcommand accepts only an adequate preflight plus both already
cached images pinned by digest. It emits a candidate for the ORIGINAL provider,
isolated egress, one test container, loopback control binding, resource limits,
and builtin seccomp. Rendering is not applying or launching anything. Image
compatibility must be verified; never silently relax seccomp to make it start.
The memory ownership store is for ONE isolated test process only; production
multi-instance use must bind the existing shared ownership mechanism.

## Actual acceptance path still to execute

1. Verify original current code, daemon, SDK, image/proxy digests, ownership,
   task/caller authority, resource availability and bounded stop path.
2. Start one original AIO test scope with a unique prefix and no credential or
   user-source mounts. Reuse original acquire/get/Sandbox methods.
3. Write/read a harmless artifact; verify same-scope reuse and that another
   user/thread cannot read its artifact or the host canary. Inspect actual
   mounts/namespaces/resource limits; test egress denial.
4. Run a bounded command and one timeout case. AIO's execute_command currently
   discards its timeout argument; merely passing timeout is not a stop proof.
   The existing host manager must enforce the deadline and observe termination.
5. Verify release parks the live container, then call original destroy for the
   exact owned ID. Independently confirm that container/proxy/network and
   child processes have ended; retain only the nonsecret test artifact.
6. Only after these pass, bind the existing protected GPT/Deer tool/runtime
   entry to this trusted provider and run one bounded end-to-end task.

Default-off: no execution MCP tool or fallback to Local/host bash is enabled
by this commit. Fake-provider tests and configuration validation are not
container acceptance. Gate1 DENY; D2 unchanged. No new service or task store.
