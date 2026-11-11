import test from 'node:test';
import assert from 'node:assert/strict';
import {REQUEST_TOOL, prepareTargetedRegistration, prepareWorkSelfReport, submitTargetedRegistration}
  from '../functions_producer_v2.mjs';

const requestJson = JSON.stringify({id: 'a'.repeat(64), method: 'register',
  params: {}, source: 'b'.repeat(64), v: 1});

function fixture() {
  const calls = [];
  const tools = {[REQUEST_TOOL]: async arguments_ => {
    calls.push(arguments_);
    return {status: 'UNKNOWN_QUERY_ONLY'};
  }};
  return {calls, options: {tools, requestJson, referenceKind: 'cloud-task',
    officialReference: {threadId: 'official-task-fixture', turnId: 'initial-turn-fixture'}}};
}

test('both missing inputs are visible together and no MCP invocation occurs', () => {
  const {calls, options} = fixture();
  const outcome = prepareTargetedRegistration({tools: options.tools});
  assert.equal(outcome.status, 'PREPARATION_BLOCKED');
  assert.deepEqual(outcome.issues, [
    {input: 'officialReference', code: 'LOCATOR_REQUIRED'},
    {input: 'requestJson', code: 'REQUEST_JSON_REQUIRED'},
  ]);
  assert.equal(outcome.producerArguments, null);
  assert.equal(calls.length, 0);
});

test('no inputs reports all applicable missing input categories', () => {
  assert.deepEqual(prepareTargetedRegistration().issues.map(issue => issue.code),
    ['LOCATOR_REQUIRED', 'REQUEST_JSON_REQUIRED', 'OFFICIAL_REQUEST_TOOL_REQUIRED']);
});

test('preparation never infers official route from runtime IDs or missing environment', () => {
  const {calls, options} = fixture();
  for (const referenceKind of [undefined, null, '', 'auto']) {
    const result = prepareTargetedRegistration({...options, referenceKind});
    assert.deepEqual(result.issues.map(issue => issue.code), ['OFFICIAL_REFERENCE_KIND_REQUIRED']);
  }
  assert.equal(prepareTargetedRegistration({...options,
    officialReference: {CODEX_THREAD_ID: 'runtime-thread', CODEX_ENVIRONMENT_ID: 'runtime-env'}})
    .issues[0].code, 'LOCATOR_REQUIRED');
  assert.equal(prepareTargetedRegistration({...options, referenceKind: 'native-chat'})
    .issues[0].code, 'ENVIRONMENT_REFERENCE_REQUIRED');
  assert.equal(calls.length, 0);
});

test('cloud and native preparation preserve explicit exact addresses without reading', () => {
  const {calls, options} = fixture();
  for (const referenceKind of ['cloud-task', 'native-chat']) {
    const officialReference = {threadId: 'official-target-fixture',
      environmentId: 'official-computer-fixture', turnId: 'official-turn-fixture'};
    const result = prepareTargetedRegistration({...options, referenceKind, officialReference});
    assert.equal(result.status, 'INPUTS_PREPARED_NOT_AUTHORIZED');
    const expected = {v: 3, kind: 'ordinary-work', route: referenceKind,
      threadId: officialReference.threadId};
    if (referenceKind === 'native-chat') expected.environmentId = officialReference.environmentId;
    assert.deepEqual(result.producerArguments.targetLocator, expected);
    assert.equal(result.producerArguments.requestJson, requestJson);
    assert.equal(result.officialReferenceProvenance, 'NOT_VERIFIED');
    assert.equal(result.hostVerification, 'NOT_RUN');
    assert.equal(result.toolSchema, 'NOT_VERIFIED');
    assert.equal(result.registration, 'NOT_RUN');
  }
  assert.equal(calls.length, 0);
});

