"""Cancel only untouched pending rows through the original SQL writer fence."""
from datetime import datetime, timezone

from batch_mcp_entry import Cancel
from fresh_batch_host import HostDenied


class PendingCancel(Cancel):
    pending_only: bool = False


async def cancel_pending_items(host, repository, request):
    from sqlalchemy import select
    from deerflow.persistence.subagent_batches.model import SubagentBatchItemRow, SubagentBatchRow

    owner, binding = host.config['owner'], host.binding('cancel')

    def checked(row):
        host.check('cancel')
        if row is None or row.user_id != owner:
            raise HostDenied('PENDING_CANCEL_BATCH_NOT_BOUND')
        saved = row.execution_spec.get('native_control', {}).get('binding', {})
        if {key: saved.get(key) for key in binding} != binding:
            raise HostDenied('PENDING_CANCEL_SOURCE_CHANGED')
        return repository._native_control(row, user_id=owner, binding=saved)

    async with repository._sf() as session:
        batch = await session.get(SubagentBatchRow, request.batch_id)
        checked(batch)
        await repository._lock_native_writer(session, request.batch_id, owner)
        batch = await session.get(SubagentBatchRow, request.batch_id, populate_existing=True)
        control = checked(batch)
        items = list((await session.execute(select(SubagentBatchItemRow).where(
            SubagentBatchItemRow.batch_id == request.batch_id).with_for_update())).scalars())
        if len(items) != batch.total_items:
            raise HostDenied('PENDING_CANCEL_ITEM_COUNT_CHANGED')
        pending = [item for item in items if item.status == 'pending']
        for item in pending:
            if (item.attempt != 0 or item.lease_owner is not None
                    or item.lease_expires_at is not None or item.started_at is not None
                    or item.cancel_requested_at is not None or item.id in control['items']):
                raise HostDenied('PENDING_CANCEL_ATTEMPT_MISMATCH')
        now = datetime.now(timezone.utc)
        for item in pending:
            item.status = 'cancelled'
            item.cancel_requested_at = now
            item.completed_at = now
            item.updated_at = now
            item.error = 'Pending item cancelled before dispatch'
        # Reuse original aggregation; never rewrite completed/running item state,
        # native attempt records, results, binding or payloadDigest.
        await session.flush()
        if len(pending) == batch.total_items:
            batch.status = 'cancelled'
        await repository._refresh_batch_status(session, batch, now=now)
        checked(batch)
        await session.commit()
        host.check('cancel')
        aggregate = await repository._with_counts(session, batch)
        return {'batch_id': batch.id, 'batch_status': aggregate['status'],
                'counts': aggregate['counts'],
                'cancelled_pending_item_ids': [item.id for item in pending],
                'running_items_stopped': False}
