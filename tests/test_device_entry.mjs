import test from 'node:test';
import assert from 'node:assert/strict';
import {createDeviceRegistrationEntries} from '../functions_device_entry_v1.mjs';
import {REQUEST_TOOL} from '../functions_producer_v2.mjs';

const requestJson = JSON.stringify({id: 'a'.repeat(64), method: 'register', params: {},
  source: 'b'.repeat(64), v: 1});
const cloud = {handle: 'cloud', referenceKind: 'cloud-task',
  officialReference: {threadId: 'synthetic-cloud'}};
const native = {handle: 'native', referenceKind: 'native-chat',
  officialReference: {threadId: 'synthetic-native', environmentId: 'official-environment'}};

function fixture(options = {}) {
  const calls = [];
  const outcome = {status: 'UNKNOWN_QUERY_ONLY'};
  const tools = {[REQUEST_TOOL]: async args => {calls.push(args); return outcome;}};
  const entries = createDeviceRegistrationEntries({tools,
    hostReferences: [cloud, native], deniedThreadIds: [], ...options});
  return {entries, calls, outcome};
}

for (const surface of ['Mobile', 'Desktop']) {
  for (const reference of [cloud, native]) {
    test(`${surface} entry preserves ${reference.referenceKind} official address`, async () => {
      const {entries, calls, outcome} = fixture();
      const prep = entries[`prepare${surface}`]({referenceHandle: reference.handle, requestJson});
      assert.equal(prep.status, 'INPUTS_PREPARED_NOT_AUTHORIZED');
      assert.equal(prep.entrySurface, surface.toLowerCase());
      assert.equal(prep.surfaceProvenance, 'CALLER_SELECTION_NOT_VERIFIED');
      assert.equal(prep.authorization, 'NOT_VERIFIED');
      assert.equal(prep.identity, 'NOT_VERIFIED');
      assert.equal(prep.targetRoute, reference.referenceKind);
      assert.equal(calls.length, 0);
      assert.equal(await entries.submit(prep), outcome);
      assert.equal(calls.length, 1);
      assert.equal(calls[0].request_json, requestJson);
      assert.equal(calls[0].mailbox_id, 'gateway_internal_v1');
      assert.deepEqual(Object.keys(calls[0]).sort(),
        ['mailbox_id', 'request_json', 'target_locator_json']);
      const locator = JSON.parse(calls[0].target_locator_json);
      assert.equal(locator.route, reference.referenceKind);
      assert.equal(locator.threadId, reference.officialReference.threadId);
      assert.equal(Object.hasOwn(locator, 'environmentId'), reference === native);
      if (reference === native) assert.equal(locator.environmentId, 'official-environment');
      await assert.rejects(entries.submit(prep), /DEVICE_ENTRY_ATTEMPT_ALREADY_STARTED/);
      assert.equal(calls.length, 1);
    });
  }
}

test('device selection alone cannot create an official route or reference', async () => {
  const {entries, calls} = fixture();
  for (const prepare of [entries.prepareMobile, entries.prepareDesktop]) {
    const prep = prepare({requestJson});
    assert.equal(prep.status, 'PREPARATION_BLOCKED');
    assert.equal(prep.targetRoute, 'UNRESOLVED');
    assert(prep.issues.some(x => x.code === 'DEVICE_ENTRY_OFFICIAL_REFERENCE_REQUIRED'));
    await assert.rejects(entries.submit(prep), /DEVICE_ENTRY_PREPARATION_REQUIRED/);
  }
  assert.equal(calls.length, 0);
});

test('runtime hints cannot select a route, supply an environment, or bypass a missing handle', () => {
  const {entries, calls} = fixture();
  const prep = entries.prepareMobile({requestJson,
    runtimeIdentifiers: {CODEX_THREAD_ID: 'anything', CODEX_ENVIRONMENT_ID: 'runtime'},
    selfReport: {v: 1, kind: 'work-runtime-hints', observations: {CODEX_THREAD_ID: 'anything'}}});
  assert.equal(prep.status, 'PREPARATION_BLOCKED');
  assert.equal(calls.length, 0);
});

