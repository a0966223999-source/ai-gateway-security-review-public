/** Original-host integration. Not a new RPC, permission grant or resolver. */
import {createDeviceRegistrationEntries} from './functions_device_entry_v1.mjs';
import {canonicalTargetLocator, targetLocatorFromCloudTaskReference,
  targetLocatorFromNativeChatReference} from './functions_producer_v2.mjs';

export const CREATE_THREAD_TOOL = 'mcp__cloud_plugin_connector_1p_f0fcb7e1f1dc8191b87056c93cd6338f_thread_management__create_thread';

function fail(code) { throw new Error(code); }
function isObject(value) { return value && typeof value === 'object' && !Array.isArray(value); }

/**
 * Install only in the existing privileged calling host. Do not expose this
 * object or its reference installation methods as caller-supplied RPC input.
 * The exact official create tool result is captured by the host itself; native
 * references must arrive through the host's actual supported chat-reference
 * context. No route is inferred from the selected phone/computer surface.
 */
export function createOfficialDeviceHost({tools, deniedThreadIds} = {}) {
  // Validate installation before any create call can have an external effect.
  createDeviceRegistrationEntries({tools, hostReferences: [], deniedThreadIds});
  const denied = new Set(deniedThreadIds);
  const references = new Map();
  const preparations = new WeakMap();
  const createAttempts = new Set();
  const registrationAttempts = new Set();
  let revision = 0;
  let open = true;
  let sequence = 0;
  function current() { if (!open) fail('DEVICE_ENTRY_HOST_CONTEXT_INVALIDATED'); }

  function retain(referenceKind, officialReference) {
    current();
    canonicalTargetLocator(referenceKind === 'cloud-task'
      ? targetLocatorFromCloudTaskReference(officialReference)
      : targetLocatorFromNativeChatReference(officialReference));
    const handle = `host-reference-${++sequence}`;
    // The unchanged producer validates identifiers again during preparation.
    // Installation rejects an ambiguous cloud address rather than stripping it.
    const entry = createDeviceRegistrationEntries({tools,
      hostReferences: [{handle, referenceKind, officialReference}],
      deniedThreadIds: [...denied]});
    references.set(handle, Object.freeze({entry, referenceKind,
      threadId: officialReference.threadId,
      officialReference: Object.freeze({threadId: officialReference.threadId,
        ...(referenceKind === 'native-chat' ? {environmentId: officialReference.environmentId} : {})})}));
    return handle;
  }

  async function createWork({creationKey, createArguments} = {}) {
    current();
    if (typeof creationKey !== 'string' || !/^[a-zA-Z0-9_.:-]{1,128}$/u.test(creationKey)) {
      fail('DEVICE_ENTRY_CREATION_KEY_REQUIRED');
    }
    if (!isObject(createArguments) || typeof createArguments.title !== 'string' ||
        !createArguments.title.trim() || typeof createArguments.prompt !== 'string' ||
        !createArguments.prompt.trim()) fail('DEVICE_ENTRY_CREATE_ARGUMENTS_REQUIRED');
    if (!tools || typeof tools[CREATE_THREAD_TOOL] !== 'function') fail('OFFICIAL_CREATE_TOOL_REQUIRED');
    if (createAttempts.has(creationKey)) fail('DEVICE_ENTRY_CREATION_ALREADY_STARTED');
    createAttempts.add(creationKey);
    const startedRevision = revision;
    // User authorization to create the particular Work is still required.
    // No creation, registration or continuation is implicit in installation.
    const officialResult = await tools[CREATE_THREAD_TOOL]({...createArguments});
    if (!open || revision !== startedRevision) {
      return Object.freeze({status: 'CREATION_RETURNED_CONTEXT_INVALIDATED', officialResult,
        referenceHandle: null, registration: 'NOT_RUN'});
    }
    const result = officialResult?.structuredContent;
    // Consume only structured official metadata from this exact invocation.
    // Never parse assistant text, a copied URL, runtime IDs, or error messages.
    if (officialResult?.isError || !isObject(result) ||
        result.taskCreated === false || result.task_created === false || result.status === 'unavailable' ||
        typeof result.threadId !== 'string' || !result.threadId.trim()) {
      return Object.freeze({status: 'OFFICIAL_CREATE_REFERENCE_UNAVAILABLE', officialResult,
        referenceHandle: null, registration: 'NOT_RUN'});
    }
    let referenceHandle;
    try { referenceHandle = retain('cloud-task', {threadId: result.threadId}); }
    catch {
      // Creation may have happened. Preserve the official outcome even when
      // its reference cannot be installed, and never create a replacement.
      return Object.freeze({status: 'OFFICIAL_CREATE_REFERENCE_UNAVAILABLE', officialResult,
        referenceHandle: null, registration: 'NOT_RUN'});
    }
    return Object.freeze({status: 'OFFICIAL_CREATE_REFERENCE_RETAINED', officialResult,
      referenceHandle, admissionOutcome: result.admissionOutcome ?? 'unconfirmed',
      registration: 'NOT_RUN', authorization: 'NOT_VERIFIED'});
  }

  function installNativeReference(officialChatReference) {
    current();
    // Protected host installation port. The caller of this method, not a JSON
    // marker in the reference, must establish that this is actual platform
    // chat-reference context. It does not trigger a read or grant any access.
    if (!isObject(officialChatReference)) fail('DEVICE_ENTRY_OFFICIAL_REFERENCE_REQUIRED');
    return retain('native-chat', officialChatReference);
  }

  function prepare(surface, {referenceHandle, requestJson, selfReport, ...extra} = {}) {
    current();
    if (Object.keys(extra).length) fail('DEVICE_ENTRY_OPTION_UNSUPPORTED');
    const reference = references.get(referenceHandle);
    if (!reference) fail('DEVICE_ENTRY_OFFICIAL_REFERENCE_REQUIRED');
    if (denied.has(reference.threadId)) fail('DEVICE_ENTRY_TARGET_ACCESS_BLOCKED');
    const prepared = reference.entry[surface]({referenceHandle, requestJson, selfReport});
    if (prepared.status === 'INPUTS_PREPARED_NOT_AUTHORIZED') {
      preparations.set(prepared, Object.freeze({reference, prepared, revision,
        requestId: JSON.parse(requestJson).id}));
    }
    return prepared;
  }

  async function submit(prepared) {
    current();
    const ticket = preparations.get(prepared);
    if (!ticket) fail('DEVICE_ENTRY_PREPARATION_REQUIRED');
    if (ticket.revision !== revision) fail('DEVICE_ENTRY_HOST_CONTEXT_CHANGED');
    if (denied.has(ticket.reference.threadId)) fail('DEVICE_ENTRY_TARGET_ACCESS_BLOCKED');
    if (registrationAttempts.has(ticket.requestId)) fail('DEVICE_ENTRY_ATTEMPT_ALREADY_STARTED');
    registrationAttempts.add(ticket.requestId);
    return await ticket.reference.entry.submit(prepared);
  }

  function blockTarget(threadId) {
    current();
    if (typeof threadId !== 'string' || !threadId.trim()) fail('DEVICE_ENTRY_TARGET_REQUIRED');
    denied.add(threadId);
    revision++; // Any previously prepared operation must be reviewed again.
  }

  return Object.freeze({
    createWork,
    installNativeReference,
    prepareMobile: options => prepare('prepareMobile', options),
    prepareDesktop: options => prepare('prepareDesktop', options),
    submit,
    blockTarget,
    invalidate() { open = false; revision++; },
  });
}