test('malformed or noncanonical original request is reported without rewriting it', () => {
  const {calls, options} = fixture();
  const invalid = [null, '', '{', 'x'.repeat(65537), JSON.stringify({method: 'register'}),
    requestJson.replace('register', 'save'), requestJson.replace('"params":{}', '"params":[]'),
    requestJson.replace('"v":1', '"v":true'), requestJson.replace('a'.repeat(64), 'a'.repeat(63)),
    requestJson.replace('b'.repeat(64), 'B'.repeat(64)), ` ${requestJson}`,
    requestJson.replace('"v":1}', '"v":1,"v":1}'),
    requestJson.replace('"params":{}', '"params":{"scope":"invented"}'),
    requestJson.replace('"v":1}', '"v":1,"targetLocator":{}}')];
  for (const original of invalid) {
    const input = {...options, requestJson: original};
    const outcome = prepareTargetedRegistration(input);
    assert.equal(outcome.status, 'PREPARATION_BLOCKED', String(original));
    assert.equal(outcome.issues.some(issue => issue.input === 'requestJson'), true);
    assert.equal(input.requestJson, original);
  }
  assert.equal(calls.length, 0);
});

test('blocked preparation accumulates independent locator/request/tool problems', () => {
  const result = prepareTargetedRegistration({referenceKind: 'native-chat',
    officialReference: {threadId: 'fixture'}, requestJson: '{', tools: {}});
  assert.deepEqual(result.issues.map(issue => issue.code),
    ['ENVIRONMENT_REFERENCE_REQUIRED', 'REQUEST_JSON_REQUIRED', 'OFFICIAL_REQUEST_TOOL_REQUIRED']);
});

test('prepared arguments are detached and immutable, with no plan or authorization minted', () => {
  const {calls, options} = fixture();
  const outcome = prepareTargetedRegistration(options);
  options.officialReference.threadId = 'changed-after-prepare';
  options.requestJson = 'changed-after-prepare';
  assert.equal(outcome.producerArguments.targetLocator.threadId, 'official-task-fixture');
  assert.equal(outcome.producerArguments.requestJson, requestJson);
  assert.throws(() => { outcome.producerArguments.targetLocator.route = 'native-chat'; }, TypeError);
  assert.throws(() => { outcome.status = 'READY'; }, TypeError);
  assert.deepEqual(Object.keys(outcome.producerArguments).sort(), ['requestJson', 'targetLocator']);
  assert.equal(calls.length, 0);
});

test('only an explicit subsequent submission makes a call and UNKNOWN remains query-only', async () => {
  const {calls, options} = fixture();
  const preparation = prepareTargetedRegistration(options);
  assert.equal(calls.length, 0);
  const outcome = await submitTargetedRegistration({tools: options.tools,
    ...preparation.producerArguments});
  assert.deepEqual(outcome, {status: 'UNKNOWN_QUERY_ONLY'});
  assert.equal(calls.length, 1);
  assert.equal(calls[0].request_json, requestJson);
  assert.equal(JSON.parse(calls[0].target_locator_json).route, 'cloud-task');
});

test('Work self-report retains only allowed observations and unchanged request bytes', () => {
  const original = {CODEX_THREAD_ID: 'official-task-fixture', CODEX_ENVIRONMENT_ID: 'runtime-env',
    SECRET: 'must-not-copy', route: 'native-chat', user: 'not-authority'};
  const result = prepareWorkSelfReport({runtimeIdentifiers: original, requestJson});
  assert.equal(result.status, 'SELF_REPORT_PREPARED_UNVERIFIED');
  assert.deepEqual(result.selfReport.observations, {
    CODEX_THREAD_ID: original.CODEX_THREAD_ID, CODEX_ENVIRONMENT_ID: original.CODEX_ENVIRONMENT_ID});
  assert.equal(result.requestJson, requestJson);
  assert.equal(result.route, 'UNRESOLVED');
  assert.equal(result.identity, 'NOT_VERIFIED');
  assert.equal(result.authorization, 'NOT_VERIFIED');
  assert.equal(result.registration, 'NOT_RUN');
  original.CODEX_THREAD_ID = 'changed';
  assert.equal(result.selfReport.observations.CODEX_THREAD_ID, 'official-task-fixture');
});

test('self-report alone cannot create locator or invoke a tool', () => {
  const {calls, options} = fixture();
  const report = prepareWorkSelfReport({runtimeIdentifiers: {CODEX_THREAD_ID: 'reported-work'},
    requestJson});
  const result = prepareTargetedRegistration({tools: options.tools, requestJson,
    selfReport: report.selfReport});
  assert.equal(result.status, 'PREPARATION_BLOCKED');
  assert.equal(result.issues[0].code, 'LOCATOR_REQUIRED');
  assert.equal(result.producerArguments, null);
  assert.equal(calls.length, 0);
});

