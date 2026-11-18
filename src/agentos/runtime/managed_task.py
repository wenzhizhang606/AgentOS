"""One leased, read-only support task. A model attempt has exactly one sender."""

import logging
import time

from agentos.context.service import prepare_context
from agentos.contracts.errors import PlatformError
from agentos.control.release import Release
from agentos.gateways.model.client import ModelNotSent, ModelOutputError
from agentos.observability.capabilities import observe


log = logging.getLogger(__name__)


def _finish(store, task, dispatch_id, result, charge, receipt_id, input_tokens=0, output_tokens=0):
    # Retry the same receipt, never the external call. The database deduplicates it.
    for attempt in range(3):
        try:
            store.finish(task, dispatch_id, result, charge, receipt_id,
                         input_tokens=input_tokens, output_tokens=output_tokens)
            return
        except PlatformError as exc:
            if exc.code != "TRANSIENT_DEPENDENCY_FAILURE" or attempt == 2:
                raise
            time.sleep(0.1 * (2 ** attempt))


def _prepare_and_admit(store, task, release):
    def prepare(parent_span_id):
        def observe_capability(meta, action):
            return observe(store, task, meta, lambda _: action(), parent_span_id=parent_span_id)

        epoch, sources = store.evidence(task)
        return prepare_context(
            task_id=task["task_id"], question=task["goal"], candidates=sources,
            model=release.model, window_tokens=release.window_tokens,
            output_tokens=release.max_output_tokens, safety_margin_tokens=release.safety_margin_tokens,
            retention_mode=release.retention_mode, release_id=release.version_id,
            destination=release.endpoint, attempt=task["attempt"],
            identity_ref=task["subject_id"], authorization_revision=epoch,
            capabilities=release.capabilities, observe=observe_capability,
        )

    meta = {"kind": "context", "name": "prepare_context", "version": "evidence/1",
            "digest": release.version_id}
    prepared = observe(store, task, meta, prepare)
    start = time.monotonic()
    permit = store.admit(task, prepared.snapshot, release.reservation_micros, prepared.retained_payload)
    return prepared, permit["dispatch_attempt_id"], start + float(permit["valid_for_seconds"])


def run_one(store, model, release: Release, worker_id: str, lease_seconds: int = 120) -> bool:
    task = store.claim(worker_id, lease_seconds=lease_seconds, agent_version_id=release.version_id)
    if task is None:
        return False
    task_id = task["task_id"]
    try:
        try:
            prepared, dispatch_id, expires = _prepare_and_admit(store, task, release)
        except PlatformError as exc:
            if exc.code != "CONTEXT_STALE":
                raise
            # Re-read once under current permissions before any send has been admitted.
            prepared, dispatch_id, expires = _prepare_and_admit(store, task, release)
    except PlatformError as exc:
        if exc.code not in {"LEASE_LOST", "CANCELLED", "DEADLINE_EXCEEDED",
                            "TRANSIENT_DEPENDENCY_FAILURE"}:
            store.fail(task, exc.code)
        log.info("Preparation stopped task=%s code=%s", task_id, exc.code)
        return True

    if time.monotonic() >= expires:
        _finish(store, task, dispatch_id, {"status": "failed", "error": "DISPATCH_EXPIRED"},
                0, "unsent-" + dispatch_id)
        return True
    try:
        answer = model.generate(prepared, send_before=expires)
    except ModelNotSent as exc:
        _finish(store, task, dispatch_id, {"status": "failed", "error": exc.code},
                0, "unsent-" + dispatch_id)
        return True
    except ModelOutputError as exc:
        _finish(store, task, dispatch_id, {"status": "failed", "error": exc.code},
                release.cost(exc.input_tokens, exc.output_tokens), exc.provider_call_id,
                exc.input_tokens, exc.output_tokens)
        return True
    except PlatformError as exc:
        # Once the transport has been entered, a missing receipt cannot release the hold.
        store.fail(task, "EXTERNAL_OUTCOME_UNKNOWN")
        log.warning("Model call awaits reconciliation task=%s dispatch=%s code=%s",
                    task_id, dispatch_id, exc.code)
        return True

    result = {"status": "complete", "answer": answer.answer, "citations": answer.citations,
              "snapshot_id": prepared.snapshot_id}
    _finish(store, task, dispatch_id, result,
            release.cost(answer.input_tokens, answer.output_tokens), answer.provider_call_id,
            answer.input_tokens, answer.output_tokens)
    log.info("Settled model receipt task=%s dispatch=%s", task_id, dispatch_id)
    return True
