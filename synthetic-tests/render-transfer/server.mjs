import http from 'node:http';
import { handle, MAX_BYTES } from './handler.mjs';

// PORT is the sole environment setting read. No secrets or external dependencies.
const port = Number(process.env.PORT ?? 10000);
if (!Number.isInteger(port) || port < 1 || port > 65535) throw new Error('invalid_port');

function reply(res, result) {
  res.writeHead(result.status, {
    'Content-Type': 'application/json; charset=utf-8',
    'Cache-Control': 'no-store',
    'X-Content-Type-Options': 'nosniff',
    'Connection': 'close',
  });
  res.end(JSON.stringify(result.data));
}
const server = http.createServer((req, res) => {
  // Reject other routes/methods before collecting any request body.
  if (req.url !== '/transfer-check' || req.method !== 'POST') {
    reply(res, handle({ method: req.method, path: req.url }));
    req.resume();
    return;
  }
  if (req.headers['content-encoding'] && req.headers['content-encoding'] !== 'identity') {
    reply(res, { status: 415, data: { error: 'encoding_not_supported' } });
    req.resume();
    return;
  }
  const contentType = req.headers['content-type'] ?? '';
  if (!/^text\/plain(?:;\s*charset=utf-8)?$/i.test(contentType)) {
    reply(res, { status: 415, data: { error: 'unsupported_content_type' } });
    req.resume();
    return;
  }
  if (Number(req.headers['content-length'] ?? 0) > MAX_BYTES) {
    reply(res, { status: 413, data: { error: 'too_large' } });
    req.resume();
    return;
  }
  let size = 0;
  let chunks = [];
  let finished = false;
  req.on('data', chunk => {
    if (finished) return;
    size += chunk.length;
    if (size > MAX_BYTES) {
      finished = true;
      chunks = [];
      reply(res, { status: 413, data: { error: 'too_large' } });
      return;
    }
    chunks.push(chunk);
  });
  req.on('end', () => {
    if (finished) return;
    finished = true;
    const body = Buffer.concat(chunks, size);
    chunks = [];
    reply(res, handle({ method: req.method, path: req.url, contentType, body }));
  });
  req.on('error', () => { chunks = []; finished = true; res.destroy(); });
});
server.requestTimeout = 10_000;
server.headersTimeout = 5_000;
server.timeout = 10_000;
server.maxHeadersCount = 20;
server.maxConnections = 8;
server.listen(port, '0.0.0.0');
process.on('SIGTERM', () => server.close(() => process.exit(0)));
