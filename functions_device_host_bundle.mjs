/** Generated, reviewed producer integration. Loading has zero external effects.
 * For functions.exec, remove only the two bottom `export ` prefixes.
 * Construct with the real tools and current protected deniedThreadIds.
 */
const producer = (() => {
// functions.exec uses a V8 isolate without the Web TextEncoder global.
// Keep the published v2 source unchanged and supply this dependency only in
// this private closure. No global patch, tool call, package or network access.
const TextEncoder = typeof globalThis.TextEncoder === 'function'
  ? globalThis.TextEncoder : class TextEncoder {
    encode(input = '') {
      const bytes = [];
      for (const character of String(input)) {
        let cp = character.codePointAt(0);
        if (cp >= 0xd800 && cp <= 0xdfff) cp = 0xfffd;
        if (cp <= 0x7f) bytes.push(cp);
        else if (cp <= 0x7ff) bytes.push(0xc0 | (cp >> 6), 0x80 | (cp & 0x3f));
        else if (cp <= 0xffff) bytes.push(0xe0 | (cp >> 12),
          0x80 | ((cp >> 6) & 0x3f), 0x80 | (cp & 0x3f));
        else bytes.push(0xf0 | (cp >> 18), 0x80 | ((cp >> 12) & 0x3f),
          0x80 | ((cp >> 6) & 0x3f), 0x80 | (cp & 0x3f));
      }
      return Uint8Array.from(bytes);
    }
  };
/** Official same-mailbox MCP producer. Does not discover or invent target IDs. */
const REQUEST_TOOL = 'mcp__codex_apps__x_redis_test_gateway_source_rpc_request';
const MAILBOX_ID = 'gateway_internal_v1';

function fail(code) {
  throw new Error(code);
}

function validateIdentifier(value, error) {
  if (typeof value !== 'string' || !value.trim()) fail(error);
  if (/[\u0000-\u001f\u007f]/u.test(value)) fail('TARGET_LOCATOR_IDENTIFIER');
  // Reject lone UTF-16 surrogates instead of silently replacing their bytes.
  for (const character of value) {
    const point = character.codePointAt(0);
    if (point >= 0xd800 && point <= 0xdfff) fail('TARGET_LOCATOR_IDENTIFIER');
  }
  if (new TextEncoder().encode(value).length > 256) fail('TARGET_LOCATOR_IDENTIFIER');
}

function validateSelfReportIdentifier(value, error) {
  validateIdentifier(value, error);
  // One union shared by report preparation and serialization; includes the
  // Unicode whitespace recognized by Python and the ECMAScript BOM whitespace.
  if (/^[\u0020\u0085\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000\ufeff]+$/u.test(value)) {
    fail('SELF_REPORT_IDENTIFIER');
  }
}

/** Use only a verified official chat reference already supplied by producer context. */
function targetLocatorFromOfficialReference(reference) {
  if (!reference || typeof reference !== 'object' || Array.isArray(reference)) fail('LOCATOR_REQUIRED');
  validateIdentifier(reference.environmentId, 'ENVIRONMENT_REFERENCE_REQUIRED');
  validateIdentifier(reference.threadId, 'LOCATOR_REQUIRED');
  return {v: 2, kind: 'ordinary-work', threadId: reference.threadId, environmentId: reference.environmentId};
}

function targetLocatorFromCloudTaskReference(reference) {
  if (!reference || typeof reference !== 'object' || Array.isArray(reference)) fail('LOCATOR_REQUIRED');
  validateIdentifier(reference.threadId,'LOCATOR_REQUIRED');
  // Use only an actual official create_thread result or explicit cloud task reference.
  // A runtime environment_context, source_thread_id, or pasted text is not that reference.
  return {v:3,kind:'ordinary-work',route:'cloud-task',threadId:reference.threadId};
}
function targetLocatorFromNativeChatReference(reference) {
  const old=targetLocatorFromOfficialReference(reference);
  return {...old,v:3,route:'native-chat'};
}
function canonicalTargetLocator(locator) {
  if (!locator || typeof locator !== 'object' || Array.isArray(locator)) fail('LOCATOR_REQUIRED');
  if(locator.v===2 || locator.route==='native-chat') validateIdentifier(locator.environmentId,'ENVIRONMENT_REFERENCE_REQUIRED');
  validateIdentifier(locator.threadId,'LOCATOR_REQUIRED');
  targetLocatorRoute(locator);
  return JSON.stringify(Object.fromEntries(Object.keys(locator).sort().map(k=>[k,locator[k]])));
}

/** Validate only the existing targeted host's exact, ASCII register request. */
function validateRegistrationRequest(requestJson) {
  if (typeof requestJson !== 'string' || !requestJson) fail('REQUEST_JSON_REQUIRED');
  if (new TextEncoder().encode(requestJson).length > 65536) fail('REQUEST_JSON_SIZE');
  let request;
  try {
    request = JSON.parse(requestJson);
  } catch {
    fail('REQUEST_JSON_REQUIRED');
  }
  if (!request || typeof request !== 'object' || Array.isArray(request) ||
      Object.keys(request).sort().join(',') !== 'id,method,params,source,v' ||
      request.v !== 1 || request.method !== 'register' ||
      typeof request.id !== 'string' || !/^[0-9a-f]{64}$/u.test(request.id) ||
      typeof request.source !== 'string' || !/^[0-9a-f]{64}$/u.test(request.source) ||
      !request.params || typeof request.params !== 'object' ||
      Array.isArray(request.params) || Object.keys(request.params).length !== 0) {
    fail('EXACT_REGISTER_REQUEST_REQUIRED');
  }
  // The targeted register schema contains only fixed ASCII keys/values and
  // lowercase hex strings. This is the original canonical representation,
  // including duplicate-key rejection, without reserializing submitted bytes.
  const canonical = JSON.stringify({id: request.id, method: 'register', params: {},
    source: request.source, v: 1});
  if (requestJson !== canonical) fail('EXACT_CANONICAL_JSON_REQUIRED');
}

/** Collect explicit Work observations. No route, official reference, or grant. */
function prepareWorkSelfReport(options = {}) {
  const issues = [];
  const validOptions = options !== null && typeof options === 'object' && !Array.isArray(options);
  if (!validOptions) issues.push({input: 'options', code: 'OPTIONS_OBJECT_REQUIRED'});
  const {runtimeIdentifiers, requestJson} = validOptions ? options : {};
  const observations = {};
  if (!runtimeIdentifiers || typeof runtimeIdentifiers !== 'object' ||
      Array.isArray(runtimeIdentifiers)) {
    issues.push({input: 'runtimeIdentifiers', code: 'RUNTIME_THREAD_HINT_REQUIRED'});
  } else {
    for (const [key, error] of [['CODEX_THREAD_ID', 'RUNTIME_THREAD_HINT_REQUIRED'],
      ['CODEX_ENVIRONMENT_ID', 'RUNTIME_ENVIRONMENT_HINT_INVALID']]) {
      if (key === 'CODEX_ENVIRONMENT_ID' && runtimeIdentifiers[key] === undefined) continue;
      try {
        validateSelfReportIdentifier(runtimeIdentifiers[key], error);
        observations[key] = runtimeIdentifiers[key];
      } catch (error_) {
        issues.push({input: key, code: error_.message});
      }
    }
  }
  try {
    validateRegistrationRequest(requestJson);
  } catch (error) {
    issues.push({input: 'requestJson', code: error.message});
  }
  const report = Object.freeze({v: 1, kind: 'work-runtime-hints',
    observations: Object.freeze(observations)});
  return Object.freeze({
    status: issues.length ? 'SELF_REPORT_INPUTS_MISSING' : 'SELF_REPORT_PREPARED_UNVERIFIED',
    issues: Object.freeze(issues.map(issue => Object.freeze(issue))),
    // Never expose a partially valid report as the next preparation step's input.
    selfReport: issues.length ? null : report,
    requestJson: issues.length ? null : requestJson,
    route: 'UNRESOLVED',
    identity: 'NOT_VERIFIED',
    authorization: 'NOT_VERIFIED',
    registration: 'NOT_RUN',
  });
}

/** Exact canonical transport shape. Observations remain untrusted lookup hints. */
function canonicalWorkSelfReport(selfReport) {
  const observations = selfReport?.observations;
  if (!selfReport || typeof selfReport !== 'object' || Array.isArray(selfReport) ||
      Object.keys(selfReport).sort().join(',') !== 'kind,observations,v' ||
      selfReport.v !== 1 || selfReport.kind !== 'work-runtime-hints' ||
      !observations || typeof observations !== 'object' || Array.isArray(observations) ||
      !Object.hasOwn(observations, 'CODEX_THREAD_ID') ||
      Object.keys(observations).some(key => !['CODEX_THREAD_ID', 'CODEX_ENVIRONMENT_ID'].includes(key))) {
    fail('SELF_REPORT_SCHEMA');
  }
  const snapshot = {};
  for (const key of ['CODEX_ENVIRONMENT_ID', 'CODEX_THREAD_ID']) {
    if (!Object.hasOwn(observations, key)) continue;
    const value = observations[key];
    validateSelfReportIdentifier(value, 'SELF_REPORT_IDENTIFIER');
    snapshot[key] = value;
  }
  const raw = JSON.stringify({kind: 'work-runtime-hints', observations: snapshot, v: 1});
  if (new TextEncoder().encode(raw).length > 2048) fail('SELF_REPORT_SIZE');
  return raw;
}

/** Submit hints once through the original RPC. No discovery, route or grant. */
async function submitSelfReportedRegistration(options = {}) {
  if (!options || typeof options !== 'object' || Array.isArray(options)) fail('OPTIONS_OBJECT_REQUIRED');
  if (Object.hasOwn(options, 'targetLocator') || Object.hasOwn(options, 'target_locator_json')) {
    fail('REGISTRATION_SIDEBANDS_MUTUALLY_EXCLUSIVE');
  }
  const {tools, requestJson, selfReport} = options;
  const selfReportJson = canonicalWorkSelfReport(selfReport);
  const preparation = prepareWorkSelfReport({runtimeIdentifiers: selfReport.observations, requestJson});
  if (preparation.status !== 'SELF_REPORT_PREPARED_UNVERIFIED') fail(preparation.issues[0].code);
  if (!tools || typeof tools[REQUEST_TOOL] !== 'function') fail('OFFICIAL_REQUEST_TOOL_REQUIRED');
  // Do not rewrite, augment or hash a different request. Tool schema presence is
  // not proof of deployed support. UNKNOWN and thrown outcomes are not retried.
  return await tools[REQUEST_TOOL]({mailbox_id: MAILBOX_ID, request_json: requestJson,
    self_report_json: selfReportJson});
}

/**
 * Pure input preparation. Makes zero tool calls and creates no authority.
 *
 * referenceKind must come from the reference's known official origin, never
 * from a missing environment, a runtime ID, a role label, or a failed read.
 * This helper cannot authenticate that origin; the caller must supply it.
 * Inspect status before explicitly invoking the original submit function.
 */
function prepareTargetedRegistration(options = {}) {
  const issues = [];
  const validOptions = options !== null && typeof options === 'object' && !Array.isArray(options);
  if (!validOptions) issues.push({input: 'options', code: 'OPTIONS_OBJECT_REQUIRED'});
  const {tools, requestJson, referenceKind, officialReference, selfReport} = validOptions ? options : {};
  let targetLocator;
  if (!officialReference || typeof officialReference !== 'object' ||
      Array.isArray(officialReference)) {
    issues.push({input: 'officialReference', code: 'LOCATOR_REQUIRED'});
  } else if (!['cloud-task', 'native-chat'].includes(referenceKind)) {
    issues.push({input: 'referenceKind', code: 'OFFICIAL_REFERENCE_KIND_REQUIRED'});
  } else {
    try {
      targetLocator = referenceKind === 'cloud-task'
        ? targetLocatorFromCloudTaskReference(officialReference)
        : targetLocatorFromNativeChatReference(officialReference);
      canonicalTargetLocator(targetLocator);
    } catch (error) {
      // Report the existing constructor's input error without disclosing data.
      issues.push({input: 'officialReference', code: error.message});
    }
  }
  if (selfReport !== undefined) {
    const observations = selfReport?.observations;
    if (!selfReport || typeof selfReport !== 'object' || Array.isArray(selfReport) ||
        Object.keys(selfReport).sort().join(',') !== 'kind,observations,v' ||
        selfReport.v !== 1 || selfReport.kind !== 'work-runtime-hints' ||
        !observations || typeof observations !== 'object' || Array.isArray(observations) ||
        Object.keys(observations).some(key => !['CODEX_THREAD_ID', 'CODEX_ENVIRONMENT_ID'].includes(key))) {
      issues.push({input: 'selfReport', code: 'SELF_REPORT_SCHEMA'});
    } else {
      try {
        validateSelfReportIdentifier(observations.CODEX_THREAD_ID, 'RUNTIME_THREAD_HINT_REQUIRED');
        if (Object.hasOwn(observations, 'CODEX_ENVIRONMENT_ID')) {
          validateSelfReportIdentifier(observations.CODEX_ENVIRONMENT_ID, 'RUNTIME_ENVIRONMENT_HINT_INVALID');
        }
        if (targetLocator && observations.CODEX_THREAD_ID !== targetLocator.threadId) {
          fail('SELF_REPORT_OFFICIAL_TARGET_MISMATCH');
        }
        // Runtime environment is never copied into the official locator, nor
        // used to infer cloud/native routing. A runtime hint alone does not
        // establish its mapping to an official, authorized computer reference.
      } catch (error) {
        issues.push({input: 'selfReport', code: error.message});
      }
    }
  }
  try {
    validateRegistrationRequest(requestJson);
  } catch (error) {
    issues.push({input: 'requestJson', code: error.message});
  }
  if (!tools || typeof tools[REQUEST_TOOL] !== 'function') {
    issues.push({input: 'tools', code: 'OFFICIAL_REQUEST_TOOL_REQUIRED'});
  }
  const producerArguments = issues.length === 0
    ? Object.freeze({requestJson, targetLocator: Object.freeze(targetLocator)})
    : null;
  return Object.freeze({
    status: producerArguments ? 'INPUTS_PREPARED_NOT_AUTHORIZED' : 'PREPARATION_BLOCKED',
    issues: Object.freeze(issues.map(issue => Object.freeze(issue))),
    producerArguments,
    // Callable presence does not verify the currently exposed tool schema.
    officialReferenceProvenance: 'NOT_VERIFIED',
    toolSchema: 'NOT_VERIFIED',
    hostVerification: 'NOT_RUN',
    registration: 'NOT_RUN',
  });
}

/** Exactly one official invocation. UNKNOWN_QUERY_ONLY is returned unchanged, never retried. */
async function submitTargetedRegistration({tools, requestJson, targetLocator}) {
  const targetLocatorJson = canonicalTargetLocator(targetLocator);
  if (typeof requestJson !== 'string' || !requestJson) fail('REQUEST_JSON_REQUIRED');
  let request;
  try { request = JSON.parse(requestJson); } catch { fail('REQUEST_JSON_REQUIRED'); }
  if (request?.method !== 'register') fail('TARGET_LOCATOR_REGISTER_ONLY');
  if (!tools || typeof tools[REQUEST_TOOL] !== 'function') fail('OFFICIAL_REQUEST_TOOL_REQUIRED');
  // Server validates canonical request bytes. Never parse/reserialize or augment them here.
  return await tools[REQUEST_TOOL]({mailbox_id: MAILBOX_ID, request_json: requestJson,
    target_locator_json: targetLocatorJson});
}

/** Typed official address only, not origin, user authorship, or authority. */
function targetLocatorRoute(locator) {
  if(!locator||typeof locator!=='object'||Array.isArray(locator)||locator.kind!=='ordinary-work')throw Error('TARGET_LOCATOR_SCHEMA');
  const route=locator.v===2?'native-chat':locator.v===3?locator.route:undefined;
  if(!['cloud-task','native-chat'].includes(route))throw Error('TARGET_LOCATOR_SCHEMA');
  const keys=['kind','threadId','v',...(locator.v===3?['route']:[]),...(route==='native-chat'?['environmentId']:[])].sort().join(',');
  if(Object.keys(locator).sort().join(',')!==keys)throw Error('TARGET_LOCATOR_SCHEMA');
  for(const key of ['threadId',...(route==='native-chat'?['environmentId']:[])]){
    const value=locator[key];if(typeof value!=='string'||!value.trim()||/[\u0000-\u001f\u007f]/u.test(value))throw Error('VERIFIED_NATIVE_REFERENCE_REQUIRED');
    try{if(unescape(encodeURIComponent(value)).length>256)throw Error('VERIFIED_NATIVE_REFERENCE_REQUIRED');}catch{throw Error('VERIFIED_NATIVE_REFERENCE_REQUIRED');}
  }
  return route;
}
function targetReadArguments(locator,args) {
  const route=targetLocatorRoute(locator);
  if(!args||typeof args!=='object'||Array.isArray(args)||args.threadId!==locator.threadId||![1,20].includes(args.limit)||
    Object.keys(args).some(k=>!['threadId','environmentId','limit','cursor'].includes(k))||
    (Object.hasOwn(args,'cursor')&&(typeof args.cursor!=='string'||!args.cursor)))throw Error('EXACT_TARGET_NATIVE_ADDRESS_REQUIRED');
  if(route==='cloud-task'){
    if(Object.hasOwn(args,'environmentId'))throw Error('CLOUD_ROUTE_OMITS_ENVIRONMENT');
    return {...args};
  }
  if(Object.hasOwn(args,'environmentId')&&args.environmentId!==locator.environmentId)throw Error('EXACT_TARGET_NATIVE_ADDRESS_REQUIRED');
  return {...args,environmentId:locator.environmentId};
}
function targetEnvironmentMatches(locator,value) {
  return targetLocatorRoute(locator)==='cloud-task'||!Object.hasOwn(value,'environmentId')||value.environmentId===locator.environmentId;
}

return Object.freeze({REQUEST_TOOL, MAILBOX_ID, targetLocatorFromOfficialReference, targetLocatorFromCloudTaskReference, targetLocatorFromNativeChatReference, canonicalTargetLocator, prepareWorkSelfReport, canonicalWorkSelfReport, submitSelfReportedRegistration, prepareTargetedRegistration, submitTargetedRegistration});
})();
const device = (() => {
const {prepareTargetedRegistration, submitTargetedRegistration} = producer;
/**
 * Mobile/desktop entry selection for the existing same-mailbox producer.
 * The selection is a caller hint, not authenticated device or target identity.
 * Both entries retain the official reference's cloud/native route. Neither
 * entry discovers a reference, converts runtime hints into one, or retries.
 */


const SURFACES = new Set(['mobile', 'desktop']);
const FORBIDDEN_OPTIONS = new Set([
  'targetLocator', 'target_locator_json', 'self_report_json',
  'route', 'environmentId', 'threadId', 'runtimeIdentifiers',
  'deviceVerified', 'identityVerified', 'authorization', 'officialReference',
  'referenceKind', 'deniedThreadIds', 'hostReferences',
]);
const ALLOWED_OPTIONS = new Set([
  'requestJson', 'referenceHandle', 'selfReport',
]);

function issue(input, code) { return Object.freeze({input, code}); }
function fail(code) { throw new Error(code); }

/**
 * Construct one preparation/submission scope with the existing official tools.
 * hostReferences and deniedThreadIds are installation-time host context, not
 * RPC/caller input. The calling host must obtain actual official references
 * from supported context. This module validates shape only; it cannot
 * authenticate that context or resolve current Work. Callers select a handle,
 * never a route or environment. Denied targets are blocked on both entries.
 *
 * The private ticket prevents alteration between preparation and submission.
 * A request ID can be attempted only once in this instance, including thrown
 * or UNKNOWN outcomes. This is not a persisted/global replay fence: the original
 * host, immutable sideband, nonce, signed deadline and once checks remain final.
 */
function createDeviceRegistrationEntries({tools, hostReferences, deniedThreadIds} = {}) {
  if (!Array.isArray(hostReferences) || !Array.isArray(deniedThreadIds) ||
      deniedThreadIds.some(id => typeof id !== 'string' || !id.trim())) {
    fail('DEVICE_ENTRY_HOST_CONTEXT_REQUIRED');
  }
  const denied = new Set(deniedThreadIds);
  const references = new Map();
  for (const row of hostReferences) {
    if (!row || typeof row !== 'object' || Array.isArray(row) ||
        Object.keys(row).sort().join(',') !== 'handle,officialReference,referenceKind' ||
        typeof row.handle !== 'string' || !/^[a-zA-Z0-9_.:-]{1,128}$/u.test(row.handle) ||
        references.has(row.handle)) fail('DEVICE_ENTRY_HOST_REFERENCE_INVALID');
    // Copy an exact address at installation, before caller-owned mutation.
    const reference = row.officialReference;
    const snapshot = reference && typeof reference === 'object' && !Array.isArray(reference)
      ? Object.freeze({threadId: reference.threadId,
        ...(Object.hasOwn(reference, 'environmentId') ? {environmentId: reference.environmentId} : {})})
      : null;
    // The original helper verifies identifier/schema without making tool calls.
    // A cloud reference may include extra official create metadata, but an
    // explicit environment must never be silently dropped and change its route.
    if (!['cloud-task', 'native-chat'].includes(row.referenceKind) || !snapshot ||
        (row.referenceKind === 'cloud-task' && Object.hasOwn(snapshot, 'environmentId'))) {
      fail('DEVICE_ENTRY_HOST_REFERENCE_INVALID');
    }
    references.set(row.handle, Object.freeze({referenceKind: row.referenceKind,
      officialReference: snapshot}));
  }
  const tickets = new WeakMap();
  const attemptedRequests = new Set();

  function prepare(surface, options) {
    if (!SURFACES.has(surface)) fail('DEVICE_ENTRY_SELECTION_REQUIRED');
    const issues = [];
    if (!options || typeof options !== 'object' || Array.isArray(options)) {
      issues.push(issue('options', 'OPTIONS_OBJECT_REQUIRED'));
      options = {};
    }
    for (const key of Object.keys(options)) {
      if (FORBIDDEN_OPTIONS.has(key)) {
        issues.push(issue(key, 'DEVICE_ENTRY_OFFICIAL_REFERENCE_REQUIRED'));
      } else if (!ALLOWED_OPTIONS.has(key)) {
        issues.push(issue(key, 'DEVICE_ENTRY_OPTION_UNSUPPORTED'));
      }
    }
    const selected = references.get(options.referenceHandle);
    if (!selected) {
      issues.push(issue('referenceHandle', 'DEVICE_ENTRY_OFFICIAL_REFERENCE_REQUIRED'));
    } else if (denied.has(selected.officialReference.threadId)) {
      issues.push(issue('referenceHandle', 'DEVICE_ENTRY_TARGET_ACCESS_BLOCKED'));
    }
    const original = prepareTargetedRegistration({tools,
      requestJson: options.requestJson,
      referenceKind: selected?.referenceKind,
      officialReference: selected?.officialReference,
      selfReport: options.selfReport,
    });
    issues.push(...original.issues);
    const blocked = issues.length !== 0;
    const result = Object.freeze({
      status: blocked ? 'PREPARATION_BLOCKED' : 'INPUTS_PREPARED_NOT_AUTHORIZED',
      issues: Object.freeze(issues),
      entrySurface: surface,
      surfaceProvenance: 'CALLER_SELECTION_NOT_VERIFIED',
      targetRoute: blocked ? 'UNRESOLVED' : original.producerArguments.targetLocator.route,
      officialReferenceProvenance: 'HOST_SUPPLIED_NOT_AUTHENTICATED_BY_HELPER',
      identity: 'NOT_VERIFIED',
      authorization: 'NOT_VERIFIED',
      hostVerification: 'NOT_RUN',
      registration: 'NOT_RUN',
    });
    if (!blocked) {
      // The existing preparation has already checked exact canonical bytes.
      // Copy its immutable address, not the caller-owned reference object.
      const {requestJson, targetLocator} = original.producerArguments;
      tickets.set(result, Object.freeze({requestJson, targetLocator,
        requestId: JSON.parse(requestJson).id}));
    }
    return result;
  }

  async function submit(preparation) {
    const ticket = tickets.get(preparation);
    if (!ticket) fail('DEVICE_ENTRY_PREPARATION_REQUIRED');
    // Fence before the first async call, including concurrent submissions.
    if (attemptedRequests.has(ticket.requestId)) fail('DEVICE_ENTRY_ATTEMPT_ALREADY_STARTED');
    attemptedRequests.add(ticket.requestId);
    return await submitTargetedRegistration({tools,
      requestJson: ticket.requestJson, targetLocator: ticket.targetLocator});
  }

  return Object.freeze({
    prepareMobile: options => prepare('mobile', options),
    prepareDesktop: options => prepare('desktop', options),
    submit,
  });
}

return Object.freeze({createDeviceRegistrationEntries});
})();
const host = (() => {
const {createDeviceRegistrationEntries} = device;
const {canonicalTargetLocator, targetLocatorFromCloudTaskReference, targetLocatorFromNativeChatReference} = producer;
/** Original-host integration. Not a new RPC, permission grant or resolver. */



const CREATE_THREAD_TOOL = 'mcp__cloud_plugin_connector_1p_f0fcb7e1f1dc8191b87056c93cd6338f_thread_management__create_thread';

function fail(code) { throw new Error(code); }
function isObject(value) { return value && typeof value === 'object' && !Array.isArray(value); }

/**
 * Install only in the existing privileged calling host. Do not expose this
 * object or its reference installation methods as caller-supplied RPC input.
 * The exact official create tool result is captured by the host itself; native
 * references must arrive through the host's actual supported chat-reference
 * context. No route is inferred from the selected phone/computer surface.
 */
function createOfficialDeviceHost({tools, deniedThreadIds} = {}) {
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

return Object.freeze({CREATE_THREAD_TOOL, createOfficialDeviceHost});
})();
export const createOfficialDeviceHost = host.createOfficialDeviceHost;
export const CREATE_THREAD_TOOL = host.CREATE_THREAD_TOOL;
