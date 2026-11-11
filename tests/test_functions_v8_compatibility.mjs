import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import {readFileSync} from 'node:fs';

const bundle = readFileSync(new URL('../functions_device_host_bundle.mjs', import.meta.url), 'utf8');
const stripped = bundle.replace(/^export /gm, '');
const requestJson = JSON.stringify({id: 'a'.repeat(64), method: 'register', params: {}, source: 'b'.repeat(64), v: 1});
function fixture() {
  const context = vm.createContext({});
  assert.equal(vm.runInContext('typeof TextEncoder', context), 'undefined');
  const api = vm.runInContext(stripped + '\n({createOfficialDeviceHost, CREATE_THREAD_TOOL})', context);
  let calls = 0;
  const tools = {
    [api.CREATE_THREAD_TOOL]: async () => {calls++; return {structuredContent: {threadId: 'synthetic-cloud'}};},
    mcp__codex_apps__x_redis_test_gateway_source_rpc_request: async () => {calls++; return {status: 'UNKNOWN_QUERY_ONLY'};},
  };
  return {context, api, tools, calls: () => calls};
}

test('bundle runs native preparation in an actual V8 context without TextEncoder', () => {
  const f = fixture();
  const h = f.api.createOfficialDeviceHost({tools: f.tools, deniedThreadIds: []});
  const handle = h.installNativeReference({threadId: '合成測試😀', environmentId: '正式位置測試'});
  const prepared = h.prepareMobile({referenceHandle: handle, requestJson});
  assert.equal(prepared.status, 'INPUTS_PREPARED_NOT_AUTHORIZED');
  assert.equal(prepared.targetRoute, 'native-chat');
  assert.equal(f.calls(), 0);
  assert.equal(vm.runInContext('typeof TextEncoder', f.context), 'undefined');
});

test('V8 fallback preserves exact UTF-8 byte limits and rejects invalid Unicode', () => {
  const f = fixture();
  const h = f.api.createOfficialDeviceHost({tools: f.tools, deniedThreadIds: []});
  for (const threadId of ['a'.repeat(256), '😀'.repeat(64), '界'.repeat(85) + 'a']) {
    assert.equal(new TextEncoder().encode(threadId).length, 256);
    assert.equal(typeof h.installNativeReference({threadId, environmentId: 'env'}), 'string');
  }
  for (const threadId of ['a'.repeat(257), '😀'.repeat(64) + 'a', '界'.repeat(86), '\ud800', '\udfff']) {
    assert.throws(() => h.installNativeReference({threadId, environmentId: 'env'}), /TARGET_LOCATOR_IDENTIFIER/);
  }
  assert.equal(f.calls(), 0);
});

test('V8 fallback preserves the original canonical request size gate', () => {
  const f = fixture();
  const h = f.api.createOfficialDeviceHost({tools: f.tools, deniedThreadIds: []});
  const referenceHandle = h.installNativeReference({threadId: 'synthetic', environmentId: 'env'});
  const p = h.prepareDesktop({referenceHandle, requestJson: 'x'.repeat(65537)});
  assert.equal(p.status, 'PREPARATION_BLOCKED');
  assert(p.issues.some(x => x.code === 'REQUEST_JSON_SIZE'));
  assert.equal(f.calls(), 0);
});

test('V8 fallback also supports captured cloud create and still never auto-registers', async () => {
  const f = fixture();
  const h = f.api.createOfficialDeviceHost({tools: f.tools, deniedThreadIds: []});
  const made = await h.createWork({creationKey: 'synthetic', createArguments: {title: 'fixture', prompt: 'offline only'}});
  assert.equal(made.status, 'OFFICIAL_CREATE_REFERENCE_RETAINED');
  assert.equal(h.prepareDesktop({referenceHandle: made.referenceHandle, requestJson}).targetRoute, 'cloud-task');
  assert.equal(f.calls(), 1); // Only the synthetic callback above, never a real tool.
});
