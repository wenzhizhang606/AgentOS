"""Transaction contracts against a dedicated, migrated MySQL 8.4 database."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import os
from uuid import uuid4

import pytest

from agentos.adapters.mysql.store import MySQLStore
from agentos.contracts.errors import PlatformError


pytestmark = [
    pytest.mark.mysql,
    pytest.mark.skipif(os.environ.get("AGENTOS_TEST_MYSQL") != "1", reason="Requires disposable MySQL"),
]


@pytest.fixture
def db():
    store = MySQLStore.from_env()
    tenant = uuid4().hex
    store.set_access(tenant, "reader", "project", True)
    store.put_source(tenant, "project", "guide", "Guide", "Approved terms", "#one")
    request = {
        "project_id": "project", "agent_version_id": tenant, "limit_micros": 100, "goal": "terms",
        "deadline_at": (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat().replace("+00:00", "Z"),
    }
    return store, tenant, request


def claimed(db):
    store, tenant, request = db
    store.create_task(tenant, "reader", "key", request)
    task = store.claim("test-worker", agent_version_id=tenant)
    epoch, sources = store.evidence(task)
    ref = {k: sources[0][k] for k in ("source_id", "version", "location", "tenant_id", "project_id")}
    snapshot = {
        "snapshot_id": uuid4().hex, "authorization_revision": epoch, "source_refs": [ref],
        "payload_digest": sha256(b"{}").hexdigest(), "retention_mode": "metadata_only",
        "stored_payload_digest": None, "agent_version_id": tenant, "identity_ref": "reader",
        "task_id": task["task_id"], "attempt": task["attempt"],
    }
    return task, snapshot


def expire_lease(store, task):
    # Advance the fixture's queue eligibility without waiting for a wall-clock race.
    store._tx(lambda cur: cur.execute(
        """UPDATE ag_task SET lease_until=UTC_TIMESTAMP(6),ready_at=UTC_TIMESTAMP(6) WHERE task_id=%s""",
        (task["task_id"],)))


def test_ac017_concurrent_create_and_release_replay(db):
    store, tenant, request = db
    def submit(_):
        return store.create_task(tenant, "reader", "shared", request)["task_id"]
    with ThreadPoolExecutor(4) as pool:
        ids = set(pool.map(submit, range(8)))
    assert len(ids) == 1
    assert store.create_task(tenant, "reader", "shared", request, replay_only=True)["task_id"] in ids
    with pytest.raises(PlatformError) as conflict:
        store.create_task(tenant, "reader", "shared", dict(request, goal="changed"))
    assert conflict.value.code == "IDEMPOTENCY_CONFLICT"
    with pytest.raises(PlatformError) as unsupported:
        store.create_task(tenant, "reader", "new", request, replay_only=True)
    assert unsupported.value.code == "INVALID_ARGUMENT"


def test_ac008_ac017_lease_fences_stale_worker_and_pending_dispatch(db):
    store, tenant, _ = db
    old, snapshot = claimed(db)
    assert store.claim("other", agent_version_id=tenant) is None
    expire_lease(store, old)
    current = store.claim("other", agent_version_id=tenant)
    assert current["lease_epoch"] > old["lease_epoch"]
    with pytest.raises(PlatformError) as fenced:
        store.admit(old, snapshot, 15)
    assert fenced.value.code == "LEASE_LOST"
    snapshot["attempt"] = current["attempt"]
    permit = store.admit(current, snapshot, 15)
    expire_lease(store, current)
    assert store.claim("replacement", agent_version_id=tenant) is None
    task = store.get_task(tenant, "reader", old["task_id"])
    assert (task["state"], task["accounting_state"], task["reserved_micros"]) == ("failed", "pending", 15)
    for _ in range(2):
        store.reconcile(tenant, permit["dispatch_attempt_id"], "verified", 11, input_tokens=4, output_tokens=7)
    task = store.get_task(tenant, "reader", old["task_id"])
    assert (task["state"], task["settled_micros"], task["reserved_micros"], task["result"]) == ("failed", 11, 0, None)


def test_ac009_atomic_budget_and_unique_dispatch(db):
    store, tenant, _ = db
    task, snapshot = claimed(db)
    with pytest.raises(PlatformError) as denied:
        store.admit(task, snapshot, 101)
    assert denied.value.code == "BUDGET_EXHAUSTED"
    assert store.get_task(tenant, "reader", task["task_id"])["snapshot_id"] is None

    def admit(_):
        try:
            return store.admit(task, snapshot, 90)["dispatch_attempt_id"]
        except PlatformError as err:
            return err.code
    with ThreadPoolExecutor(2) as pool:
        attempts = list(pool.map(admit, range(2)))
    attempts.remove("EXTERNAL_OUTCOME_UNKNOWN")
    dispatch_id, = attempts
    assert store.get_task(tenant, "reader", task["task_id"])["reserved_micros"] == 90
    for _ in range(2):
        store.finish(task, dispatch_id, {"status": "failed", "error": "INVALID_OUTPUT"}, 110, "bill",
                     input_tokens=60, output_tokens=50)
    with pytest.raises(PlatformError) as conflict:
        store.finish(task, dispatch_id, {}, 110, "bill", input_tokens=55, output_tokens=55)
    assert conflict.value.code == "IDEMPOTENCY_CONFLICT"
    read = store.get_task(tenant, "reader", task["task_id"])
    assert (read["state"], read["settled_micros"], read["reserved_micros"]) == ("failed", 110, 0)
    assert read["budget_overrun"] and read["usage"]["output_tokens"] == 50


def test_ac012_scope_recheck_and_source_tombstone(db):
    store, tenant, _ = db
    task, snapshot = claimed(db)
    with pytest.raises(PlatformError) as out_of_scope:
        store.get_task(tenant, "reader", task["task_id"], allowed_projects={"different"})
    assert out_of_scope.value.code == "RESOURCE_NOT_FOUND"
    store.set_access(tenant, "reader", "project", False)
    with pytest.raises(PlatformError) as revoked:
        store.admit(task, snapshot, 30)
    assert revoked.value.code == "FORBIDDEN"
    store.set_access(tenant, "reader", "project", True)
    with pytest.raises(PlatformError) as stale:
        store.admit(task, snapshot, 30)
    assert stale.value.code == "CONTEXT_STALE"
    snapshot["authorization_revision"], _ = store.evidence(task)
    store.delete_source(tenant, "project", "guide")
    with pytest.raises(PlatformError) as deleted:
        store.admit(task, snapshot, 30)
    assert deleted.value.code == "CONTEXT_STALE"


def test_ac001_ac012_retained_bytes_and_delivery_recheck(db):
    store, tenant, _ = db
    task, snapshot = claimed(db)
    snapshot.update(retention_mode="full", stored_payload_digest=snapshot["payload_digest"])
    with pytest.raises(PlatformError) as corrupt:
        store.admit(task, snapshot, 20, b'{"bad":true}')
    assert corrupt.value.code == "CONTEXT_STALE"
    permit = store.admit(task, snapshot, 20, b"{}")
    store.finish(task, permit["dispatch_attempt_id"], {"status": "complete", "answer": "terms"}, 12, "bill")
    assert store.get_snapshot(tenant, "reader", snapshot["snapshot_id"])["retained_payload"] == {}
    assert store.get_task(tenant, "reader", task["task_id"])["result"]["answer"] == "terms"
    store.put_source(tenant, "project", "guide", "Correction", "New terms", "#one")
    read = store.get_task(tenant, "reader", task["task_id"])
    assert read["result"] is None and read["result_error"] == "CONTEXT_STALE"
    with pytest.raises(PlatformError) as stale:
        store.get_snapshot(tenant, "reader", snapshot["snapshot_id"])
    assert stale.value.code == "CONTEXT_STALE"


def test_ac008_cancel_and_late_billing(db):
    store, tenant, _ = db
    task, snapshot = claimed(db)
    permit = store.admit(task, snapshot, 9)
    version = store.get_task(tenant, "reader", task["task_id"])["version"]
    with pytest.raises(PlatformError) as stale:
        store.cancel_task(tenant, "reader", task["task_id"], "cancel", version - 1)
    assert stale.value.code == "VERSION_CONFLICT"
    for _ in range(2):
        assert store.cancel_task(tenant, "reader", task["task_id"], "cancel", version)["state"] == "cancelled"
    store.finish(task, permit["dispatch_attempt_id"], {"status": "complete"}, 8, "late")
    result = store.get_task(tenant, "reader", task["task_id"])
    assert (result["state"], result["settled_micros"], result["result"]) == ("cancelled", 8, None)


def test_ac010_release_registration_is_bound_to_canonical_manifest(db):
    store, tenant, _ = db
    manifest = {"model": "示例", "tenant_marker": tenant, "revision": 1}
    encoded = json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    version_id = sha256(encoded).hexdigest()
    assert not store.ready(version_id)
    store.register_release(version_id, manifest)
    assert store.ready(version_id)
    store.register_release(version_id, dict(reversed(list(manifest.items()))))
    with pytest.raises(PlatformError) as changed:
        store.register_release(version_id, dict(manifest, revision=2))
    assert changed.value.code == "INVALID_ARGUMENT"


def test_ac009_billed_over_root_limit_suppresses_answer(db):
    store, tenant, _ = db
    task, snapshot = claimed(db)
    permit = store.admit(task, snapshot, 80)
    store.finish(task, permit["dispatch_attempt_id"], {"status": "complete", "answer": "terms"},
                 101, "overrun", input_tokens=99, output_tokens=2)
    read = store.get_task(tenant, "reader", task["task_id"])
    assert (read["state"], read["error_code"], read["result"]) == ("failed", "BUDGET_EXHAUSTED", None)
    assert (read["settled_micros"], read["reserved_micros"]) == (101, 0)
    assert read["usage"] == {"input_tokens": 99, "output_tokens": 2}
