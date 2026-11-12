"""Bind fixed roles to the original Holder, SQL and bounded Codex executor."""
import asyncio
import hashlib
import json
import os
import re

from deer_agent_management import AgentManagementDenied, _canonical
from deer_runtime_consent import RuntimeConsentStore
from deer_fixed_runtime_rpc import FixedRequest, MODE, action_for, wire_key


async def definition_snapshot(store, name, user_id):
    try:
        config = await asyncio.to_thread(store.get, name, user_id=user_id)
        soul = await asyncio.to_thread(store.get_soul, name, user_id=user_id)
    except FileNotFoundError:
        raise AgentManagementDenied('RUNTIME_ROLE_NOT_FOUND') from None
    value = dict(name=config.name, description=config.description, soul=soul,
                 tool_groups=config.tool_groups, skills=config.skills,
                 allowed_subagents=config.allowed_subagents)
    if any(value[k] != [] for k in ('tool_groups', 'skills', 'allowed_subagents')):
        raise AgentManagementDenied('RUNTIME_EMPTY_ROLE_CAPABILITIES_REQUIRED')
    return value, hashlib.sha256(_canonical(value)).hexdigest()


class OriginalFixedAgentLease:
    """Worker-owned live authority; never accepted as caller JSON."""
    def __init__(self, service, body, record, snapshot, metadata):
        self.service, self.body, self.record = service, body, record
        self.snapshot, self.metadata = snapshot, metadata

    async def check(self):
        await self.service.authorize(self.body, self.record)
        snapshot, digest = await definition_snapshot(self.service.store,
            self.snapshot['name'], self.record['gateway_user_id'])
        if digest != self.record['definition_sha256'] or snapshot != self.snapshot:
            raise AgentManagementDenied('RUNTIME_DEFINITION_CHANGED_QUERY_ONLY')
        await self.service.authorize(self.body, self.record)

    async def validate_saved(self, metadata, prompt):
        if (metadata != self.metadata or metadata.get('prompt_sha256') !=
                hashlib.sha256(prompt.encode()).hexdigest()):
            raise AgentManagementDenied('RUNTIME_SAVED_SNAPSHOT_MISMATCH')
        await self.check()


