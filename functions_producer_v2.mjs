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

export function canonicalTargetLocator(locator) {
  if (!locator || typeof locator !== 'object' || Array.isArray(locator)) fail('LOCATOR_REQUIRED');
  validateIdentifier(locator.environmentId, 'ENVIRONMENT_REFERENCE_REQUIRED');
  validateIdentifier(locator.threadId, 'LOCATOR_REQUIRED');
  if (Object.keys(locator).sort().join(',') !== 'environmentId,kind,threadId,v'
      || locator.v !== 2 || locator.kind !== 'ordinary-work') fail('TARGET_LOCATOR_SCHEMA');
  // Exact Python canonical ordering. The only numeric field is integer v=2.
  return JSON.stringify({environmentId: locator.environmentId, kind: locator.kind,
    threadId: locator.threadId, v: locator.v});
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
