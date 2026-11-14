import asyncio
import ast
import hashlib
import os
import unittest
from types import SimpleNamespace
from pathlib import Path
from mcp.server.fastmcp import FastMCP, Context
from mcp.types import RequestParams, ToolAnnotations
# AST isolation avoids importing absent local Redis dependency. Events is a
# fixture; Context/Meta/FastMCP registration use the installed real MCP SDK.
class OfflineEvents:
    def __init__(self, *args, **kwargs): pass
tree=ast.parse(Path(__file__).resolve().parents[1] / 'formal_transport.py'.read_text())
nodes=[n for n in tree.body if isinstance(n, ast.FunctionDef) and
       n.name in ('caller_context_observation','install_formal_transport')]
env=dict(asyncio=asyncio,hashlib=hashlib,os=os,Context=Context,
         ToolAnnotations=ToolAnnotations,InboxError=ValueError,Events=OfflineEvents,
         FORMAL='gateway.continuation.ready',MAILBOX='gateway_internal_v1')
exec(compile(ast.Module(body=nodes,type_ignores=[]),'formal_transport.py','exec'),env)
caller_context_observation=env['caller_context_observation']
install_formal_transport=env['install_formal_transport']

class SenderContextTests(unittest.TestCase):
    # Metadata examples are synthetic, never authentic Work identity proofs.
    def ctx(self, value):
        return SimpleNamespace(request_context=SimpleNamespace(meta=value))
    def test_absent(self):
        self.assertFalse(caller_context_observation(None,'owner')['work_identity_verified'])
    def test_pydantic_mcp_metadata(self):
        r=caller_context_observation(self.ctx(RequestParams.Meta(**{'openai/session':'opaque'})),'owner')
        self.assertTrue(r['conversation_id_present']); self.assertFalse(r['work_identity_verified'])
        self.assertNotIn('opaque',str(r))
    def test_owner_scoped(self):
        c=self.ctx({'openai/session':'opaque'})
        self.assertNotEqual(caller_context_observation(c,'a')['conversation_fingerprint'],
                            caller_context_observation(c,'b')['conversation_fingerprint'])
    def test_fake_thread_and_verified_are_ignored(self):
        r=caller_context_observation(self.ctx({'threadId':'forged','verified':True,
              'openai/session':'caller-controlled','Authorization':'secret'}),'owner')
        self.assertFalse(r['work_identity_verified']); self.assertNotIn('secret',str(r))
        self.assertNotIn('forged',str(r))
    def test_invalid_session(self):
        for v in [True,{},[],None,'','x'*513]:
            self.assertFalse(caller_context_observation(self.ctx({'openai/session':v}),'owner')['conversation_id_present'])
    def test_context_hidden_from_public_tool_schema(self):
        mcp=FastMCP('offline-contract')
        install_formal_transport(mcp,lambda _:None,SimpleNamespace(),lambda:'owner',{})
        tools=asyncio.run(mcp.list_tools())
        schema=next(t.inputSchema for t in tools if t.name=='gateway_inbox_subscription_status')
        self.assertNotIn('ctx',schema.get('properties',{}))

if __name__=='__main__': unittest.main()
