"""Durable management attempt/receipt records in the ORIGINAL Source directory.

The existing Source I/O helpers supply path, owner, mode, hardlink, size and
fsync guards. This journal creates no directory, database, Agent Registry,
grant, credential or transport. A reserved attempt never replays after restart.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path
import re

from deer_agent_management import AgentManagementDenied, _canonical


class OriginalManagementJournal:
    def __init__(self, root, *, binding, current, authorize, source_io=None):
        if (type(binding) is not dict
                or set(binding) != {"mcp_owner", "gateway_user_id", "grant_id"}
                or any(not isinstance(v, str) or not v.strip() for v in binding.values())
                or not callable(current) or not callable(authorize)):
            raise AgentManagementDenied("MANAGEMENT_JOURNAL_BINDING_REQUIRED")
        if source_io is None:
            from app.gateway import platform_task_source as source_io
        self._io = source_io
        self.root = Path(root)
        self.binding = dict(binding)
        self.current, self.authorize = current, authorize
        fd = self._io.private_directory(self.root)
        try:
            info = os.fstat(fd)
            self._identity = info.st_dev, info.st_ino
        finally:
            os.close(fd)

    def _open(self):
        if self.current() is not True:
            raise AgentManagementDenied("MANAGEMENT_HOST_STOPPED")
        fd = self._io.private_directory(self.root)
        info = os.fstat(fd)
        if (info.st_dev, info.st_ino) != self._identity:
            os.close(fd)
            raise AgentManagementDenied("MANAGEMENT_SOURCE_ROOT_CHANGED")
        return fd

    def _record(self, action, operation_key, digest):
        if (action != "create"
                or not isinstance(operation_key, str)
                or re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", operation_key) is None
                or not isinstance(digest, str)
                or re.fullmatch(r"[a-f0-9]{64}", digest) is None):
            raise AgentManagementDenied("MANAGEMENT_OPERATION_INVALID")
        identity = [self.binding, action, operation_key]
        key = hashlib.sha256(_canonical(identity)).hexdigest()
        record = {"schema": "original-deer-agent-attempt-v1", "key": key,
                  "binding": dict(self.binding), "action": action,
                  "operation_key": operation_key, "payload_sha256": digest}
        return key, record

    def _read(self, key, record):
        fd = self._open()
        try:
            raw = self._io.read_at(fd, "agent-attempt-" + key + ".json")
            if json.loads(raw) != record:
                raise AgentManagementDenied("MANAGEMENT_OPERATION_CONFLICT")
            try:
                receipt = json.loads(self._io.read_at(fd, "agent-receipt-" + key + ".json"))
            except FileNotFoundError:
                return {"status": "ATTEMPTED_UNKNOWN_QUERY_ONLY", "operation_id": key}
            if (type(receipt) is not dict
                    or set(receipt) != {"schema", "attempt_sha256", "result"}
                    or receipt["schema"] != "original-deer-agent-receipt-v1"
                    or receipt["attempt_sha256"] != hashlib.sha256(_canonical(record)).hexdigest()
                    or type(receipt["result"]) is not dict):
                raise AgentManagementDenied("MANAGEMENT_RECEIPT_MISMATCH_QUERY_ONLY")
            return {"status": "CONFIRMED", "operation_id": key,
                    "result": receipt["result"]}
        finally:
            os.close(fd)

    def _reserve(self, key, record):
        fd = self._open()
        try:
            try:
                self._io.publish(fd, "agent-attempt-" + key + ".json", self._io.encode(record))
            except FileExistsError:
                return False
            except OSError as error:
                # O_EXCL may have succeeded before fsync failed. Keep it.
                raise AgentManagementDenied("MANAGEMENT_RESERVE_UNKNOWN_QUERY_ONLY") from error
            return True
        finally:
            os.close(fd)

    def _save(self, key, record, result):
        receipt = {"schema": "original-deer-agent-receipt-v1",
                   "attempt_sha256": hashlib.sha256(_canonical(record)).hexdigest(),
                   "result": result}
        fd = self._open()
        try:
            self._io.publish(fd, "agent-receipt-" + key + ".json", self._io.encode(receipt))
        except (OSError, ValueError) as error:
            raise AgentManagementDenied("MANAGEMENT_RESULT_UNKNOWN_QUERY_ONLY") from error
        finally:
            os.close(fd)

    async def _check(self):
        if self.current() is not True or await self.authorize() is not True:
            raise AgentManagementDenied("MANAGEMENT_JOURNAL_AUTHORITY_DENIED")

    async def query(self, action, operation_key, digest):
        await self._check()
        key, record = self._record(action, operation_key, digest)
        try:
            result = await asyncio.to_thread(self._read, key, record)
        except FileNotFoundError:
            result = {"status": "NOT_FOUND_QUERY_ONLY", "operation_id": key}
        await self._check()
        return result

    async def __call__(self, action, operation_key, digest, perform):
        await self._check()
        key, record = self._record(action, operation_key, digest)
        reserved = await asyncio.to_thread(self._reserve, key, record)
        if not reserved:
            saved = await self.query(action, operation_key, digest)
            if saved["status"] != "CONFIRMED":
                raise AgentManagementDenied("MANAGEMENT_ATTEMPT_UNKNOWN_QUERY_ONLY")
            return saved["result"]
        # All failures below preserve attempted_unknown, including cancellation,
        # process death and a successful original write whose reply was lost.
        await self._check()
        result = await perform()
        if type(result) is not dict:
            raise AgentManagementDenied("MANAGEMENT_RESULT_INVALID_QUERY_ONLY")
        result = json.loads(_canonical(result))
        await self._check()
        await asyncio.to_thread(self._save, key, record, result)
        await self._check()
        return result
