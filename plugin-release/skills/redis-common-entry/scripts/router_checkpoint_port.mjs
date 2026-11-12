/** Original MCP checkpoint port for the current Work's Router continuation seam.
 * No service, timer, Source initializer, native caller, grant or ACK fabrication.
 * A checkpoint observation must never be passed as S61 native-start/completion proof.
 */
const names = Object.freeze({
  read: 'mcp__codex_apps__x_redis_test_gateway_checkpoint_read',
  save: 'mcp__codex_apps__x_redis_test_gateway_checkpoint_save',
  receipt: 'mcp__codex_apps__x_redis_test_gateway_checkpoint_receipt',
});
const hex = /^[a-f0-9]{64}$/;
const key = /^[A-Za-z0-9_.:-]{1,128}$/;
// MCP payloads are JSON. Also works in Work's V8 runtime without structuredClone.
const clone = value => JSON.parse(JSON.stringify(value, (_key,entry) => {
  if (['undefined','function','symbol','bigint'].includes(typeof entry)
      || (typeof entry==='number' && !Number.isFinite(entry))) fail('CHECKPOINT_JSON_REQUIRED');
  return entry;
}));
const fail = code => { throw Error(code); };
const freeze = value => {
  if (value && typeof value === 'object') {
    Object.values(value).forEach(freeze);
    Object.freeze(value);
  }
  return value;
};
const same = (a,b) => {
  if (a === b) return true;
  if (!a || !b || typeof a !== 'object' || typeof b !== 'object'
      || Array.isArray(a) !== Array.isArray(b)) return false;
  const keys = Object.keys(a);
  return keys.length === Object.keys(b).length
    && keys.every(k => Object.hasOwn(b,k) && same(a[k],b[k]));
};
function decode(reply) {
  if (reply?.isError) return null;
  let value = reply;
  for (let i=0;i<8;i++) {
    if (typeof value === 'string') {
      try { value=JSON.parse(value); } catch { return null; }
    } else if (value?.structuredContent != null) {
      value=value.structuredContent;
    } else if (Array.isArray(value?.content)) {
      const texts=value.content.filter(x=>x.type==='text');
      if(texts.length!==1) return null;
      value=texts[0].text;
    } else return value;
  }
  return null;
}

