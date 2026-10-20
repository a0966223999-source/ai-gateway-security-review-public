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
  console.log(JSON.stringify({ event: 'request_handler_entered' }));
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
// Summarize only the existing parser fragment; never retain or emit its values.
function summarizeHeaderFragment(packet) {
  const LIMIT = 64 * 1024;
  const result = { fragmentBytes: Buffer.isBuffer(packet) ? packet.length : 0,
    inspectedBytes: 0, coverage: 'unknown_start', headerTerminatorSeen: false,
    incompleteLineBytesLowerBound: 0, groups: [] };
  if (!Buffer.isBuffer(packet)) return result;
  const data = packet.subarray(0, LIMIT);
  result.inspectedBytes = data.length;
  const firstEnd = data.indexOf('\r\n');
  if (firstEnd < 0 || firstEnd > 8192) return result;
  // Without a complete request line, do not guess boundaries inside values.
  if (!/^(GET|HEAD|POST|PUT|DELETE|OPTIONS|PATCH|CONNECT|TRACE) [^ \r\n]+ HTTP\/1\.[01]$/.test(data.subarray(0, firstEnd).toString('latin1'))) return result;
  result.coverage = 'request_line_anchored_fragment';
  const allow = new Set(['host', 'content-type', 'content-length', 'user-agent',
    'accept', 'accept-encoding', 'accept-language', 'cookie', 'authorization',
    'proxy-authorization', 'connection', 'transfer-encoding', 'expect',
    'forwarded', 'x-forwarded-for', 'x-forwarded-host', 'x-forwarded-proto',
    'x-request-id', 'traceparent', 'tracestate', 'baggage', 'x-amzn-trace-id',
    'cf-ray', 'cf-connecting-ip', 'cf-visitor', 'x-render-origin-server',
    'x-envoy-original-path', 'x-envoy-external-address', 'x-envoy-attempt-count',
    'via', 'referer']); // At most 31 allowlisted groups, plus 'other'.
  const groups = new Map();
  let cursor = firstEnd + 2;
  while (cursor < data.length) {
    const end = data.indexOf('\r\n', cursor);
    const complete = end !== -1;
    const lineEnd = complete ? end : data.length;
    if (lineEnd === cursor && complete) { result.headerTerminatorSeen = true; break; }
    const colon = data.indexOf(58, cursor);
    if (colon <= cursor || colon >= lineEnd) {
      result.incompleteLineBytesLowerBound = lineEnd - cursor;
      break;
    }
    // Validate only the field name bytes, never decode a field value.
    let valid = true;
    for (let i = cursor; i < colon; i++) {
      const c = data[i];
      if (!((c >= 48 && c <= 57) || (c >= 65 && c <= 90) ||
        (c >= 97 && c <= 122) || [33,35,36,37,38,39,42,43,45,46,94,95,96,124,126].includes(c))) { valid = false; break; }
    }
    if (!valid) { result.coverage = 'request_line_anchored_partial'; break; }
    const candidate = colon - cursor <= 64 ? data.subarray(cursor, colon).toString('ascii').toLowerCase() : '';
    const name = allow.has(candidate) ? candidate : 'other';
    if (!groups.has(name)) groups.set(name, { name, completeCount: 0,
      partialCount: 0, completeValueBytes: 0, partialValueBytesLowerBound: 0 });
    const group = groups.get(name);
    // Bytes after colon, including optional whitespace; exclude line CRLF.
    const valueBytes = lineEnd - colon - 1;
    if (complete) { group.completeCount++; group.completeValueBytes += valueBytes; }
    else { group.partialCount++; group.partialValueBytesLowerBound += valueBytes; }
    if (!complete) break;
    cursor = end + 2;
  }
  result.groups = [...groups.values()];
  return result;
}
// Diagnostic codes only: never log error messages, raw packets, or request data.
// A clientError listener replaces Node's default handling, so preserve its
// response mapping, avoid a second response, and always destroy the socket.
server.on('clientError', (error, socket) => {
  const code = typeof error.code === 'string' && /^[A-Z0-9_]{1,64}$/.test(error.code)
    ? error.code : 'UNKNOWN';
  const diagnostic = { event: 'client_error', code };
  if (code === 'HPE_HEADER_OVERFLOW') diagnostic.headerFragment = summarizeHeaderFragment(error.rawPacket);
  console.log(JSON.stringify(diagnostic));
  try {
    if (socket.writable && (!socket._httpMessage || !socket._httpMessage._headerSent)) {
      const status = code === 'HPE_HEADER_OVERFLOW' ? 431
        : code === 'HPE_CHUNK_EXTENSIONS_OVERFLOW' ? 413
        : code === 'ERR_HTTP_REQUEST_TIMEOUT' ? 408 : 400;
      socket.write(`HTTP/1.1 ${status} ${http.STATUS_CODES[status]}\r\nConnection: close\r\n\r\n`);
    }
  } finally {
    socket.destroy(error);
  }
});
server.requestTimeout = 10_000;
server.headersTimeout = 5_000;
server.timeout = 10_000;
server.maxHeadersCount = 20;
server.maxConnections = 8;
server.listen(port, '0.0.0.0');
process.on('SIGTERM', () => server.close(() => process.exit(0)));