test('official target must match self-report and runtime environment never becomes a locator', () => {
  const {calls, options} = fixture();
  const report = prepareWorkSelfReport({runtimeIdentifiers: {CODEX_THREAD_ID: 'official-task-fixture',
    CODEX_ENVIRONMENT_ID: 'runtime-env'}, requestJson});
  const result = prepareTargetedRegistration({...options, selfReport: report.selfReport});
  assert.equal(result.status, 'INPUTS_PREPARED_NOT_AUTHORIZED');
  assert.equal(Object.hasOwn(result.producerArguments.targetLocator, 'environmentId'), false);
  const wrong = prepareTargetedRegistration({...options, selfReport: report.selfReport,
    officialReference: {threadId: 'another-work'}});
  assert.equal(wrong.status, 'PREPARATION_BLOCKED');
  assert.equal(wrong.issues[0].code, 'SELF_REPORT_OFFICIAL_TARGET_MISMATCH');
  assert.equal(calls.length, 0);
});

test('malformed self-reports never override official reference or grant authority', () => {
  const {calls, options} = fixture();
  for (const selfReport of [null, [], {}, {v: 1, kind: 'work-runtime-hints', observations: {}},
    {v: 1, kind: 'work-runtime-hints', observations: {CODEX_THREAD_ID: 'bad\nidentifier'}},
    {v: 1, kind: 'work-runtime-hints', observations: {CODEX_THREAD_ID: 'fixture', grant: true}},
    {v: 1, kind: 'work-runtime-hints', observations: {CODEX_THREAD_ID: 'fixture'}, authorized: true}]) {
    const outcome = prepareTargetedRegistration({...options, selfReport});
    assert.equal(outcome.status, 'PREPARATION_BLOCKED');
    assert.equal(outcome.producerArguments, null);
    assert.equal(outcome.issues.some(issue => issue.input === 'selfReport'), true);
  }
  assert.equal(calls.length, 0);
});

test('self-report missing thread and request returns both input issues without substitution', () => {
  assert.deepEqual(prepareWorkSelfReport({runtimeIdentifiers: {}}).issues.map(issue => issue.code),
    ['RUNTIME_THREAD_HINT_REQUIRED', 'REQUEST_JSON_REQUIRED']);
});

test('invalid optional environment cannot become a usable partial self-report', () => {
  const {calls, options} = fixture();
  const report = prepareWorkSelfReport({runtimeIdentifiers: {
    CODEX_THREAD_ID: 'official-task-fixture', CODEX_ENVIRONMENT_ID: 'bad\nenvironment'}, requestJson});
  assert.equal(report.status, 'SELF_REPORT_INPUTS_MISSING');
  assert.equal(report.selfReport, null);
  assert.equal(report.requestJson, null);
  const chained = prepareTargetedRegistration({...options,
    requestJson: report.requestJson, selfReport: report.selfReport});
  assert.equal(chained.status, 'PREPARATION_BLOCKED');
  assert.deepEqual(chained.issues.map(issue => issue.code),
    ['SELF_REPORT_SCHEMA', 'REQUEST_JSON_REQUIRED']);
  // Even a caller that incorrectly passes the original request remains blocked.
  assert.equal(prepareTargetedRegistration({...options, selfReport: report.selfReport}).status,
    'PREPARATION_BLOCKED');
  assert.equal(calls.length, 0);
});

test('new preparation exports report null and non-object options without throwing', () => {
  for (const options of [null, false, 0, 'bad', [], () => {}]) {
    const report = prepareWorkSelfReport(options);
    assert.equal(report.status, 'SELF_REPORT_INPUTS_MISSING');
    assert.equal(report.issues[0].code, 'OPTIONS_OBJECT_REQUIRED');
    assert.equal(report.selfReport, null);
    const prepared = prepareTargetedRegistration(options);
    assert.equal(prepared.status, 'PREPARATION_BLOCKED');
    assert.equal(prepared.issues[0].code, 'OPTIONS_OBJECT_REQUIRED');
    assert.equal(prepared.producerArguments, null);
  }
});
