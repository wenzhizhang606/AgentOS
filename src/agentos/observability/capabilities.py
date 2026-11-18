"""Trace local work against its actual task attempt, without recording payloads."""

from agentos.contracts.errors import PlatformError


def observe(store, task, meta, action, *, parent_span_id=None):
    span_id = store.start_capability(task, meta["kind"], meta["name"], meta["version"],
                                     meta["digest"], parent_span_id=parent_span_id)
    try:
        result = action(span_id)
    except PlatformError as exc:
        store.finish_capability(task, span_id, error_code=exc.code)
        raise
    except Exception as exc:
        store.finish_capability(task, span_id, error_code="CAPABILITY_FAILED")
        raise PlatformError("CAPABILITY_FAILED", "Local capability execution failed") from exc
    snapshot_id = result.snapshot_id if meta["kind"] == "context" else None
    store.finish_capability(task, span_id, snapshot_id=snapshot_id)
    return result