export function createRouterCheckpointPort({tools,scope,clock=()=>Date.now()/1000}={}) {
  // Scope constrains existing server authorization; it does not create it.
  if (!scope || Object.keys(scope).sort().join(',')!=='link_id,thread_id'
      || typeof scope.link_id!=='string' || !hex.test(scope.link_id)
      || typeof scope.thread_id!=='string' || !/^[A-Za-z0-9_-]{1,64}$/.test(scope.thread_id)
      || typeof clock!=='function') fail('ROUTER_CHECKPOINT_SCOPE_REQUIRED');
  for (const name of Object.values(names)) {
    if (typeof tools?.[name]!=='function') fail('TOOL_NOT_FOUND:'+name);
  }
  const bound=freeze(clone(scope));
  const ports=Object.fromEntries(Object.entries(names).map(([k,n])=>[k,tools[n]]));
  const issued=new WeakMap(), attempted=new Set();
  function operation(operationKey) {
    if(typeof operationKey!=='string'||!key.test(operationKey)) fail('OPERATION_KEY_REQUIRED');
  }
  function result(reply,sourceKey) {
    const value=decode(reply);
    if (!value || value.historical_only===true || value.authority===false
        || value.status!=='CHECKPOINT_RESULT_VERIFIED' || !Object.hasOwn(value,'checkpoint')) return null;
    if (value.scope!=='platform-checkpoint-only' || value.native_work_authority!==false
        || value.source_key!==sourceKey || !hex.test(value.revision)
        || !value.checkpoint || typeof value.checkpoint!=='object' || Array.isArray(value.checkpoint)
        || typeof value.expires_at!=='number' || !Number.isFinite(value.expires_at)
        || value.expires_at<=clock()) fail('CHECKPOINT_LIVE_SCOPE_OR_CONTENT_INVALID');
    return value;
  }
  async function invoke(kind,request) {
    try { return await ports[kind]({request}); }
    catch { return null; } // Effect/transport failures are unknown, never retried here.
  }
  function pending(operationKey,kind,raw) {
    return freeze({status:'QUERY_ORIGINAL_ONLY',operation_key:operationKey,
      operation_kind:kind,dispatch_allowed:false,native_completion_verified:false,
      raw_reply:clone(raw)});
  }
  function observe(value) {
    const observation=freeze({status:'CHECKPOINT_READY',source_key:value.source_key,
      revision:value.revision,checkpoint:clone(value.checkpoint),expires_at:value.expires_at,
      dispatch_allowed:false,native_completion_verified:false,transport_ack_allowed:false});
    issued.set(observation,clone(value));
    return observation;
  }
  async function read({operation_key,source_key}={}) {
    operation(operation_key);
    if(typeof source_key!=='string'||!hex.test(source_key)) fail('SOURCE_KEY_REQUIRED');
    // A repeated read key recovers the old snapshot; caller must use a NEW read key for freshness.
    const raw=await invoke('read',{...bound,operation_key,source_key});
    const value=result(raw,source_key);
    return value?observe(value):pending(operation_key,'read',raw);
  }
  async function save({operation_key,observation,checkpoint}={}) {
    operation(operation_key);
    const before=issued.get(observation);
    if(!before) fail('ISSUED_CHECKPOINT_OBSERVATION_REQUIRED');
    if(before.expires_at<=clock()) fail('CHECKPOINT_EXPIRED');
    if(!checkpoint||typeof checkpoint!=='object'||Array.isArray(checkpoint)) fail('CHECKPOINT_OBJECT_REQUIRED');
    if(attempted.has(operation_key)) return pending(operation_key,'execute',null);
    const expected=freeze(clone(checkpoint));
    const request={...bound,operation_key,source_key:before.source_key,
      expected_revision:before.revision,checkpoint:expected};
    attempted.add(operation_key); // Before first await; durable fence remains on original server.
    const raw=await invoke('save',request);
    const value=result(raw,before.source_key);
    if(!value) return pending(operation_key,'execute',raw);
    if(value.expires_at>before.expires_at || !same(value.checkpoint,expected)) fail('CHECKPOINT_SAVE_MISMATCH');
    return freeze({status:'CHECKPOINT_SAVE_RETURNED',operation_key,observation:observe(value),
      independent_read_required:true,dispatch_allowed:false,transport_ack_allowed:false});
  }
  async function confirm({saved,operation_key}={}) {
    if(saved?.status!=='CHECKPOINT_SAVE_RETURNED'||!issued.has(saved.observation)) fail('SAVED_OBSERVATION_REQUIRED');
    if(operation_key===saved.operation_key) fail('INDEPENDENT_READ_KEY_REQUIRED');
    const after=await read({operation_key,source_key:saved.observation.source_key});
    if(after.status!=='CHECKPOINT_READY') return after;
    if(after.revision!==saved.observation.revision || !same(after.checkpoint,saved.observation.checkpoint)) fail('CHECKPOINT_READBACK_MISMATCH');
    return freeze({status:'CHECKPOINT_RESULT_READBACK_VERIFIED',observation:after,
      checkpoint_result_saved:true,dispatch_allowed:false,native_completion_verified:false,
      // The original Receiver separately owns any lease/result fence and transport ACK.
      transport_ack_allowed:false,receiver_result_check_required:true});
  }
  async function query({operation_key,operation_kind}={}) {
    operation(operation_key);
    if(!['read','execute'].includes(operation_kind)) fail('OPERATION_KIND_REQUIRED');
    const raw=await invoke('receipt',{operation_key,operation_kind});
    // Historical receipts are audit only. Never hydrate a current observation from them.
    return pending(operation_key,operation_kind,raw);
  }
  return Object.freeze({read,save,confirm,query});
}

