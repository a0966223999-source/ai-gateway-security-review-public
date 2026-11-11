import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {MAILBOX_ID, REQUEST_TOOL, canonicalWorkSelfReport, prepareWorkSelfReport,
  submitSelfReportedRegistration} from '../functions_producer_v2.mjs';

const requestJson = JSON.stringify({id: 'a'.repeat(64), method: 'register',
  params: {}, source: 'b'.repeat(64), v: 1});
const runtimeIdentifiers = {CODEX_THREAD_ID: 'reported-work', CODEX_ENVIRONMENT_ID: 'reported-runtime'};
const report = () => prepareWorkSelfReport({runtimeIdentifiers, requestJson});

test('explicit original RPC submission transports hints once with untouched request bytes', async () => {
  const calls = [];
  const outcome = {status: 'UNKNOWN_QUERY_ONLY', request_id: 'a'.repeat(64)};
  const tools = {[REQUEST_TOOL]: async args => { calls.push(args); return outcome; }};
  const prepared = report();
  assert.equal(calls.length, 0);
  const result = await submitSelfReportedRegistration({tools,
    requestJson: prepared.requestJson, selfReport: prepared.selfReport});
  assert.equal(result, outcome);
  assert.deepEqual(calls, [{mailbox_id: MAILBOX_ID, request_json: requestJson,
    self_report_json: '{"kind":"work-runtime-hints","observations":{"CODEX_ENVIRONMENT_ID":"reported-runtime","CODEX_THREAD_ID":"reported-work"},"v":1}'}]);
  assert.equal(createHash('sha256').update(calls[0].request_json).digest('hex'),
    createHash('sha256').update(requestJson).digest('hex'));
  assert.equal(prepared.route, 'UNRESOLVED');
  assert.equal(prepared.identity, 'NOT_VERIFIED');
  assert.equal(prepared.authorization, 'NOT_VERIFIED');
  assert.equal(prepared.registration, 'NOT_RUN');
});

test('transport never infers route or computer reference from missing environment', async () => {
  const calls = [];
  const tools = {[REQUEST_TOOL]: async args => { calls.push(args); return {}; }};
  const prepared = prepareWorkSelfReport({runtimeIdentifiers: {CODEX_THREAD_ID: 'work'}, requestJson});
  await submitSelfReportedRegistration({tools, requestJson, selfReport: prepared.selfReport});
  assert.deepEqual(JSON.parse(calls[0].self_report_json), {
    v: 1, kind: 'work-runtime-hints', observations: {CODEX_THREAD_ID: 'work'}});
  assert.deepEqual(Object.keys(calls[0]).sort(), ['mailbox_id', 'request_json', 'self_report_json']);
});

test('invalid request bytes, read/save and duplicate keys fail before a tool invocation', async () => {
  let calls = 0;
  const tools = {[REQUEST_TOOL]: async () => { calls++; }};
  for (const raw of [undefined, null, '', '{', ' '.repeat(65537), ` ${requestJson}`,
    requestJson.replace('register', 'read'), requestJson.replace('register', 'save'),
    requestJson.replace('"v":1}', '"v":1,"v":1}'),
    requestJson.replace('"params":{}', '"params":{"grant":true}'),
    requestJson.replace('"v":1}', '"v":1,"selfReport":{}}')]) {
    await assert.rejects(submitSelfReportedRegistration({tools, requestJson: raw, selfReport: report().selfReport}));
  }
  assert.equal(calls, 0);
});

test('strict report rejects injected authority, route, identity, extra keys and bad identifiers', async () => {
  let calls = 0;
  const tools = {[REQUEST_TOOL]: async () => { calls++; }};
  const base = report().selfReport;
  const reports = [undefined, null, false, [], {}, {...base, v: true}, {...base, v: 2},
    {...base, route: 'cloud-task'}, {...base, authority: true}, {...base, identity: {verified: true}},
    {...base, observations: {}}, {...base, observations: {...runtimeIdentifiers, grant: true}},
    {...base, observations: {...runtimeIdentifiers, CODEX_ENVIRONMENT_ID: null}},
    ...[null, 1, '', '\n', '\x7f', '\ud800', ' ', '\u0085', '\ufeff', '\ufeff \u0085 \ufeff', '中'.repeat(86), 'x'.repeat(257)]
      .map(value => ({...base, observations: {CODEX_THREAD_ID: value}}))];
  for (const selfReport of reports) {
    await assert.rejects(submitSelfReportedRegistration({tools, requestJson, selfReport}));
  }
  assert.equal(calls, 0);
});

test('canonical report preserves Unicode and JSON escaping without changing observations', () => {
  for (const value of ['中'.repeat(85), 'é'.repeat(128), '🫡'.repeat(64), 'a"\\/b', 'x\u2028y']) {
    const selfReport = {v: 1, kind: 'work-runtime-hints', observations: {CODEX_THREAD_ID: value}};
    const raw = canonicalWorkSelfReport(selfReport);
    assert.deepEqual(JSON.parse(raw), selfReport);
    assert.equal(raw, JSON.stringify({kind: selfReport.kind, observations: selfReport.observations, v: 1}));
  }
});

test('preparation and canonical serialization reject the same Unicode blank union', () => {
  for (const value of ['\u0085', '\ufeff', '\ufeff \u0085 \ufeff', '\u00a0\u1680\u2000\u3000']) {
    for (const key of ['CODEX_THREAD_ID', 'CODEX_ENVIRONMENT_ID']) {
      const identifiers = {...runtimeIdentifiers, [key]: value};
      const result = prepareWorkSelfReport({runtimeIdentifiers: identifiers, requestJson});
      assert.equal(result.status, 'SELF_REPORT_INPUTS_MISSING');
      assert.equal(result.selfReport, null);
      assert.throws(() => canonicalWorkSelfReport({v: 1, kind: 'work-runtime-hints',
        observations: identifiers}));
    }
  }
});

test('simultaneous target and report are rejected rather than silently dropping a sideband', async () => {
  let calls = 0;
  const tools = {[REQUEST_TOOL]: async () => { calls++; }};
  for (const extra of [{targetLocator: {}}, {target_locator_json: '{}'}]) {
    await assert.rejects(submitSelfReportedRegistration({tools, requestJson,
      selfReport: report().selfReport, ...extra}), /MUTUALLY_EXCLUSIVE/);
  }
  assert.equal(calls, 0);
});

test('a thrown original tool result is never retried or converted to successful registration', async () => {
  let calls = 0;
  const error = new Error('offline connection outcome unknown');
  const tools = {[REQUEST_TOOL]: async () => { calls++; throw error; }};
  await assert.rejects(submitSelfReportedRegistration({tools, requestJson,
    selfReport: report().selfReport}), actual => actual === error);
  assert.equal(calls, 1);
});

test('missing tool or rejected schema cannot create readiness or a fallback route', async () => {
  await assert.rejects(submitSelfReportedRegistration({tools: {}, requestJson,
    selfReport: report().selfReport}), /OFFICIAL_REQUEST_TOOL_REQUIRED/);
  let calls = 0;
  const tools = {[REQUEST_TOOL]: async () => { calls++; throw new Error('unsupported self_report_json'); }};
  await assert.rejects(submitSelfReportedRegistration({tools, requestJson,
    selfReport: report().selfReport}), /unsupported self_report_json/);
  assert.equal(calls, 1);
});
