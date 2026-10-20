import { createHash } from 'node:crypto';

export const MAX_BYTES = 1024;
export const FIXTURE_TEXT = 'render-transfer-synthetic-v1\n';
const FIXTURE = Buffer.from(FIXTURE_TEXT, 'utf8');

// Pure handler: no filesystem, networking, environment access, or logging.
export function handle({ method, path, contentType = '', body = Buffer.alloc(0) }) {
  if (path === '/healthz' && method === 'GET') {
    return { status: 200, data: { ok: true, mode: 'synthetic-transfer-only' } };
  }
  if (path !== '/transfer-check') return { status: 404, data: { error: 'not_found' } };
  if (method !== 'POST') return { status: 405, data: { error: 'method_not_allowed' } };
  if (!Buffer.isBuffer(body)) return { status: 400, data: { error: 'invalid_body' } };
  if (body.length > MAX_BYTES) return { status: 413, data: { error: 'too_large' } };
  if (!/^text\/plain(?:;\s*charset=utf-8)?$/i.test(contentType)) {
    return { status: 415, data: { error: 'unsupported_content_type' } };
  }
  if (!body.equals(FIXTURE)) return { status: 422, data: { error: 'synthetic_fixture_required' } };
  return {
    status: 200,
    data: { mode: 'synthetic-transfer-only', bytes: body.length,
      sha256: createHash('sha256').update(body).digest('hex') },
  };
}
