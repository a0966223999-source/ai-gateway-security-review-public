import test from 'node:test';
import assert from 'node:assert/strict';
import {REQUEST_TOOL, canonicalTargetLocator, targetLocatorFromOfficialReference,
  submitTargetedRegistration} from '../functions_producer_v2.mjs';

const requestJson = '{"id":"' + 'a'.repeat(64) + '","method":"register","params":{},"source":"fixture","v":1}';
const locator = {v: 2, kind: 'ordinary-work', threadId: 'official-thread-fixture', environmentId: 'official-environment-fixture'};

function harness(result = {status: 'verified_host_response'}) {
  const calls = [];
  return {calls, tools: {[REQUEST_TOOL]: async args => { calls.push(args); return result; }}};
}

test('missing locator and environment reject before any MCP invocation', async () => {
  const {calls, tools} = harness();
  await assert.rejects(submitTargetedRegistration({tools, requestJson}), /LOCATOR_REQUIRED/);
  for (const environmentId of [undefined, null, '', ' ']) {
    await assert.rejects(submitTargetedRegistration({tools, requestJson,
      targetLocator: {...locator, environmentId}}), /ENVIRONMENT_REFERENCE_REQUIRED/);
  }
  assert.equal(calls.length, 0);
});

test('verified reference fields are preserved and never invented', () => {
  assert.deepEqual(targetLocatorFromOfficialReference(locator), locator);
  assert.throws(() => targetLocatorFromOfficialReference(), /LOCATOR_REQUIRED/);
  assert.throws(() => targetLocatorFromOfficialReference({threadId: 'thread'}), /ENVIRONMENT_REFERENCE_REQUIRED/);
  assert.throws(() => targetLocatorFromOfficialReference({environmentId: 'environment'}), /LOCATOR_REQUIRED/);
});

test('one official call preserves request bytes, separate canonical locator, no extra fields', async () => {
  const {calls, tools} = harness();
  await submitTargetedRegistration({tools, requestJson, targetLocator: locator});
  assert.deepEqual(calls, [{mailbox_id: 'gateway_internal_v1', request_json: requestJson,
    target_locator_json: '{"environmentId":"official-environment-fixture","kind":"ordinary-work","threadId":"official-thread-fixture","v":2}'}]);
});

test('UNKNOWN_QUERY_ONLY returns unchanged with no retry', async () => {
  const result = {status: 'UNKNOWN_QUERY_ONLY', request_id: 'a'.repeat(64)};
  const {calls, tools} = harness(result);
  assert.equal(await submitTargetedRegistration({tools, requestJson, targetLocator: locator}), result);
  assert.equal(calls.length, 1);
});

test('uncertain failure is not retried', async () => {
  let calls = 0;
  const tools = {[REQUEST_TOOL]: async () => { calls++; throw new Error('connection lost'); }};
  await assert.rejects(submitTargetedRegistration({tools, requestJson, targetLocator: locator}), /connection lost/);
  assert.equal(calls, 1);
});

test('invalid scope and identifiers reject before invocation', async () => {
  const {calls, tools} = harness();
  for (const targetLocator of [{...locator, extra: 1}, {...locator, v: true}, {...locator, kind: 'other'},
    {...locator, threadId: 'x'.repeat(257)}, {...locator, threadId: '\ud800'},
    {...locator, threadId: '中'.repeat(86)}, {...locator, environmentId: 'line\nbreak'}]) {
    await assert.rejects(submitTargetedRegistration({tools, requestJson, targetLocator}));
  }
  for (const method of ['save', 'read']) {
    await assert.rejects(submitTargetedRegistration({tools,
      requestJson: requestJson.replace('register', method), targetLocator: locator}), /REGISTER_ONLY/);
  }
  assert.equal(calls.length, 0);
  assert.equal(JSON.parse(canonicalTargetLocator({...locator, threadId: '中'.repeat(85)})).threadId.length, 85);
});
