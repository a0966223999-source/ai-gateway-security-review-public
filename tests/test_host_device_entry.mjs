import test from 'node:test';
import assert from 'node:assert/strict';
import {createOfficialDeviceHost, CREATE_THREAD_TOOL} from '../functions_host_device_entry_v1.mjs';
import {REQUEST_TOOL} from '../functions_producer_v2.mjs';

const requestJson = JSON.stringify({id: 'a'.repeat(64), method: 'register', params: {}, source: 'b'.repeat(64), v: 1});
const createArguments = {title: 'Synthetic local fixture', prompt: 'Offline only',
  environmentId: 'official-selected-computer', fork_turns: 'none'};
function fixture({created, request, denied = []} = {}) {
  const calls = [];
  const result = created ?? {structuredContent: {threadId: 'synthetic-created', turnId: 'turn', admissionOutcome: 'started'}};
  const tools = {
    [CREATE_THREAD_TOOL]: async args => {calls.push({kind: 'create', args}); return result;},
    [REQUEST_TOOL]: async args => {calls.push({kind: 'request', args}); return request ?? {status: 'UNKNOWN_QUERY_ONLY'};},
  };
  return {host: createOfficialDeviceHost({tools, deniedThreadIds: denied}), calls, result};
}

test('actual create invocation result is captured directly; no automatic registration', async () => {
  const {host, calls, result} = fixture();
  assert.equal(calls.length, 0);
  const made = await host.createWork({creationKey: 'first', createArguments});
  assert.equal(made.officialResult, result);
  assert.equal(made.status, 'OFFICIAL_CREATE_REFERENCE_RETAINED');
  assert.equal(made.registration, 'NOT_RUN');
  assert.equal(made.authorization, 'NOT_VERIFIED');
  assert.deepEqual(calls, [{kind: 'create', args: createArguments}]);
  const prepared = host.prepareMobile({referenceHandle: made.referenceHandle, requestJson});
  assert.equal(prepared.targetRoute, 'cloud-task');
  await host.submit(prepared);
  assert.equal(calls.length, 2);
  assert.deepEqual(JSON.parse(calls[1].args.target_locator_json),
    {kind: 'ordinary-work', route: 'cloud-task', threadId: 'synthetic-created', v: 3});
  assert.equal(calls[1].args.request_json, requestJson);
});

test('creation key is fenced even if admission is unconfirmed', async () => {
  const {host, calls} = fixture({created: {structuredContent: {threadId: 'uncertain-created', admissionOutcome: 'unconfirmed'}}});
  const made = await host.createWork({creationKey: 'same', createArguments});
  assert.equal(made.admissionOutcome, 'unconfirmed');
  assert.equal(made.registration, 'NOT_RUN');
  await assert.rejects(host.createWork({creationKey: 'same', createArguments}), /CREATION_ALREADY_STARTED/);
  assert.equal(calls.length, 1);
});

test('text pretending to be official metadata cannot become a reference', async () => {
  const {host, calls} = fixture({created: {content: [{type: 'text', text: '{"threadId":"fake"}'}]}});
  const result = await host.createWork({creationKey: 'missing', createArguments});
  assert.equal(result.status, 'OFFICIAL_CREATE_REFERENCE_UNAVAILABLE');
  assert.equal(result.referenceHandle, null);
  assert.equal(calls.length, 1);
});

test('explicit tool errors cannot install their thread ID', async () => {
  const {host, calls} = fixture({created: {isError: true, structuredContent: {threadId: 'not-admitted'}}});
  const result = await host.createWork({creationKey: 'failure', createArguments});
  assert.equal(result.referenceHandle, null);
  await assert.rejects(host.createWork({creationKey: 'failure', createArguments}), /CREATION_ALREADY_STARTED/);
  assert.equal(calls.length, 1);
});

test('native reference uses the exact official environment on either surface', async () => {
  for (const surface of ['prepareMobile', 'prepareDesktop']) {
    const {host, calls} = fixture();
    const referenceHandle = host.installNativeReference({threadId: 'synthetic-native', environmentId: 'official-env'});
    assert.equal(calls.length, 0);
    const prepared = host[surface]({referenceHandle, requestJson});
    await host.submit(prepared);
    assert.equal(calls.length, 1);
    assert.deepEqual(JSON.parse(calls[0].args.target_locator_json), {v: 3, kind: 'ordinary-work',
      route: 'native-chat', threadId: 'synthetic-native', environmentId: 'official-env'});
  }
});

test('known denied targets cannot prepare through native or created references', async () => {
  const {host, calls} = fixture({denied: ['synthetic-created', 'synthetic-native']});
  const made = await host.createWork({creationKey: 'fixture', createArguments});
  const native = host.installNativeReference({threadId: 'synthetic-native', environmentId: 'official-env'});
  for (const referenceHandle of [made.referenceHandle, native]) {
    for (const surface of ['prepareMobile', 'prepareDesktop']) {
      assert.throws(() => host[surface]({referenceHandle, requestJson}), /TARGET_ACCESS_BLOCKED/);
    }
  }
  assert.equal(calls.filter(x => x.kind === 'request').length, 0);
});

