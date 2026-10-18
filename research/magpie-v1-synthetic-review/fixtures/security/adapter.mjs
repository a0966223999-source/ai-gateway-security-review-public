// Offline synthetic adapter. A closure-private authority is NOT real authenticated IPC.
const own = (x,k) => Object.getOwnPropertyDescriptor(x,k);
function record(x, fields) {
  if (!x || Object.getPrototypeOf(x)!==Object.prototype) return false;
  const keys=Reflect.ownKeys(x);
  if(keys.length!==fields.length || keys.some(k=>typeof k!=='string'||!fields.includes(k))) return false;
  return fields.every(k=>{const d=own(x,k);return d&&Object.hasOwn(d,'value')&&d.enumerable});
}
export function validateRequest(x){
  try {
    if(!record(x,['model','protocol','requestId','tenantRef']))return {ok:false,code:'INVALID_REQUEST'};
    if(!['chat','responses','messages'].includes(own(x,'protocol').value))return {ok:false,code:'INVALID_PROTOCOL'};
    for(const k of ['model','requestId','tenantRef'])if(typeof own(x,k).value!=='string'|| !/^[a-zA-Z0-9._-]{1,80}$/.test(own(x,k).value))return {ok:false,code:'INVALID_REQUEST'};
    return {ok:true};
  }catch{return {ok:false,code:'INVALID_REQUEST'}}
}
export function createOfflineHarness(){
  const grants=new WeakMap(),receipts=new WeakMap();
  function issue(tenantRef){const grant=Object.freeze({});grants.set(grant,tenantRef);return grant}
  function attest(receipt){if(!record(receipt,['requestId','status','usage','partial']))return null;const x=Object.freeze({});receipts.set(x,receipt);return x}
  function execute({request,grant,transport}){
    const valid=validateRequest(request);if(!valid.ok)return {status:'DENIED',code:valid.code};
    if(!grant||grants.get(grant)!==own(request,'tenantRef').value)return {status:'DENIED',code:'AUTH_DENIED'};
    if(typeof transport!=='function')return {status:'DENIED',code:'NO_TRANSPORT'};
    let proof;try{proof=transport({protocol:own(request,'protocol').value,model:own(request,'model').value,requestId:own(request,'requestId').value})}catch{return {status:'UNKNOWN',code:'TRANSPORT_UNKNOWN',attempts:1}};
    const response=receipts.get(proof);if(!response)return {status:'UNKNOWN',code:'UNTRUSTED_RECEIPT',attempts:1};
    try{
      if(own(response,'requestId').value!==own(request,'requestId').value)return {status:'UNKNOWN',code:'RECEIPT_MISMATCH',attempts:1};
      if(own(response,'status').value!==200)return {status:'DENIED',code:'UPSTREAM_ERROR',attempts:1};
      if(own(response,'partial').value!==false)return {status:'UNKNOWN',code:'PARTIAL',attempts:1};
      const usage=own(response,'usage').value;
      if(!record(usage,['tokens'])||!Number.isSafeInteger(own(usage,'tokens').value)||own(usage,'tokens').value<0)return {status:'UNKNOWN',code:'UNVERIFIED_USAGE',attempts:1};
      return {status:'MOCK_ACCEPTED',code:'MOCK_ONLY',attempts:1,usage:{tokens:own(usage,'tokens').value}};
    }catch{return {status:'UNKNOWN',code:'UNVERIFIED_RECEIPT',attempts:1}}
  }
  return Object.freeze({issue,attest,execute});
}
// Backward-compatible entrypoint deliberately DENY-only: caller supplied verified flags cannot authorize.
export function executeMock({request}={}){const v=validateRequest(request);return {status:'DENIED',code:v.ok?'AUTH_DENIED':v.code}}
