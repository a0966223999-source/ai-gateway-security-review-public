/** Official same-mailbox MCP producer. Does not discover or invent target IDs. */
export const REQUEST_TOOL = 'mcp__codex_apps__x_redis_test_gateway_source_rpc_request';
export const MAILBOX_ID = 'gateway_internal_v1';

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
