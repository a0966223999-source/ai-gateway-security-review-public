import {types} from 'node:util';
// Node-only descriptor parser. Reject Proxy BEFORE reflection; never invoke accessors.
export function dataRecord(x, fields) {
  if (!x || typeof x !== 'object' || types.isProxy(x)) return null;
  try {
    if (Object.getPrototypeOf(x) !== Object.prototype) return null;
    const ds = Object.getOwnPropertyDescriptors(x), keys = Reflect.ownKeys(ds);
    if (keys.length !== fields.length || keys.some(k => typeof k !== 'string' || !fields.includes(k))) return null;
    const copy = Object.create(null);
    for (const k of fields) {
      const d = ds[k];
      if (!d || !Object.hasOwn(d, 'value') || !d.enumerable) return null;
      copy[k] = d.value;
    }
    return copy;
  } catch { return null; }
}
const fields = ['model','protocol','requestId','tenantRef'];
export function snapshotRequest(x) {
  const r = dataRecord(x, fields);
  if (!r || !['chat','responses','messages'].includes(r.protocol)) return null;
  if (['model','requestId','tenantRef'].some(k => typeof r[k] !== 'string' || !/^[a-zA-Z0-9._-]{1,80}$/.test(r[k]))) return null;
  return Object.freeze({...r});
}
export function validateRequest(x) { return snapshotRequest(x) ? {ok:true} : {ok:false,code:'INVALID_REQUEST'}; }
export function snapshotUsage(x) {
  const u = dataRecord(x, ['tokens']);
  return u && Number.isSafeInteger(u.tokens) && u.tokens >= 0 ? Object.freeze({tokens:u.tokens}) : null;
}
const denied = code => Object.freeze({status:'DENIED',code,usageVerified:false});
// Production entrypoint is DENY-only pending the existing Gate/Facade/Broker.
// Never accepts a caller-supplied verified flag, synthetic grant, or receipt.
export function executeMock(x) {
  const a = dataRecord(x, ['request','grant','transport']);
  return denied(a && snapshotRequest(a.request) ? 'AUTH_DENIED' : 'INVALID_REQUEST');
}
// Offline fixture ONLY, not an identity authority or proof of real billing.
export function createOfflineHarness() {
  const grants = new WeakMap(), receipts = new WeakMap();
  function issue(tenantRef) {
    if (typeof tenantRef !== 'string' || !/^[a-zA-Z0-9._-]{1,80}$/.test(tenantRef)) return null;
    const key = Object.freeze({}); grants.set(key, tenantRef); return key;
  }
  function attest(input) {
    const r = dataRecord(input, ['requestId','status','usage','partial']);
    if (!r || typeof r.requestId !== 'string' || !Number.isInteger(r.status) || r.status < 100 || r.status > 599 || typeof r.partial !== 'boolean') return null;
    const key = Object.freeze({});
    receipts.set(key, Object.freeze({requestId:r.requestId,status:r.status,partial:r.partial,usage:snapshotUsage(r.usage)}));
    return key;
  }
  function execute(input) {
    const a = dataRecord(input, ['request','grant','transport']);
    const r = a && snapshotRequest(a.request);
    if (!r) return denied('INVALID_REQUEST');
    if (!a.grant || grants.get(a.grant) !== r.tenantRef) return denied('AUTH_DENIED');
    if (typeof a.transport !== 'function' || types.isProxy(a.transport)) return denied('NO_TRANSPORT');
    let proof;
    try { proof = a.transport(Object.freeze({protocol:r.protocol,model:r.model,requestId:r.requestId})); }
    catch { return {status:'UNKNOWN',code:'TRANSPORT_UNKNOWN',attempts:1,usageVerified:false}; }
    const response = receipts.get(proof);
    const unknown = code => ({status:'UNKNOWN',code,attempts:1,usageVerified:false});
    if (!response) return unknown('UNTRUSTED_RECEIPT');
    if (response.requestId !== r.requestId) return unknown('RECEIPT_MISMATCH');
    if (response.status !== 200) return {...denied('UPSTREAM_ERROR'),attempts:1};
    if (response.partial) return unknown('PARTIAL');
    if (!response.usage) return unknown('UNVERIFIED_USAGE');
    return {status:'MOCK_ACCEPTED',code:'MOCK_ONLY',attempts:1,usageVerified:false,usage:{...response.usage}};
  }
  return Object.freeze({issue,attest,execute});
}