test('self-report is consistency evidence only; runtime environment is never a route', async () => {
  const {entries, calls} = fixture();
  const prep = entries.prepareDesktop({requestJson, referenceHandle: 'native',
    selfReport: {v: 1, kind: 'work-runtime-hints', observations: {
      CODEX_THREAD_ID: 'synthetic-native', CODEX_ENVIRONMENT_ID: 'untrusted-runtime'}}});
  assert.equal(prep.status, 'INPUTS_PREPARED_NOT_AUTHORIZED');
  await entries.submit(prep);
  assert.equal(JSON.parse(calls[0].target_locator_json).environmentId, 'official-environment');
  assert(!Object.hasOwn(calls[0], 'self_report_json'));
});

test('self-report target mismatch blocks before the original RPC', () => {
  const {entries, calls} = fixture();
  const prep = entries.prepareMobile({requestJson, referenceHandle: 'cloud',
    selfReport: {v: 1, kind: 'work-runtime-hints', observations: {CODEX_THREAD_ID: 'other'}}});
  assert.equal(prep.status, 'PREPARATION_BLOCKED');
  assert(prep.issues.some(x => x.code === 'SELF_REPORT_OFFICIAL_TARGET_MISMATCH'));
  assert.equal(calls.length, 0);
});

test('caller-supplied route, reference, authority, device claims and unknown fields are rejected', () => {
  const {entries, calls} = fixture();
  for (const key of ['route', 'officialReference', 'referenceKind', 'targetLocator',
    'target_locator_json', 'self_report_json', 'threadId', 'environmentId',
    'deviceVerified', 'identityVerified', 'authorization', 'hostReferences',
    'deniedThreadIds', 'unexpected']) {
    const prep = entries.prepareDesktop({referenceHandle: 'cloud', requestJson, [key]: true});
    assert.equal(prep.status, 'PREPARATION_BLOCKED', key);
  }
  assert.equal(calls.length, 0);
});

test('all device and route combinations enforce the host-owned denied-target list', async () => {
  const {entries, calls} = fixture({deniedThreadIds: ['synthetic-cloud', 'synthetic-native']});
  for (const prepare of [entries.prepareMobile, entries.prepareDesktop]) {
    for (const referenceHandle of ['cloud', 'native']) {
      const prep = prepare({requestJson, referenceHandle});
      assert.equal(prep.status, 'PREPARATION_BLOCKED');
      assert(prep.issues.some(x => x.code === 'DEVICE_ENTRY_TARGET_ACCESS_BLOCKED'));
      await assert.rejects(entries.submit(prep), /DEVICE_ENTRY_PREPARATION_REQUIRED/);
    }
  }
  assert.equal(calls.length, 0);
});

test('changing the surface or selected reference cannot retry a submitted nonce', async () => {
  const {entries, calls} = fixture();
  const first = entries.prepareMobile({requestJson, referenceHandle: 'cloud'});
  const second = entries.prepareDesktop({requestJson, referenceHandle: 'native'});
  await entries.submit(first);
  await assert.rejects(entries.submit(second), /DEVICE_ENTRY_ATTEMPT_ALREADY_STARTED/);
  assert.equal(calls.length, 1);
});

test('concurrent submissions reserve before awaiting and make exactly one call', async () => {
  let resolve;
  let calls = 0;
  const pending = new Promise(r => {resolve = r;});
  const {entries} = fixture({tools: {[REQUEST_TOOL]: async () => {calls++; return pending;}}});
  const prep = entries.prepareDesktop({requestJson, referenceHandle: 'cloud'});
  const first = entries.submit(prep);
  await assert.rejects(entries.submit(prep), /DEVICE_ENTRY_ATTEMPT_ALREADY_STARTED/);
  resolve({status: 'UNKNOWN_QUERY_ONLY'});
  await first;
  assert.equal(calls, 1);
});

