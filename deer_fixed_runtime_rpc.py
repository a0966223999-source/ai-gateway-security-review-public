"""Fixed definition tasks over the original signed transport, off by default."""
import asyncio
import hashlib
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations
from deer_agent_management import _canonical, _name

MODE = 'original-deer-fixed-runtime-v1'


class FixedRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    operation_key: str = Field(pattern=r'^[A-Za-z0-9_.:-]{1,128}$')
    kind: Literal['prepare', 'execute', 'status', 'stop']
    name: str
    task: str | None = Field(default=None, min_length=1, max_length=12000)
    batch_id: str | None = Field(default=None, min_length=1, max_length=256)
    item_id: str | None = Field(default=None, min_length=1, max_length=256)
    timeout_seconds: int = Field(default=90, ge=10, le=90)

    @field_validator('name')
    @classmethod
    def name_valid(cls, value):
        return _name(value)

    def scoped(self):
        base={'operation_key','kind','name'}
        fields=self.model_fields_set
        if self.kind=='prepare':
            valid=fields <= base|{'task'} and self.task is not None
        elif self.kind=='execute':
            valid=fields <= base|{'batch_id','item_id','timeout_seconds'} and all((self.batch_id,self.item_id))
        elif self.kind=='stop':
            valid=fields <= base|{'batch_id','item_id'} and all((self.batch_id,self.item_id))
        else:
            valid=fields <= base|{'batch_id'} and self.batch_id is not None
        if not valid:
            raise ValueError('FIXED_RUNTIME_FIELDS_INVALID')
        return self


def action_for(kind):
    return {'prepare':'submit','execute':'execute','status':'read','stop':'execute'}[kind]


def wire_key(kind,key):
    return 'fixed-agent:'+hashlib.sha256(_canonical([MODE,kind,key])).hexdigest()


def public_reply(result, *, historical=False):
    if result.get('status') in {'NOT_FOUND_QUERY_ONLY','PENDING_OR_UNKNOWN_QUERY_SAME_KEY'}:
        return {k:result[k] for k in ('request_id','status') if k in result}
    value=result.get('result')
    if type(value) is not dict or value.get('kind')!=MODE:
        raise ToolError('FIXED_RUNTIME_RESPONSE_SCOPE_INVALID')
    if historical:
        return {'request_id':result['request_id'],'historical_only':True,'authority':False,
                **{k:value[k] for k in ('kind','status','batch_id','item_id','definition_sha256') if k in value}}
    return {'request_id':result['request_id'],**value}


def install_fixed_runtime_tools(mcp, *, rpc, principal, oauth_meta, enabled=False):
    if enabled is not True or not {'read','submit','execute'} <= set(rpc.config['actions']):
        return False

    @mcp.tool(meta=oauth_meta,annotations=ToolAnnotations(readOnlyHint=False,
        destructiveHint=False,idempotentHint=True,openWorldHint=True))
    async def fixed_agent_task(request: FixedRequest)->dict:
        """Prepare/execute/read/stop one original fixed-role task with explicit runtime consent.

        prepare only saves. execute starts the existing bounded Codex CLI.
        No caller owner, grant, command, executable, PID or definition override.
        Management consent never authorizes runtime. Unknown: query same key.
        """
        request=FixedRequest.model_validate(request.model_dump(exclude_unset=True)).scoped()
        owner=principal()
        action=action_for(request.kind)
        key=wire_key(request.kind,request.operation_key)
        prior=await asyncio.to_thread(rpc.query,owner=owner,parent=rpc.config['parent_id'],
            action=action,operation_key=key)
        if principal()!=owner:
            raise ToolError('FIXED_RUNTIME_OWNER_CHANGED')
        if prior.get('status')!='NOT_FOUND_QUERY_ONLY':
            return public_reply(prior,historical=True)
        reply=await asyncio.to_thread(rpc.call,owner=owner,parent=rpc.config['parent_id'],
            action=action,operation_key=key,
            response_timeout=12 if request.kind in ('prepare','status','stop') else 3,payload={
                **request.model_dump(exclude={'operation_key'},exclude_unset=True),
                'execution_mode':MODE,'runtime_operation_key':request.operation_key})
        if principal()!=owner:
            raise ToolError('FIXED_RUNTIME_OWNER_CHANGED_QUERY_ONLY')
        return public_reply(reply)

    @mcp.tool(meta=oauth_meta,annotations=ToolAnnotations(readOnlyHint=True,
        destructiveHint=False,idempotentHint=True,openWorldHint=False))
    async def fixed_agent_receipt(operation_key:str,kind:Literal['prepare','execute','status','stop'])->dict:
        """Original same-key audit only; no dispatch, role contents or renewed consent."""
        FixedRequest(operation_key=operation_key,kind='prepare',name='validate-key',task='validate').scoped()
        owner=principal()
        reply=await asyncio.to_thread(rpc.query,owner=owner,parent=rpc.config['parent_id'],
            action=action_for(kind),operation_key=wire_key(kind,operation_key))
        if principal()!=owner:
            raise ToolError('FIXED_RUNTIME_OWNER_CHANGED')
        return public_reply(reply,historical=True)
    return True
