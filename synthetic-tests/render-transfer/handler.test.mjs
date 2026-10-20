import test from 'node:test';
import assert from 'node:assert/strict';
import { handle, FIXTURE_TEXT, MAX_BYTES } from './handler.mjs';
const valid = { method: 'POST', path: '/transfer-check', contentType: 'text/plain', body: Buffer.from(FIXTURE_TEXT) };

test('exact synthetic bytes accepted with known digest', () => {
  const result = handle(valid);
  assert.equal(result.status, 200);
  assert.equal(result.data.bytes, 29);
  assert.equal(result.data.sha256, '0bd3cb21b5c6da909de4cd0bfa000114e33a25e7df53c3c6bb6a53fdd6565794');
  assert.deepEqual(Object.keys(result.data).sort(), ['bytes', 'mode', 'sha256']);
});
test('health check', () => assert.equal(handle({method:'GET',path:'/healthz'}).status, 200));
test('unknown route rejected', () => assert.equal(handle({...valid,path:'/dispatch'}).status, 404));
test('query variant rejected', () => assert.equal(handle({...valid,path:'/transfer-check?url=x'}).status, 404));
test('wrong method rejected', () => assert.equal(handle({...valid,method:'PUT'}).status, 405));
test('wrong content type rejected', () => assert.equal(handle({...valid,contentType:'application/json'}).status, 415));
test('UTF-8 content type accepted', () => assert.equal(handle({...valid,contentType:'text/plain; charset=utf-8'}).status, 200));
test('empty body rejected', () => assert.equal(handle({...valid,body:Buffer.alloc(0)}).status, 422));
test('other text rejected without echo', () => {
  const r=handle({...valid,body:Buffer.from('unapproved-data')});
  assert.equal(r.status,422); assert.ok(!JSON.stringify(r).includes('unapproved-data'));
});
test('1KiB unapproved body rejected', () => assert.equal(handle({...valid,body:Buffer.alloc(MAX_BYTES)}).status, 422));
test('over 1KiB rejected', () => assert.equal(handle({...valid,body:Buffer.alloc(MAX_BYTES+1)}).status, 413));
test('non-buffer rejected', () => assert.equal(handle({...valid,body:'text'}).status, 400));
test('wrong final newline rejected', () => assert.equal(handle({...valid,body:Buffer.from(FIXTURE_TEXT.trim())}).status, 422));
