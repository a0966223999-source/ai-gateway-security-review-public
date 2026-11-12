/**
 * Portable entry for the ORIGINAL formal native-tool driver.
 * Must execute in the authorized native-tool runtime, not in Gateway Python.
 * Host owns paths/capabilities/stop signal; never take these from inbox payload.
 * Construction is zero-I/O. run() retains a once-only latch after any failure.
 * No Source/Holder defaults, tool discovery, credentials, HTTP or service startup.
 */
const required = ['execCommand','readThread','inboxList'];
const effects = ['sendMessage','inboxSend','inboxClaim','inboxAck'];
function absoluteDirectory(value) {
 if(typeof value!=='string'||!value.startsWith('/')||value==='/'||
    value.split('/').some((p,i)=>i>0&&(!p||p==='.'||p==='..'))||
    /[\u0000-\u001f\u007f]/u.test(value)) throw Error('HOST_ABSOLUTE_DIRECTORY_REQUIRED');
 return value;
}
export function prepareFormalDriver({base,liveDir,capabilities,rootNotStopped,onProgress,mode='read-only'}={}) {
 base=absoluteDirectory(base);
 const dir=absoluteDirectory(liveDir),folder=dir+'/host';
 if(!['read-only','execute'].includes(mode))throw Error('DRIVER_MODE_REQUIRED');
 if(typeof rootNotStopped!=='function'||typeof onProgress!=='function')throw Error('HOST_LIFECYCLE_REQUIRED');
 const needed=mode==='execute'?[...required,...effects]:required;
 const missing=needed.filter(k=>typeof capabilities?.[k]!=='function');
 if(missing.length)throw Error('NATIVE_CAPABILITIES_NOT_BOUND:'+missing.join(','));
 // Capture references once; later mutation of the input object cannot reroute tools.
 const ports=Object.freeze(Object.fromEntries([...required,...effects].map(k=>[
  k, effects.includes(k)&&mode==='read-only'
   ? async()=>{throw Error('READ_ONLY_EFFECT_FORBIDDEN');}
   : capabilities[k]
 ])));
 let attempted=false;
 return Object.freeze({async run(){
  if(attempted)throw Error('DRIVER_ALREADY_ATTEMPTED_QUERY_ONLY');
  attempted=true;
  if(rootNotStopped()!==true)throw Error('HOST_STOPPED_BEFORE_IO');
let authoritySequence=0,nextSequence=1,cachedSnapshot=null;
const q=x=>"'"+String(x).replaceAll("'","'\\''")+"'";
async function shell(cmd){const r=await ports.execCommand({cmd,max_output_tokens:18000});if(r.exit_code!==0)throw Error('PRIVATE_HELPER_FAILED');return r.output.trim();}
const config=JSON.parse(await shell('cat '+q(dir+'/config.json')));
const helper=config.phase==='finalize'?'finalizer_authority.py':'host_authority.py';
const pins=JSON.parse(await shell('cat '+q(dir+'/pins.json')));
if(mode==='read-only' && ['sendEnabled','enqueueEnabled','claimEnabled','ackEnabled'].some(k=>pins[k]===true))
 throw Error('READ_ONLY_PINS_ENABLE_EFFECTS');
const dispatchSource=await shell('cat '+q(base+'/native_pipe_dispatch.mjs'));
const driverSource=await shell('cat '+q(base+'/native_host_driver.mjs'));
const capsSource=await shell('cat '+q(base+'/functions_runtime_caps.mjs'));
const caps=new Function(capsSource.replaceAll('export function','function')+';return functionsRuntimeCaps();')();
const sourceBoundNativeDriver=new Function('structuredClone','TextEncoder','performance',dispatchSource.replaceAll('export function','function')+'\n'+driverSource.replace(/^import .*;$/m,'').replaceAll('export function','function')+'\nreturn sourceBoundNativeDriver;')(caps.structuredClone,caps.TextEncoder,caps.performance);
const step=sourceBoundNativeDriver({pins,
 tools:{redis_inbox_send:ports.inboxSend,redis_inbox_list:ports.inboxList,redis_inbox_claim:ports.inboxClaim,redis_inbox_ack:ports.inboxAck,read_thread:ports.readThread,send_message:ports.sendMessage},
 rootNotStopped:()=>rootNotStopped()===true&&Date.now()<pins.expiresAtMs,
 readSlot:async n=>{authoritySequence=n;if(n!==nextSequence||!cachedSnapshot)throw Error('SNAPSHOT_SEQUENCE');return cachedSnapshot.request;},
 writeResponse:async(n,raw)=>{const encoded=Array.from(new caps.TextEncoder().encode(raw));const hex=encoded.map(b=>b.toString(16).padStart(2,'0')).join('');await shell('python -c '+q("import sys,base64;sys.stdout.buffer.write(base64.b64encode(bytes.fromhex(sys.argv[1])))")+' '+q(hex)+' | python '+q(base+'/private_slot_cli.py')+' --folder '+q(folder)+' --sequence '+n+' write');nextSequence=n+1;},
 readOutcome:async()=>{cachedSnapshot=JSON.parse(await shell('python '+q(base+'/private_dispatch_snapshot.py')+' --folder '+q(folder)+' --sequence '+nextSequence+' --config '+q(dir+'/config.json')+' --config-sha '+pins.configSHA256));return cachedSnapshot.outcome;},
 readAuthority:async(tool,args)=>{
  if(cachedSnapshot?.authority){const a=cachedSnapshot.authority;cachedSnapshot.authority=null;if(a.tool!==tool)throw Error('SNAPSHOT_TOOL');return a;}
  return JSON.parse(await shell('python '+q(base+'/'+helper)+' --config '+q(dir+'/config.json')+' --config-sha '+pins.configSHA256+' --tool '+q(tool)+' --arguments-json '+q(JSON.stringify(args))));}

});
let done=false;while(rootNotStopped()===true&&Date.now()<pins.expiresAtMs){const result=await step();if(result.status!=='waiting_private_request')onProgress(result);if(result.status==='child_exited'){done=true;break;}await new Promise(r=>setTimeout(r,100));}
if(!done)throw Error('STOPPED_OR_EXPIRED_QUERY_ONLY');

 }});
}