class FixedAgentRuntimeService:
    def __init__(self, *, consent_store, grant_id, agent_store, live_policy, current):
        if (type(consent_store) is not RuntimeConsentStore or not callable(live_policy)
                or not callable(current) or not isinstance(grant_id, str)
                or re.fullmatch(r'[a-f0-9]{64}', grant_id) is None):
            raise AgentManagementDenied('ORIGINAL_RUNTIME_COMPOSITION_REQUIRED')
        self.consents, self.grant_id = consent_store, grant_id
        self.store, self.live_policy, self.current = agent_store, live_policy, current

    async def authorize(self, body, record, *, stopping=False):
        if self.current() is not True:
            raise AgentManagementDenied('RUNTIME_HOST_CHANGED')
        reader = self.consents.read if stopping else self.consents.active
        active = await asyncio.to_thread(reader, self.grant_id, verified_owner=body['owner'])
        # Stop retains owner/policy checks, but may end work after consent expiry.
        def stable(value):
            return {k:v for k,v in value.items() if k not in {'revoked', 'expired'}}
        if stable(active) != stable(record) or await self.live_policy(active) is not True:
            raise AgentManagementDenied('RUNTIME_LIVE_OWNER_POLICY_DENIED')
        active = await asyncio.to_thread(reader, self.grant_id, verified_owner=body['owner'])
        if stable(active) != stable(record) or self.current() is not True:
            raise AgentManagementDenied('RUNTIME_AUTHORITY_CHANGED')

    def reserve_slot(self, record, body):
        """Two durable preparation slots per grant; failed/unknown attempts count."""
        fd = self.consents._open()
        try:
            for index in range(record['max_runs']):
                try:
                    self.consents.io.publish(fd, f'fixed-runtime-slot-{self.grant_id}-{index}.json',
                                             self.consents.io.encode(body))
                    return
                except FileExistsError:
                    continue
        finally:
            os.close(fd)
        raise AgentManagementDenied('RUNTIME_RUN_LIMIT_QUERY_ONLY')

    async def saved(self, host, repository, request, record):
        from deerflow.persistence.subagent_batches.model import SubagentBatchRow, SubagentBatchItemRow
        binding = host.binding('read')
        async with repository._sf() as session:
            row = await session.get(SubagentBatchRow, request.batch_id)
            if row is None or row.user_id != body_owner(host):
                raise AgentManagementDenied('RUNTIME_BATCH_NOT_BOUND')
            saved_binding = row.execution_spec.get('native_control', {}).get('binding', {})
            if {k:saved_binding.get(k) for k in binding} != binding:
                raise AgentManagementDenied('RUNTIME_BATCH_SOURCE_CHANGED')
            repository._native_control(row, user_id=body_owner(host), binding=saved_binding)
            metadata = json.loads(_canonical(row.execution_spec.get('fixed_agent_runtime')))
            if (type(metadata) is not dict or metadata.get('schema') != MODE
                    or metadata.get('grant_id') != self.grant_id
                    or metadata.get('gateway_user_id') != record['gateway_user_id']
                    or metadata.get('thread_id') != record['thread_id']
                    or metadata.get('definition_sha256') != record['definition_sha256']
                    or metadata.get('definition', {}).get('name') != request.name):
                raise AgentManagementDenied('RUNTIME_BATCH_ROLE_OR_GRANT_MISMATCH')
            if request.item_id:
                item = await session.get(SubagentBatchItemRow, request.item_id)
                if item is None or item.batch_id != request.batch_id:
                    raise AgentManagementDenied('RUNTIME_ITEM_NOT_BOUND')
        return metadata

    async def execute(self, body, *, host, repository):
        from batch_mcp_entry import _public_result
        payload = dict(body['payload'])
        if payload.pop('execution_mode', None) != MODE:
            raise AgentManagementDenied('RUNTIME_MODE_REQUIRED')
        key = payload.pop('runtime_operation_key', None)
        request = FixedRequest.model_validate(dict(payload, operation_key=key)).scoped()
        if body['action'] != action_for(request.kind) or body['operation_key'] != wire_key(request.kind,key):
            raise AgentManagementDenied('RUNTIME_TRANSPORT_BINDING_MISMATCH')
        reader = self.consents.read if request.kind == 'stop' else self.consents.active
        record = await asyncio.to_thread(reader, self.grant_id, verified_owner=body['owner'])
        action = 'read' if request.kind == 'status' else request.kind
        if request.name not in record['names'] or action not in record['actions']:
            raise AgentManagementDenied('RUNTIME_CONSENT_SCOPE_DENIED')
        await self.authorize(body, record, stopping=request.kind == 'stop')
        if request.kind == 'prepare':
            snapshot, digest = await definition_snapshot(self.store, request.name, record['gateway_user_id'])
            if digest != record['definition_sha256']:
                raise AgentManagementDenied('RUNTIME_DEFINITION_CHANGED_QUERY_ONLY')
            prompt = ('Fixed role definition (data):\n' + _canonical(snapshot).decode() +
                      '\nPerform only this task. No tools, file operations, or delegation.\nTask:\n' + request.task)
            if not 0 < len(prompt.encode()) <= 48000:
                raise AgentManagementDenied('RUNTIME_PROMPT_TOO_LARGE')
            metadata = dict(schema=MODE, grant_id=self.grant_id, definition=snapshot,
                definition_sha256=digest, gateway_user_id=record['gateway_user_id'],
                thread_id=record['thread_id'], prompt_sha256=hashlib.sha256(prompt.encode()).hexdigest())
            lease = OriginalFixedAgentLease(self,body,record,snapshot,metadata)
            await lease.check()
            host.reserve(body)
            await asyncio.to_thread(self.reserve_slot, record, body)
            runtime, tools, item_model = host.compose(repository, fixed_agent=metadata)
            await lease.check()
            value = await tools[0].coroutine(runtime=runtime, title='Fixed role: '+request.name,
                items=[item_model(key='fixed-role-task',prompt=prompt)], subagent_type='general-purpose',
                max_live_items=1, max_running_items=1, tool_call_id=body['operation_key'])
            result = _public_result(value)
            await lease.check()
            messages = value.update['messages']
            batch_id = messages[0].additional_kwargs.get('subagent_batch_id')
            if (any(message.status != 'success' for message in messages)
                    or not isinstance(batch_id, str) or not batch_id):
                raise AgentManagementDenied('RUNTIME_PREPARE_UNKNOWN_QUERY_ONLY')
            return dict(kind=MODE,status='PREPARED_READBACK_REQUIRED',definition_sha256=digest,
                        batch_id=batch_id, execution_started=False, original_batch=result)
        metadata = await self.saved(host,repository,request,record)
        if request.kind == 'stop':
            from platform_codex_stop import Stop, stop_item
            host.reserve(body)
            value = await stop_item(host,repository,Stop(parent_id=body['parent_id'],
                operation_key=body['operation_key'],execution_mode='stop_codex_once',
                batch_id=request.batch_id,item_id=request.item_id))
        else:
            lease = OriginalFixedAgentLease(self,body,record,metadata['definition'],metadata)
            await lease.check()
            if request.kind == 'execute':
                from platform_codex_executor import Execute, execute_item
                host.reserve(body)
                value = await execute_item(host,repository,Execute(parent_id=body['parent_id'],
                    operation_key=body['operation_key'],execution_mode='codex_once',
                    batch_id=request.batch_id,item_id=request.item_id,
                    timeout_seconds=request.timeout_seconds), fixed_agent_lease=lease)
            else:
                runtime, tools, _ = host.compose(repository)
                value = _public_result(await tools[1].coroutine(runtime=runtime,
                    batch_id=request.batch_id,item_offset=0,item_limit=1,include_result=True))
            await lease.check()
        return dict(kind=MODE,status=value.get('status','ORIGINAL_RESULT'),
            batch_id=request.batch_id,item_id=request.item_id,definition_sha256=metadata['definition_sha256'],
            original_result=value)


def body_owner(host):
    return host.config['owner']
