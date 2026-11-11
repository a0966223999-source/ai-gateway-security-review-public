/**
 * Mobile/desktop entry selection for the existing same-mailbox producer.
 * The selection is a caller hint, not authenticated device or target identity.
 * Both entries retain the official reference's cloud/native route. Neither
 * entry discovers a reference, converts runtime hints into one, or retries.
 */
import {
  prepareTargetedRegistration,
  submitTargetedRegistration,
} from './functions_producer_v2.mjs';

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
export function createDeviceRegistrationEntries({tools, hostReferences, deniedThreadIds} = {}) {
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
