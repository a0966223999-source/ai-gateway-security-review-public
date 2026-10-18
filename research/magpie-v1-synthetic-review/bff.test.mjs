import test from 'node:test';import assert from 'node:assert/strict';import http from 'node:http';
import {POST,GET} from './next/app/api/model/route.ts';
const live = new Set();
async function withServer(fn) {
 const s=http.createServer(async(req,res)=>{const r=req.method==='POST'?await POST(new Request('http://localhost/api/model',{method:'POST',body:'ignored'})):await GET();res.writeHead(r.status,Object.fromEntries(r.headers));res.end(await r.text())});
 await new Promise(r=>s.listen(0,'127.0.0.1',r));const port=s.address().port;live.add(port);
 try {await fn('http://127.0.0.1:'+port+'/api/model')} finally {s.closeAllConnections();await new Promise(r=>s.close(r));live.delete(port)}
}
test('production candidate denies before body parsing, even forged verified headers',async()=>{const req={get headers(){throw Error('SECRET')},get json(){throw Error('SECRET')},verified:true};const r=await POST(req);assert.equal(r.status,403);assert.equal((await r.json()).code,'TRUSTED_GATE_REQUIRED')});
test('real HTTP tenant A and B both denied; no request body/log echo',async()=>withServer(async u=>{for(const tenant of ['tenant-a','tenant-b']){const r=await fetch(u,{method:'POST',headers:{Authorization:'synthetic-secret','X-Verified':'true','Content-Type':'application/json'},body:JSON.stringify({tenant,verified:true,usageVerified:true,secret:'SECRET-SENTINEL'})});assert.equal(r.status,403);const b=await r.text();assert.equal(b.includes(tenant),false);assert.equal(b.includes('SECRET'),false);assert.equal(r.headers.get('cache-control'),'no-store')}}));
test('SSE request remains deny-only JSON, not an authorized stream',async()=>withServer(async u=>{const r=await fetch(u,{method:'POST',headers:{Accept:'text/event-stream'},body:'{"stream":true}'});assert.equal(r.status,403);assert.match(r.headers.get('content-type'),/application\/json/)}));
test('GET method rejected via real HTTP',async()=>withServer(async u=>{const r=await fetch(u);assert.equal(r.status,405);assert.equal(r.headers.get('allow'),'POST')}));
test('aborted request never becomes authorized or verified',async()=>{const c=new AbortController();c.abort();const r=await POST(new Request('http://localhost/api/model',{method:'POST',signal:c.signal}));assert.equal(r.status,403);assert.equal((await r.json()).usage_verified,false)});
test('all test listeners closed',()=>assert.equal(live.size,0));
