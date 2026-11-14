"""Isolated contracts. Redis calls are fixtures; Lua is the real liblua engine.
No live Redis, Work identity, OAuth grant, callback or notification is created.
"""
import ast
import asyncio
import ctypes
import ctypes.util
import hashlib
import json
from pathlib import Path
import re
import secrets
import unittest
from types import SimpleNamespace
from mcp.server.fastmcp import Context, FastMCP
from mcp.types import ToolAnnotations

ROOT = Path(__file__).resolve().parents[1]
class InboxError(Exception): pass
def canonical(x): return json.dumps(x,sort_keys=True,separators=(',',':'))
def identifier(x):
    if not isinstance(x,str) or not re.fullmatch('[A-Za-z0-9_-]{1,80}',x): raise InboxError('invalid_identifier')
    return x

def extract(path,names,env):
    tree=ast.parse(path.read_text())
    nodes=[n for n in tree.body if isinstance(n,(ast.ClassDef,ast.FunctionDef)) and n.name in names]
    exec(compile(ast.Module(body=nodes,type_ignores=[]),str(path),'exec'),env)
    return env

E=extract(ROOT/'inbox.py',{'Inbox'},dict(hashlib=hashlib,json=json,re=re,secrets=secrets,
          identifier=identifier,canonical=canonical,InboxError=InboxError))
class EventsFixture:
    def __init__(self,*a,**kw): pass
    async def dispatch(self,*a): return []
F=extract(ROOT/'formal_transport.py',{'caller_context_observation','install_formal_transport'},
 dict(asyncio=asyncio,hashlib=hashlib,os=SimpleNamespace(),Context=Context,
 ToolAnnotations=ToolAnnotations,InboxError=InboxError,Events=EventsFixture,
 FORMAL='gateway.continuation.ready',MAILBOX='gateway_internal_v1'))
def origin(fp='a'*64):
    return dict(schema='caller-context-observation-v1',work_identity_verified=False,
      evidence='CONVERSATION_METADATA_ONLY_NOT_WORK_IDENTITY',conversation_id_present=True,
      conversation_fingerprint=fp)

class PythonContracts(unittest.TestCase):
    def send(self,o=None,name='gateway.continuation.ready'):
        box=E['Inbox']('fixture-owner','gateway_internal_v1')
        box.run=lambda action,**kw:dict(action=action,**kw)
        return box.send('fixture-op','body',event_name=name,sender_context=o)
    def test_context_is_bound_in_digest(self):
        a=self.send(origin());b=self.send(origin('b'*64))
        self.assertNotEqual(a['digest'],b['digest'])
        self.assertEqual(a['sender_context'],origin())
    def test_legacy_digest_unchanged(self):
        for name,expected in [('inbox.message.created',['fixture-op','body']),
          ('gateway.continuation.ready',['gateway.continuation.ready','fixture-op','body'])]:
            r=self.send(name=name);self.assertNotIn('sender_context',r)
            self.assertEqual(r['digest'],hashlib.sha256(canonical(expected).encode()).hexdigest())
    def test_reject_identity_elevation_or_secrets(self):
        for patch in [{'work_identity_verified':True},{'secret':'NEVER_STORE'},
          {'conversation_fingerprint':'not-hash'},{'evidence':'VERIFIED'},
          {'conversation_id_present':1}]:
            with self.assertRaises(InboxError):self.send({**origin(),**patch})
    def test_context_not_allowed_on_synthetic_legacy(self):
        with self.assertRaises(InboxError): self.send(origin(),name='inbox.message.created')
    def test_absent_context_explicitly_unverified(self):
        o=F['caller_context_observation'](None,'fixture-owner')
        self.assertFalse(o['work_identity_verified']);self.assertFalse(o['conversation_id_present'])
        self.assertEqual(self.send(o)['sender_context'],o)
    def test_public_send_schema_has_no_origin_argument(self):
        m=FastMCP('offline');F['install_formal_transport'](m,lambda _:None,None,lambda:'owner',{})
        t=next(t for t in asyncio.run(m.list_tools()) if t.name=='gateway_inbox_send')
        self.assertEqual(set(t.inputSchema['properties']),{'mailbox_id','operation_key','message'})
    def test_formal_handler_uses_context_not_message(self):
        class Box:
            owner='fixture-owner';mailbox_id='gateway_internal_v1'
            def for_event(self,n):return self
            def send(self,op,message,**kw):
                self.kw=kw;return {'event_id':'fixture-event','sender_context':kw['sender_context']}
        class Registry:
            def __init__(self):self.tools={}
            def tool(self,**kw):
                def register(fn):self.tools[fn.__name__]=fn;return fn
                return register
        m=Registry();box=Box();F['install_formal_transport'](m,lambda _:box,None,lambda:'owner',{})
        ctx=SimpleNamespace(request_context=SimpleNamespace(meta={'openai/session':'actual-fixture-session'}))
        r=asyncio.run(m.tools['gateway_inbox_send']('gateway_internal_v1','op',
            '{"sender_context":{"conversation_fingerprint":"FORGED"}}',ctx))
        self.assertNotEqual(r['sender_context']['conversation_fingerprint'],'FORGED')
        self.assertFalse(r['sender_context']['work_identity_verified'])