test('FORBIDDEN and arbitrary thrown outcomes retain identity and cannot be retried', async () => {
  for (const error of [new Error('FORBIDDEN'), new Error('transport uncertain')]) {
    let calls = 0;
    const {entries} = fixture({tools: {[REQUEST_TOOL]: async () => {calls++; throw error;}}});
    const prep = entries.prepareMobile({requestJson, referenceHandle: 'native'});
    await assert.rejects(entries.submit(prep), e => e === error);
    await assert.rejects(entries.submit(prep), /DEVICE_ENTRY_ATTEMPT_ALREADY_STARTED/);
    assert.equal(calls, 1);
  }
});

test('host references, request bytes, and blocked list are snapshotted before submission', async () => {
  const reference = structuredClone(native);
  const denied = ['already-denied'];
  const {entries, calls} = fixture({hostReferences: [reference], deniedThreadIds: denied});
  reference.referenceKind = 'cloud-task';
  reference.officialReference.environmentId = 'attacker';
  reference.officialReference.threadId = 'attacker';
  denied.length = 0;
  const options = {requestJson, referenceHandle: 'native'};
  const prep = entries.prepareDesktop(options);
  options.requestJson = 'replacement';
  assert(Object.isFrozen(prep));
  await entries.submit(prep);
  assert.equal(calls[0].request_json, requestJson);
  assert.equal(JSON.parse(calls[0].target_locator_json).environmentId, 'official-environment');
  assert.equal(JSON.parse(calls[0].target_locator_json).threadId, 'synthetic-native');
});

test('clearing the installer deny array cannot reopen a denied reference', async () => {
  const deniedThreadIds = ['synthetic-native'];
  const {entries, calls} = fixture({deniedThreadIds});
  deniedThreadIds.length = 0;
  for (const prepare of [entries.prepareMobile, entries.prepareDesktop]) {
    const prep = prepare({requestJson, referenceHandle: 'native'});
    assert.equal(prep.status, 'PREPARATION_BLOCKED');
    assert(prep.issues.some(x => x.code === 'DEVICE_ENTRY_TARGET_ACCESS_BLOCKED'));
    await assert.rejects(entries.submit(prep), /DEVICE_ENTRY_PREPARATION_REQUIRED/);
  }
  assert.equal(calls.length, 0);
});

test('forged, copied, foreign-scope and blocked tickets cannot submit', async () => {
  const {entries, calls} = fixture();
  const prep = entries.prepareMobile({requestJson, referenceHandle: 'cloud'});
  for (const ticket of [{...prep}, null, {}, fixture().entries.prepareMobile({requestJson, referenceHandle: 'cloud'})]) {
    await assert.rejects(entries.submit(ticket), /DEVICE_ENTRY_PREPARATION_REQUIRED/);
  }
  assert.equal(calls.length, 0);
});

test('missing or malformed trusted host context cannot construct an entry', () => {
  for (const context of [{}, {hostReferences: [], deniedThreadIds: [null]},
    {hostReferences: [cloud, cloud], deniedThreadIds: []},
    {hostReferences: [{...cloud, referenceKind: 'mobile'}], deniedThreadIds: []},
    {hostReferences: [{...cloud, officialReference: {threadId: 'cloud', environmentId: 'runtime'}}], deniedThreadIds: []}]) {
    assert.throws(() => createDeviceRegistrationEntries(context), /DEVICE_ENTRY_HOST_/);
  }
});

test('malformed native reference and noncanonical request block with no call', () => {
  const {entries, calls} = fixture({hostReferences: [{...native, officialReference: {threadId: 'native'}}]});
  const prep = entries.prepareDesktop({referenceHandle: 'native', requestJson: requestJson + ' '});
  assert.equal(prep.status, 'PREPARATION_BLOCKED');
  assert(prep.issues.some(x => x.code === 'ENVIRONMENT_REFERENCE_REQUIRED'));
  assert(prep.issues.some(x => x.code === 'EXACT_CANONICAL_JSON_REQUIRED'));
  assert.equal(calls.length, 0);
});