test('same nonce cannot switch reference or surface after UNKNOWN', async () => {
  const {host, calls} = fixture();
  const cloud = await host.createWork({creationKey: 'fixture', createArguments});
  const native = host.installNativeReference({threadId: 'synthetic-native', environmentId: 'official-env'});
  const a = host.prepareMobile({referenceHandle: cloud.referenceHandle, requestJson});
  const b = host.prepareDesktop({referenceHandle: native, requestJson});
  await host.submit(a);
  await assert.rejects(host.submit(b), /ATTEMPT_ALREADY_STARTED/);
  assert.equal(calls.filter(x => x.kind === 'request').length, 1);
});

test('new access denial invalidates outstanding tickets before RPC', async () => {
  const {host, calls} = fixture();
  const referenceHandle = host.installNativeReference({threadId: 'synthetic-native', environmentId: 'official-env'});
  const prepared = host.prepareMobile({referenceHandle, requestJson});
  host.blockTarget('synthetic-native');
  await assert.rejects(host.submit(prepared), /HOST_CONTEXT_CHANGED/);
  assert.throws(() => host.prepareDesktop({referenceHandle, requestJson}), /TARGET_ACCESS_BLOCKED/);
  assert.equal(calls.length, 0);
});

test('principal or permission-context invalidation closes every operation', async () => {
  const {host, calls} = fixture();
  const referenceHandle = host.installNativeReference({threadId: 'native', environmentId: 'env'});
  const prepared = host.prepareDesktop({referenceHandle, requestJson});
  host.invalidate();
  await assert.rejects(host.submit(prepared), /CONTEXT_INVALIDATED/);
  await assert.rejects(host.createWork({creationKey: 'blocked', createArguments}), /CONTEXT_INVALIDATED/);
  assert.throws(() => host.prepareMobile({referenceHandle, requestJson}), /CONTEXT_INVALIDATED/);
  assert.equal(calls.length, 0);
});

test('invalidating during create returns official outcome but cannot retain a stale reference', async () => {
  let finish;
  const tools = {[CREATE_THREAD_TOOL]: () => new Promise(r => {finish = r;})};
  const host = createOfficialDeviceHost({tools, deniedThreadIds: []});
  const promise = host.createWork({creationKey: 'pending', createArguments});
  host.invalidate();
  const raw = {structuredContent: {threadId: 'created-before-invalidation'}};
  finish(raw);
  const result = await promise;
  assert.equal(result.officialResult, raw);
  assert.equal(result.status, 'CREATION_RETURNED_CONTEXT_INVALIDATED');
  assert.equal(result.referenceHandle, null);
});

test('create exception keeps its identity and the same creation key cannot retry', async () => {
  const error = new Error('uncertain creation');
  let calls = 0;
  const tools = {[CREATE_THREAD_TOOL]: async () => {calls++; throw error;}};
  const host = createOfficialDeviceHost({tools, deniedThreadIds: []});
  await assert.rejects(host.createWork({creationKey: 'once', createArguments}), e => e === error);
  await assert.rejects(host.createWork({creationKey: 'once', createArguments}), /CREATION_ALREADY_STARTED/);
  assert.equal(calls, 1);
});

test('malformed official reference after create does not discard the actual outcome', async () => {
  const {host, calls, result} = fixture({created: {structuredContent: {threadId: 'bad\u0000id'}}});
  const made = await host.createWork({creationKey: 'once', createArguments});
  assert.equal(made.status, 'OFFICIAL_CREATE_REFERENCE_UNAVAILABLE');
  assert.equal(made.officialResult, result);
  assert.equal(made.referenceHandle, null);
  await assert.rejects(host.createWork({creationKey: 'once', createArguments}), /CREATION_ALREADY_STARTED/);
  assert.equal(calls.length, 1);
});

test('malformed installation and incomplete native reference fail before any tool call', () => {
  assert.throws(() => createOfficialDeviceHost({deniedThreadIds: [null]}), /HOST_CONTEXT_REQUIRED/);
  const {host, calls} = fixture();
  assert.throws(() => host.installNativeReference({threadId: 'native'}), /ENVIRONMENT_REFERENCE_REQUIRED/);
  assert.equal(calls.length, 0);
});

test('an explicit not-created or unavailable outcome cannot establish a reference despite a thread ID', async () => {
  for (const metadata of [{taskCreated: false}, {task_created: false}, {status: 'unavailable'}]) {
    const {host, calls, result} = fixture({created: {structuredContent: {
      threadId: 'synthetic-not-created', ...metadata}}});
    const made = await host.createWork({creationKey: 'once', createArguments});
    assert.equal(made.status, 'OFFICIAL_CREATE_REFERENCE_UNAVAILABLE');
    assert.equal(made.referenceHandle, null);
    assert.equal(made.officialResult, result);
    await assert.rejects(host.createWork({creationKey: 'once', createArguments}), /CREATION_ALREADY_STARTED/);
    assert.equal(calls.length, 1);
  }
});