class LuaContracts(unittest.TestCase):
    def run_lua(self,case):
        # Execute the exact product Lua with an explicitly in-memory Redis/cjson
        # fixture. Deep copies preserve serialization boundaries; no server runs.
        code='''
local function clone(v) if type(v)~='table' then return v end local t={} for k,x in pairs(v) do t[k]=clone(x) end return t end
local stored=nil; local writes=0
cjson={decode=clone,encode=clone}
redis={call=function(cmd,...)
 local a={...}; if cmd=='TIME' then return {'1000'} elseif cmd=='GET' then return clone(stored)
 elseif cmd=='SET' then stored=clone(a[2]);writes=writes+1;return 'OK'
 elseif cmd=='TTL' then return stored and 86400 or -2 else error('unexpected redis call') end end}
KEYS={'fixture-mailbox'}
local product=assert(load([==['''+(ROOT/'inbox.lua').read_text()+''']==]))
local function call(action,req) req.epoch='epoch';ARGV={action,req};return product() end
local function origin() return {schema='caller-context-observation-v1',work_identity_verified=false,
 evidence='CONVERSATION_METADATA_ONLY_NOT_WORK_IDENTITY',conversation_id_present=true,conversation_fingerprint=string.rep('a',64)} end
local function send(o,digest,op) return call('send',{event_name='gateway.continuation.ready',event_id=string.rep('e',64),operation_key=op or 'op',message='{"sender_context":"FORGED"}',digest=digest or 'digest',sender_context=o}) end
local function list() return call('list',{event_name='gateway.continuation.ready',cursor_epoch='',after=0,limit=20}) end
'''+case
        lib=ctypes.CDLL(ctypes.util.find_library('lua5.4'))
        lib.luaL_newstate.restype=ctypes.c_void_p
        lib.luaL_openlibs.argtypes=[ctypes.c_void_p]
        lib.luaL_loadstring.argtypes=[ctypes.c_void_p,ctypes.c_char_p]
        lib.lua_pcallk.argtypes=[ctypes.c_void_p,ctypes.c_int,ctypes.c_int,ctypes.c_int,ctypes.c_longlong,ctypes.c_void_p]
        lib.lua_tolstring.argtypes=[ctypes.c_void_p,ctypes.c_int,ctypes.POINTER(ctypes.c_size_t)];lib.lua_tolstring.restype=ctypes.c_char_p
        lib.lua_close.argtypes=[ctypes.c_void_p]
        state=lib.luaL_newstate();lib.luaL_openlibs(state)
        try:
            status=lib.luaL_loadstring(state,code.encode())
            if not status:status=lib.lua_pcallk(state,0,0,0,0,None)
            if status:self.fail(lib.lua_tolstring(state,-1,None).decode())
        finally:lib.lua_close(state)
    def test_atomic_event_context_and_list(self):
        self.run_lua("local r=send(origin());assert(r.sender_context.conversation_fingerprint==string.rep('a',64));assert(writes==1);local l=list();assert(l.items[1].sender_context.work_identity_verified==false);assert(l.items[1].message:find('FORGED'))")
    def test_duplicate_preserves_original_context_no_write(self):
        self.run_lua("send(origin());local r=send(origin());assert(r.duplicate==true);assert(r.sender_context);assert(writes==1)")
    def test_conflict_does_not_relabel_original(self):
        self.run_lua("send(origin());local b=origin();b.conversation_fingerprint=string.rep('b',64);local r=send(b,'other-digest');assert(r.error=='operation_conflict');assert(list().items[1].sender_context.conversation_fingerprint==string.rep('a',64));assert(writes==1)")
    def test_legacy_missing_origin_not_backfilled(self):
        self.run_lua("send(nil);local r=send(nil);assert(r.sender_context==nil);assert(list().items[1].sender_context==nil);assert(writes==1)")
    def test_lua_rejects_verified_and_unknown_fields(self):
        self.run_lua("local a=origin();a.work_identity_verified=true;assert(send(a).error=='invalid_sender_context');local b=origin();b.secret='NO';assert(send(b).error=='invalid_sender_context');assert(writes==0)")

if __name__=='__main__':unittest.main()
