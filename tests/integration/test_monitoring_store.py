"""AC-012/013/023 against a dedicated database with migrations 001 and 002."""

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


def register(store, manifest):
    raw = json.dumps(manifest, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    release_id = sha256(raw).hexdigest()
    store.register_release(release_id, manifest)
    return release_id


@pytest.fixture
def deployment():
    tenant = uuid4().hex
    store = MySQLStore.from_env()
    for subject in ("alice", "bob", "observer"):
        store.set_access(tenant, subject, "support", True)
    capability = {"kind": "tool", "name": "lookup-" + tenant, "version": "1", "digest": "a"*64}
    release_id = register(store, {"fixture": tenant, "capabilities": [capability]})
    return store, tenant, release_id, capability


def claim(deployment, subject="alice"):
    store, tenant, release_id, _ = deployment
    deadline = datetime.now(timezone.utc) + timedelta(minutes=10)
    request = {"project_id": "support", "agent_version_id": release_id,
               "goal": "PRIVATE task input", "limit_micros": 500, "deadline_at": deadline.isoformat()}
    store.create_task(tenant, subject, uuid4().hex, request)
    return store.claim("worker", agent_version_id=release_id)


def start_context(store, task):
    return store.start_capability(task, "context", "prepare_context", "evidence/1", task["agent_version_id"])


def test_ac013_023_observe_starts_failure_success_and_snapshot_links(deployment):
    store, tenant, _, cap = deployment
    task = claim(deployment)
    scope = {"allowed_projects": ("support",)}
    ctx = start_context(store, task)
    failed = store.start_capability(task, **cap, parent_span_id=ctx)
    store.finish_capability(task, failed, error_code="SECRET raw exception with arguments")
    ok = store.start_capability(task, **cap, parent_span_id=ctx)
    store.finish_capability(task, ok)
    store.finish_capability(task, ok)
    snap = str(uuid4())
    store.finish_capability(task, ctx, snapshot_id=snap)
    # Simulate a disappeared worker by deliberately leaving a started span without a terminal callback.
    interrupted = start_context(store, task)
    timeline = store.get_timeline(tenant, "alice", task["task_id"], **scope)
    assert "PRIVATE" not in json.dumps(timeline) and "SECRET" not in json.dumps(timeline)
    events = timeline["events"]
    assert [e["occurred_at"] for e in events] == sorted(e["occurred_at"] for e in events)
    assert [e["event"] for e in events if e.get("span_id") == interrupted] == ["started"]
    assert [e["event"] for e in events if e.get("span_id") == ok] == ["started", "succeeded"]
    error = next(e for e in events if e["event"] == "failed")
    assert (error["error_code"], error["parent_span_id"]) == ("CAPABILITY_FAILED", ctx)
    assert next(e for e in events if e.get("snapshot_id") == snap)["span_id"] == ctx

    report = store.get_monitoring(tenant, "alice", "support", **scope)
    tool = next(r for r in report["capabilities"] if r["kind"] == "tool")
    assert (tool["calls"],tool["succeeded"],tool["failed"],tool["unfinished"]) == (2,1,1,0)
    context = next(r for r in report["capabilities"] if r["kind"] == "context")
    assert context["unfinished"] == 1 and context["avg_duration_ms"] >= 0
    assert report["summary"]["tasks_with_spans"] == 1
    assert report["summary"]["tool_calls"] == 2 and report["summary"]["skill_calls"] == 0


def test_ac012_metadata_scope_and_revocation(deployment):
    store, tenant, _, _ = deployment
    task = claim(deployment)
    claim(deployment, subject="bob")
    scope = {"allowed_projects": ("support",)}
    with pytest.raises(PlatformError) as denied:
        store.get_timeline(tenant, "bob", task["task_id"], **scope)
    assert denied.value.code == "RESOURCE_NOT_FOUND"
    assert store.get_monitoring(tenant, "alice", "support", **scope)["summary"]["tasks"] == 1
    overview = store.get_monitoring(tenant, "observer", "support", **scope, include_all_subjects=True)
    assert overview["scope"] == "project" and overview["summary"]["tasks"] == 2
    trace = store.get_timeline(tenant, "observer", task["task_id"], **scope, monitor_project_ids=("support",))
    assert trace["task_id"] == task["task_id"]
    assert "PRIVATE" not in json.dumps([overview, trace])
    for other_tenant, projects in ((tenant, ()), ("other", ("support",))):
        with pytest.raises(PlatformError):
            store.get_timeline(other_tenant, "observer", task["task_id"],
                               allowed_projects=projects, monitor_project_ids=("support",))
    store.set_access(tenant, "observer", "support", False)
    with pytest.raises(PlatformError):
        store.get_monitoring(tenant, "observer", "support", **scope, include_all_subjects=True)
    with pytest.raises(PlatformError):
        store.get_timeline(tenant, "observer", task["task_id"], **scope, monitor_project_ids=("support",))


def test_ac013_one_charge_per_dispatch_with_multiple_spans_and_unknown_usage(deployment):
    store, tenant, release, cap = deployment
    task = claim(deployment)
    epoch, _ = store.evidence(task)
    snapshot = {"snapshot_id": str(uuid4()), "task_id": task["task_id"], "agent_version_id": release,
                "attempt": task["attempt"], "identity_ref": "alice", "authorization_revision": epoch,
                "source_refs": [], "payload_digest": "b"*64, "retention_mode": "metadata_only",
                "stored_payload_digest": None}
    snapshot["capability_refs"] = [cap]
    ctx = start_context(store, task)
    for _ in range(2):
        store.finish_capability(task, store.start_capability(task, **cap))
    store.finish_capability(task, store.start_capability(task, **cap, parent_span_id=ctx))
    store.finish_capability(task, ctx, snapshot_id=snapshot["snapshot_id"])
    permit = store.admit(task, snapshot, 45)
    claim(deployment)  # A task without telemetry is still part of the denominator.

    def view():
        return store.get_monitoring(tenant, "alice", "support", allowed_projects=("support",), limit=1)
    initial = view()
    assert len(initial["recent_tasks"]) == 1
    assert initial["summary"]["tasks"] == 2 and initial["summary"]["tasks_with_spans"] == 1
    assert initial["summary"]["reserved_micros"] == "45"
    assert initial["summary"]["pending_receipts"] == 1 and initial["summary"]["usage_receipts"] == 0
    store.finish(task, permit["dispatch_attempt_id"], {"status": "complete", "answer": "private answer"},
                 21, "receipt", input_tokens=11, output_tokens=1)
    final = view()
    assert final["summary"]["settled_micros"] == "21" and final["summary"]["reserved_micros"] == "0"
    assert final["summary"]["dispatches"] == 1 and final["summary"]["input_tokens"] == "11"
    assert final["summary"]["pending_receipts"] == 0 and final["summary"]["usage_receipts"] == 1
    assert final["summary"]["succeeded"] == 1
    trace = store.get_timeline(tenant, "alice", task["task_id"], allowed_projects=("support",))
    model = [e for e in trace["events"] if e["kind"] == "model"]
    assert [e["event"] for e in model] == ["dispatch_admitted", "receipt_settled"]
    assert all(e["snapshot_id"] == snapshot["snapshot_id"] for e in model)
    assert model[1]["receipt_ref"] == sha256(b"receipt").hexdigest()
    assert "private answer" not in json.dumps([trace, final])


def test_ac023_capability_identity_is_frozen_across_releases(deployment):
    store, tenant, _, cap = deployment
    register(store, {"fixture": tenant, "revision": 2, "capabilities": [cap]})
    with pytest.raises(PlatformError) as conflict:
        register(store, {"fixture": tenant, "capabilities": [dict(cap, digest="c"*64)]})
    assert conflict.value.code == "VERSION_CONFLICT"
    register(store, {"fixture": tenant, "capabilities": [dict(cap, version="2", digest="c"*64)]})
    task = claim(deployment)
    with pytest.raises(PlatformError) as changed:
        store.start_capability(task, **dict(cap, version="2", digest="c"*64))
    assert changed.value.code == "FORBIDDEN"


def test_ac012_013_span_parent_and_current_lease_must_match(deployment):
    store, _, _, cap = deployment
    first = claim(deployment)
    parent = start_context(store, first)
    other = claim(deployment)
    with pytest.raises(PlatformError) as wrong_parent:
        store.start_capability(other, **cap, parent_span_id=parent)
    assert wrong_parent.value.code == "STATE_CONFLICT"
    with pytest.raises(PlatformError) as stale:
        store.start_capability(dict(first, lease_epoch=first["lease_epoch"]+1), **cap)
    assert stale.value.code == "LEASE_LOST"
    with pytest.raises(PlatformError) as stale_finish:
        store.finish_capability(dict(first, lease_epoch=first["lease_epoch"]+1), parent)
    assert stale_finish.value.code == "LEASE_LOST"


def test_ac023_model_admission_requires_exact_pinned_and_observed_capabilities(deployment):
    store, tenant, _, cap = deployment
    skill = {"kind": "skill", "name": "guide-" + tenant, "version": "1", "digest": "d"*64,
             "text_sha256": sha256(b"trusted guidance").hexdigest()}
    release = register(store, {"capabilities": [cap, skill]})
    task = claim((store, tenant, release, cap))
    epoch, _ = store.evidence(task)
    snap = {"snapshot_id": str(uuid4()), "task_id": task["task_id"], "agent_version_id": release,
            "attempt": task["attempt"], "identity_ref": "alice", "authorization_revision": epoch,
            "source_refs": [], "payload_digest": "b"*64, "retention_mode": "metadata_only"}
    with pytest.raises(PlatformError) as omitted:
        store.admit(task, snap, 5)
    assert omitted.value.code == "CONTEXT_STALE"
    skill_ref = {k: skill[k] for k in ("kind", "name", "version", "digest")}
    snap["capability_refs"] = [cap, skill_ref]
    snap["skill_text_sha256"] = {skill["name"]: skill["text_sha256"]}
    ctx = start_context(store, task)
    store.finish_capability(task, ctx, snapshot_id=snap["snapshot_id"])
    with pytest.raises(PlatformError) as unexecuted:
        store.admit(task, snap, 5)
    assert unexecuted.value.code == "CONTEXT_STALE"
    snap["snapshot_id"] = str(uuid4())
    ctx = start_context(store, task)
    for ref in (cap, skill_ref):
        store.finish_capability(task, store.start_capability(task, **ref, parent_span_id=ctx))
    store.finish_capability(task, ctx, snapshot_id=snap["snapshot_id"])
    with pytest.raises(PlatformError) as changed_body:
        store.admit(task, dict(snap, skill_text_sha256={skill["name"]: "f"*64}), 5)
    assert changed_body.value.code == "CONTEXT_STALE"
    assert store.admit(task, snap, 5)["dispatch_attempt_id"]


def test_ac013_p95_of_completed_spans_and_time_window(deployment):
    store, tenant, _, cap = deployment
    task = claim(deployment)
    for duration_ms in range(1, 21):
        span = store.start_capability(task, **cap)
        store.finish_capability(task, span, error_code="TOOL_SCHEMA_CHANGED" if duration_ms == 19 else None)
        # Give the stored observations deterministic elapsed times without introducing sleeps.
        store._tx(lambda cur: cur.execute("""UPDATE ag_capability_span
                                            SET ended_at=TIMESTAMPADD(MICROSECOND,%s,started_at)
                                            WHERE span_id=%s""", (1000*duration_ms, span)))
    store.start_capability(task, **cap)
    old = claim(deployment)
    store._tx(lambda cur: cur.execute("""UPDATE ag_task SET monitor_created_at=UTC_TIMESTAMP(6)-INTERVAL 2 HOUR
                                        WHERE task_id=%s""", (old["task_id"],)))
    report = store.get_monitoring(tenant, "alice", "support", hours=1, allowed_projects=("support",))
    assert report["summary"]["tasks"] == 1
    timing, = report["capabilities"]
    assert timing["p95_duration_ms"] == 19 and timing["max_duration_ms"] == 20
    assert timing["calls"] == 21 and timing["unfinished"] == 1
