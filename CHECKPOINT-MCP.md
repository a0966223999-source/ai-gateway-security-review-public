# Session-approved platform checkpoints on the original MCP

The seven gateway_checkpoint_* tools reuse the existing OAuth MCP, signed
fresh-host transport, Redis connection, owner/mailbox namespace, queue and
original Gateway process. They create no service, mailbox, signer, credential,
subscription, scheduler, native Work or business ACK.

A transport execute/read permission is not checkpoint authority. Every fresh
Gateway call verifies the original host ingress MAC and pinned live generation,
then checks an exact existing session-approved account link, its expiry and
revocation, the original user/token_version, current original route permissions
and explicit thread ownership. It never manufactures a browser session or PAT.
The machine-only route rejects browser cookies, Origin and Authorization headers.
Only its exact path is exempt from browser middleware; its own signature and
link verification fail closed. Neighboring paths remain protected.

Source documents use the original platform Source store. Checkpoint lifetime
cannot exceed link lifetime. Sources remain bound to their original link;
a later link cannot adopt them. Save uses the original immutable-grant CAS.
Both existing worker and original Source root persist attempt fences before
effects. Unknown effects only query the original request/Source; no replay,
new nonce, automatic retry, account or route change is provided.

Every fresh read gets a new read operation key. Receipt-only and already
recorded operations return audit metadata with historical_only=true and
authority=false, with no checkpoint payload. They do not renew permission.

Install in this order: test the Gateway handler and original browser middleware;
activate it on the existing loopback Gateway; install the worker's narrow
checkpoint dispatcher on the same original worker; deploy the updated tools
on the existing MCP. Keep the original fixed host control endpoint and keys.
Do not enable a second worker or change the batch host enrollment. If any
original worker task is active, wait for it to finish before a graceful reload.

Only after tool discovery and a fresh, unexpired user-approved link, run one
bounded create and an independent read/query. Never describe an offline fixture,
Vercel build, transport heartbeat, historical receipt, checkpoint save or HTTP200
as same-Work post-final wake. Source/Holder/Caller/native wake/result ACK remain
separate acceptance requirements. Old Source RPC UNKNOWNs are untouched.
