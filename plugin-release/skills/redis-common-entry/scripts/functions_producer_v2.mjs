/** Official same-mailbox MCP producer. Does not discover or invent target IDs. */
export const REQUEST_TOOL = 'mcp__codex_apps__x_redis_test_gateway_source_rpc_request';
export const MAILBOX_ID = 'gateway_internal_v1';
export const QUERY_TOOL = 'mcp__codex_apps__x_redis_test_gateway_source_rpc_query';

/**
 * One client entry for the existing RPC, shared by ordinary Works.
 * The selected connection is explicit; it is never inferred from a failed call.
 * Runtime observations are hints only. The original host retains all authority.
 */
export function createCommonWorkEntry({tools, linkId, runtimeIdentifiers,
  connectionBinding = 'explicit-link'} = {}) {
  if (!['explicit-link', 'host-bound'].includes(connectionBinding)) fail('CONNECTION_BINDING_REQUIRED');
  if (connectionBinding === 'explicit-link') {
    validateIdentifier(linkId, 'EXPLICIT_CONNECTION_REQUIRED');
  } else if (linkId !== undefined) {
    fail('HOST_BOUND_CONNECTION_OMITS_LINK_ID');
  }
  // Choose from the current official tool schema before dispatch, never as a
  // fallback after a rejection. Host-bound tools already select the connection.
  const connectionArgs = connectionBinding === 'explicit-link' ? {link_id: linkId} : {};
  if (!tools || typeof tools[REQUEST_TOOL] !== 'function' ||
      typeof tools[QUERY_TOOL] !== 'function') fail('ORIGINAL_RPC_TOOLS_REQUIRED');
  // Snapshot mutable bindings before any await or caller mutation.
  const requestTool = tools[REQUEST_TOOL].bind(tools);
  const queryTool = tools[QUERY_TOOL].bind(tools);
  const identifiers = {};
  for (const key of ['CODEX_THREAD_ID', 'CODEX_ENVIRONMENT_ID']) {
    if (runtimeIdentifiers && Object.hasOwn(runtimeIdentifiers, key)) {
      identifiers[key] = runtimeIdentifiers[key];
    }
  }
  validateSelfReportIdentifier(identifiers.CODEX_THREAD_ID, 'RUNTIME_THREAD_HINT_REQUIRED');
  if (Object.hasOwn(identifiers, 'CODEX_ENVIRONMENT_ID')) {
    validateSelfReportIdentifier(identifiers.CODEX_ENVIRONMENT_ID, 'RUNTIME_ENVIRONMENT_HINT_INVALID');
  }
  const tickets = new WeakMap();
  const attempts = new Map();

  function prepare({requestJson} = {}) {
    const request = parseCommonRequest(requestJson);
    let reportJson;
    if (request.method === 'register') {
      const report = prepareWorkSelfReport({runtimeIdentifiers: identifiers, requestJson});
      if (report.status !== 'SELF_REPORT_PREPARED_UNVERIFIED') fail(report.issues[0].code);
      reportJson = canonicalWorkSelfReport(report.selfReport);
    }
    const ticket = Object.freeze({
      status: 'PREPARED_NOT_AUTHORIZED', requestId: request.id,
      source: request.source, method: request.method, requestJson,
      mailboxId: MAILBOX_ID, identity: 'NOT_VERIFIED', authorization: 'NOT_VERIFIED',
    });
    tickets.set(ticket, {request, requestJson, reportJson});
    return ticket;
  }

  function exactTicket(ticket) {
    const value = tickets.get(ticket);
    if (!value) fail('ORIGINAL_PREPARED_TICKET_REQUIRED');
    return value;
  }

  async function submit(ticket) {
    const value = exactTicket(ticket);
    const id = value.request.id;
    if (attempts.has(id)) fail('ALREADY_ATTEMPTED_QUERY_ORIGINAL_ONLY');
    // Reserve synchronously before await: concurrent calls cannot double-submit.
    // This is supplemental only; the original server's durable nonce/Source
    // fences survive client recreation and remain the authority across restarts.
    attempts.set(id, value.requestJson);
    const args = {...connectionArgs, mailbox_id: MAILBOX_ID,
      request_json: value.requestJson};
    if (value.reportJson !== undefined) args.self_report_json = value.reportJson;
    return await requestTool(args);
  }

  async function query(ticket) {
    const {request} = exactTicket(ticket);
    // Also usable after reload: prepare the exact retained bytes then QUERY.
    // Never invoke submit as a recovery step, even if query returns UNKNOWN.
    return await queryTool({...connectionArgs, mailbox_id: MAILBOX_ID,
      request_id: request.id});
  }

  return Object.freeze({prepare, submit, query});
}

/** Preserve original bytes. The existing server validates exact canonical JSON. */
function parseCommonRequest(raw) {
  if (typeof raw !== 'string' || !raw || new TextEncoder().encode(raw).length > 65536) {
    fail('REQUEST_JSON_SIZE');
  }
  let request;
  try { request = JSON.parse(raw); } catch { fail('REQUEST_JSON_REQUIRED'); }
  if (!request || typeof request !== 'object' || Array.isArray(request) ||
      Object.keys(request).sort().join(',') !== 'id,method,params,source,v' ||
      request.v !== 1 || !/^[0-9a-f]{64}$/u.test(request.id) ||
      typeof request.id !== 'string' || typeof request.source !== 'string' ||
      !request.source || !['register', 'read', 'save'].includes(request.method) ||
      !request.params || typeof request.params !== 'object' || Array.isArray(request.params)) {
    fail('ORIGINAL_REQUEST_SCHEMA_REQUIRED');
  }
  if (request.method === 'register') validateRegistrationRequest(raw);
  return request;
}

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
export function targetLocatorFromOfficialReference(reference) {
  if (!reference || typeof reference !== 'object' || Array.isArray(reference)) fail('LOCATOR_REQUIRED');
  validateIdentifier(reference.environmentId, 'ENVIRONMENT_REFERENCE_REQUIRED');
  validateIdentifier(reference.threadId, 'LOCATOR_REQUIRED');
  return {v: 2, kind: 'ordinary-work', threadId: reference.threadId, environmentId: reference.environmentId};
}

export function targetLocatorFromCloudTaskReference(reference) {
  if (!reference || typeof reference !== 'object' || Array.isArray(reference)) fail('LOCATOR_REQUIRED');
  validateIdentifier(reference.threadId,'LOCATOR_REQUIRED');
  // Use only an actual official create_thread result or explicit cloud task reference.
  // A runtime environment_context, source_thread_id, or pasted text is not that reference.
  return {v:3,kind:'ordinary-work',route:'cloud-task',threadId:reference.threadId};
}
export function targetLocatorFromNativeChatReference(reference) {
  const old=targetLocatorFromOfficialReference(reference);
  return {...old,v:3,route:'native-chat'};
}
export function canonicalTargetLocator(locator) {
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
export function prepareWorkSelfReport(options = {}) {
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
export function canonicalWorkSelfReport(selfReport) {
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
export async function submitSelfReportedRegistration(options = {}) {
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
export function prepareTargetedRegistration(options = {}) {
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
export async function submitTargetedRegistration({tools, requestJson, targetLocator}) {
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

