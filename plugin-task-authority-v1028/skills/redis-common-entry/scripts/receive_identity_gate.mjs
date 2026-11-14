/** Optional gate after the original receiver's trusted event lookup.
 * No listener, timer, credential, registry, claim, send or ACK is created here.
 * Host callbacks must be injected by the original authorized runtime, never
 * deserialized from event payloads. This candidate is not a native identity API.
 */
export function createReceiveIdentityGate({host, scope, enabled = false,
                                          verificationMode = 'native_sender'}) {
  if (typeof host?.originalReceive !== 'function') {
    throw new TypeError('ORIGINAL_RECEIVER_REQUIRED');
  }
  const pinned = structuredClone(scope);
  if (!['native_sender', 'original_task'].includes(verificationMode)) {
    throw new TypeError('HOST_VERIFICATION_MODE_REQUIRED');
  }
  if (verificationMode === 'original_task' &&
      ![pinned.ownerId, pinned.sourceRef, pinned.taskId, pinned.receiverThreadId]
        .every(value => typeof value === 'string' && value)) {
    throw new TypeError('HOST_TASK_SCOPE_REQUIRED');
  }
  const hold = reason => Object.freeze({
    status: 'HELD', reason, nativeSenderVerified: false,
    processing: 'NOT_RUN', transportAck: 'NOT_RUN',
  });
  let active = enabled === true;
  let generation = 0;
  const pending = new Set();
  const ports = ['current', 'authorize', 'readThread', 'verifyNativeMailEvidence'];

  // This mode is selected by the trusted host, never by mail content. Its
  // resolver must read the original current task/Source using existing rights.
  // No payload instruction, self-reported sender or boolean becomes authority.
  async function receiveOriginalTask(event) {
    const epoch = generation;
    if (event?.mailbox_id !== pinned.mailboxId ||
        typeof event.event_id !== 'string' || !event.event_id ||
        typeof event.operation_key !== 'string' || !event.operation_key ||
        event.payload?.to_thread_id !== pinned.receiverThreadId ||
        event.payload?.task_id !== pinned.taskId) return hold('EVENT_OUTSIDE_PINNED_SCOPE');
    if (![host.current, host.resolveOriginalTask].every(x => typeof x === 'function')) {
      return hold('NOT_BOUND_ORIGINAL_TASK_READER');
    }
    if (pending.has(event.event_id)) return hold('SAME_EVENT_ALREADY_IN_PROGRESS');
    pending.add(event.event_id);
    let entered = false;
    const live = () => active && epoch === generation && host.current() === true;
    const canonical = value => JSON.stringify(value, function(key, item) {
      if (item && typeof item === 'object' && !Array.isArray(item)) {
        return Object.fromEntries(Object.keys(item).sort().map(k => [k, item[k]]));
      }
      return item;
    });
    const read = async () => {
      if (!live()) return null;
      const task = structuredClone(await host.resolveOriginalTask({
        scope: structuredClone(pinned), eventId: event.event_id,
        operationKey: event.operation_key, mailboxId: event.mailbox_id,
      }));
      if (!live() || !task || task.status !== 'AUTHORIZED' ||
          task.ownerId !== pinned.ownerId || task.sourceRef !== pinned.sourceRef ||
          task.taskId !== pinned.taskId || task.receiverThreadId !== pinned.receiverThreadId ||
          task.eventId !== event.event_id || task.operationKey !== event.operation_key ||
          typeof task.revision !== 'string' || !task.revision ||
          task.revoked !== false || task.actionKind !== 'continue_original_task' ||
          task.requiresSenderIdentity !== false ||
          typeof task.expiresAt !== 'number' || !Number.isFinite(task.expiresAt) ||
          task.expiresAt <= Date.now() / 1000 ||
          typeof task.nextStep !== 'string' || !task.nextStep) return null;
      return task;
    };
    try {
      const task = await read();
      if (!task) return hold('ORIGINAL_TASK_AUTHORIZATION_REQUIRED');
      const expected = canonical(task);
      const revalidate = async () => {
        const now = await read();
        return now !== null && canonical(now) === expected && live();
      };
      if (!await revalidate()) return hold('TASK_CHANGED_QUERY_ONLY');
      entered = true;
      // Original Receiver owns durable once/CAS/result/ACK, and must call
      // revalidate immediately before its first effect. It executes nextStep
      // from this original task, never an instruction from the mail payload.
      return await host.originalReceive(structuredClone(event), {
        originalTask: structuredClone(task), revalidate,
        verificationMode: 'original_task', nativeSenderVerified: false,
      });
    } catch {
      if (entered) return Object.freeze({status: 'PROCESSING_OUTCOME_UNKNOWN_QUERY_ONLY',
        nativeSenderVerified: false, processing: 'UNKNOWN', transportAck: 'UNKNOWN'});
      return hold('ORIGINAL_TASK_READ_UNKNOWN_QUERY_ONLY');
    } finally { pending.delete(event.event_id); }
  }

  async function receive(originalEvent) {
    // Rollback preserves the original receiver, including its own deny rules.
    if (!active) return host.originalReceive(originalEvent);
    if (verificationMode === 'original_task') {
      return receiveOriginalTask(structuredClone(originalEvent));
    }
    const epoch = generation;
    let originalReceiverEntered = false;
    const event = structuredClone(originalEvent);
    const key = event?.event_id;
    const payload = event?.payload;
    if (typeof key !== 'string' || !key ||
        typeof event.operation_key !== 'string' || !event.operation_key ||
        event.mailbox_id !== pinned.mailboxId ||
        payload?.to_thread_id !== pinned.receiverThreadId ||
        payload?.task_id !== pinned.taskId ||
        !pinned.senderThreadIds.includes(payload?.from_thread_id)) {
      return hold('EVENT_OUTSIDE_PINNED_SCOPE');
    }
    if (ports.some(name => typeof host[name] !== 'function')) {
      return hold('NOT_BOUND_ORIGINAL_NATIVE_HOST_PORTS');
    }
    if (pending.has(key)) return hold('SAME_EVENT_ALREADY_IN_PROGRESS');
    pending.add(key);
    const guard = async () => active && epoch === generation &&
      host.current() === true &&
      await host.authorize(structuredClone(pinned), structuredClone(event)) === true &&
      active && epoch === generation && host.current() === true;
    try {
      if (!await guard()) return hold('CURRENT_TASK_AUTHORIZATION_REQUIRED');
      const items = [];
      const ids = new Set();
      const cursors = new Set();
      let cursor;
      let latestTurn;
      let bytes = 0;
      const expires = performance.now() + 20000;
      for (let page = 0; page < 128; page++) {
        if (!await guard()) return hold('AUTHORIZATION_CHANGED');
        if (performance.now() >= expires) return hold('READ_BUDGET_EXCEEDED');
        // Use the original official targeted reader. No environment override,
        // account enumeration, fallback or alternate route is accepted.
        const args = {threadId: payload.from_thread_id, limit: 20};
        if (cursor !== undefined) args.cursor = cursor;
        let timer;
        let raw;
        try {
          raw = await Promise.race([
            host.readThread(args),
            new Promise((_, reject) => {
              timer = setTimeout(() => reject(new Error('READ_DEADLINE')),
                Math.max(1, expires - performance.now()));
            }),
          ]);
        } finally { clearTimeout(timer); }
        if (!await guard()) return hold('AUTHORIZATION_CHANGED');
        if (raw?.isError) return hold('NATIVE_READ_DENIED_OR_UNKNOWN');
        const value = raw?.structuredContent;
        bytes += Buffer.byteLength(JSON.stringify(value ?? null));
        if (bytes > 1048576) return hold('READ_BYTE_LIMIT');
        if (value?.threadId !== payload.from_thread_id ||
            value.truncated === true || !Array.isArray(value.items) ||
            value.items.length > 20 || !value.latestTurn?.id ||
            value.latestTurn.error !== null) {
          return hold('NATIVE_READ_INCOMPLETE_OR_MISMATCH');
        }
        const turn = JSON.stringify(value.latestTurn);
        if (latestTurn !== undefined && turn !== latestTurn) {
          return hold('NATIVE_HISTORY_CHANGED_QUERY_ONLY');
        }
        latestTurn = turn;
        for (const item of value.items) {
          if (!item?.id || ids.has(item.id) || item.truncated !== false) {
            return hold('NATIVE_ITEMS_INCOMPLETE_OR_DUPLICATED');
          }
          ids.add(item.id);
          items.push(structuredClone(item));
        }
        cursor = value.nextCursor;
        if (cursor === null) {
          // The existing host must check actual original send execution/receipt,
          // exact event/key/content/target and original USER task authorization.
          // Quoted text, an assistant declaration or thread existence is NOT proof.
          const proof = await host.verifyNativeMailEvidence({
            scope: structuredClone(pinned), event: structuredClone(event),
            capture: {threadId: value.threadId, items, complete: true},
          });
          if (!proof || proof.status !== 'VERIFIED' ||
              proof.eventId !== key || proof.operationKey !== event.operation_key ||
              proof.senderThreadId !== payload.from_thread_id ||
              proof.receiverThreadId !== pinned.receiverThreadId ||
              proof.taskId !== pinned.taskId ||
              typeof proof.nativeSendReceiptId !== 'string' ||
              !proof.nativeSendReceiptId) return hold('EXACT_NATIVE_SEND_NOT_VERIFIED');
          if (!await guard()) return hold('AUTHORIZATION_CHANGED');
          // The original receiver still owns durable dedupe, result save/readback
          // and transport ACK. It must call revalidate before its first effect.
          originalReceiverEntered = true;
          return await host.originalReceive(structuredClone(event), {
            nativeMailEvidence: structuredClone(proof), revalidate: guard,
          });
        }
        if (typeof cursor !== 'string' || !cursor || cursors.has(cursor)) {
          return hold('NATIVE_CURSOR_INVALID_OR_CYCLE');
        }
        cursors.add(cursor);
      }
      return hold('READ_PAGE_LIMIT');
    } catch {
      if (originalReceiverEntered) return Object.freeze({
        status: 'PROCESSING_OUTCOME_UNKNOWN_QUERY_ONLY',
        nativeSenderVerified: true, processing: 'UNKNOWN', transportAck: 'UNKNOWN',
      });
      // A denial/timeout/exception never invokes another reader or claims mail.
      return hold('NATIVE_VERIFICATION_UNKNOWN_QUERY_ONLY');
    } finally { pending.delete(key); }
  }
  return Object.freeze({receive,
    rollback() { active = false; generation++; },
    status() { return {enabled: active,
      verificationMode,
      nativePortsBound: ports.every(name => typeof host[name] === 'function'),
      originalTaskReaderBound: typeof host.resolveOriginalTask === 'function',
      pending: pending.size}; },
  });
}
