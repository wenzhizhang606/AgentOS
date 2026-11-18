"""AC-012/013 run inspection on a disposable MySQL 8.4 database with migrations 001–003."""

import base64
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
SCOPE = {"allowed_projects": ("support",)}


def register(store, tenant, revision):
    manifest = {"profile": "readonly-support/1", "fixture": tenant, "revision": revision}
    release = sha256(json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    store.register_release(release, manifest)
    return release


@pytest.fixture
def deployment():
    store = MySQLStore.from_env()
    tenant = uuid4().hex
    for subject in ("alice", "bob", "observer"):
        store.set_access(tenant, subject, "support", True)
    return store, tenant, register(store, tenant, 1)


def enqueue(store, tenant, release, subject="alice"):
    request = {"project_id": "support", "agent_version_id": release, "goal": "private goal",
               "limit_micros": 500, "deadline_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()}
    return store.create_task(tenant, subject, uuid4().hex, request)


def claimed(store, tenant, release, subject="alice", worker="worker-1"):
    queued = enqueue(store, tenant, release, subject)
    task = store.claim(worker, lease_seconds=3600, agent_version_id=release)
    assert task["task_id"] == queued["task_id"]
    return task


def reclaim(store, task):
    store._tx(lambda cur: cur.execute("""UPDATE ag_task SET ready_at=UTC_TIMESTAMP(6)-INTERVAL 1 SECOND,
                                         lease_until=UTC_TIMESTAMP(6)-INTERVAL 1 SECOND WHERE task_id=%s""",
                                      (task["task_id"],)))
    second = store.claim("worker-2", lease_seconds=3600, agent_version_id=task["agent_version_id"])
    assert second["task_id"] == task["task_id"] and second["attempt"] == task["attempt"] + 1
    return second


def test_ac013_retry_has_distinct_spans_and_late_receipt_belongs_to_its_dispatch(deployment):
    store, tenant, release = deployment
    first = claimed(*deployment)
    task_id = first["task_id"]
    old_span = store.start_capability(first, "context", "prepare_context", "evidence/1", release)
    second = reclaim(store, first)
    new_span = store.start_capability(second, "context", "prepare_context", "evidence/1", release)
    snapshot_id = str(uuid4())
    store.finish_capability(second, new_span, snapshot_id=snapshot_id)
    epoch, _ = store.evidence(second)
    snapshot = {"snapshot_id": snapshot_id, "task_id": task_id, "agent_version_id": release, "attempt": 2,
                "identity_ref": "alice", "authorization_revision": epoch, "source_refs": [],
                "model": "fixture-model", "payload_digest": "b" * 64, "retention_mode": "metadata_only"}
    permit = store.admit(second, snapshot, 34)
    pending = store.get_run_trace(tenant, "alice", task_id, 2, **SCOPE)
    assert pending["accounting"] == {"state": "pending", "reserved_micros": "34", "settled_micros": "0",
                                     "input_tokens": None, "output_tokens": None}
    version = store.get_task(tenant, "alice", task_id)["version"]
    store.cancel_task(tenant, "alice", task_id, uuid4().hex, version, **SCOPE)
    ended = store.get_run_trace(tenant, "alice", task_id, 2, **SCOPE)["ended_at"]
    store.finish(second, permit["dispatch_attempt_id"], {"status": "complete", "answer": "private answer"},
                 22, "private receipt", input_tokens=11, output_tokens=3)

    # Simulate an old claim without the worker field. Its own capability span still identifies it.
    store._tx(lambda cur: cur.execute("""UPDATE ag_task_event SET worker_id=NULL
                                         WHERE task_id=%s AND attempt=1 AND event_type='claimed'""", (task_id,)))
    old = store.get_run_trace(tenant, "alice", task_id, 1, **SCOPE)
    assert (old["worker_id"], old["state"], old["ended_at"], old["error_code"]) == ("worker-1", "superseded", None, None)
    assert old["run_id"] == f"{task_id}:1" and old["has_capability_spans"]
    assert old["accounting"]["state"] == "no_dispatch"
    assert [e["event"] for e in old["events"]] == ["claimed", "started"]
    assert old["events"][-1]["span_id"] == old_span
    latest = store.get_run_trace(tenant, "alice", task_id, 2, **SCOPE)
    assert (latest["state"], latest["error_code"], latest["ended_at"]) == ("cancelled", "CANCELLED", ended)
    assert ended is not None and all(e["attempt"] == 2 for e in latest["events"])
    assert latest["accounting"] == {"state": "final", "reserved_micros": "0", "settled_micros": "22",
                                    "input_tokens": "11", "output_tokens": "3"}
    events = latest["events"]
    assert [e["occurred_at"] for e in events] == sorted(e["occurred_at"] for e in events)
    assert [e["event"] for e in events if e.get("span_id") == new_span] == ["started", "succeeded"]
    receipt, = [e for e in events if e["event"] == "receipt_settled"]
    assert receipt["receipt_ref"] == sha256(b"private receipt").hexdigest() and receipt["snapshot_id"] == snapshot_id
    assert old_span not in json.dumps(latest) and "private" not in json.dumps([latest, old])


def test_ac013_missing_legacy_worker_and_unclaimed_tasks(deployment):
    store, tenant, release = deployment
    task = claimed(*deployment)
    task_id = task["task_id"]
    second = reclaim(store, task)
    store.fail(second, "MISSING_EVIDENCE")
    store._tx(lambda cur: cur.execute("""UPDATE ag_task_event SET worker_id=NULL
                                         WHERE task_id=%s AND attempt=1 AND event_type='claimed'""", (task_id,)))
    old = store.get_run_trace(tenant, "alice", task_id, 1, **SCOPE)
    assert old["worker_id"] is None and not old["has_capability_spans"]
    assert old["accounting"]["input_tokens"] is None
    latest = store.get_run_trace(tenant, "alice", task_id, 2, **SCOPE)
    assert latest["worker_id"] == "worker-2" and latest["state"] == "failed"
    assert latest["error_code"] == "MISSING_EVIDENCE"
    queued = enqueue(*deployment)
    with pytest.raises(PlatformError) as missing:
        store.get_run_trace(tenant, "alice", queued["task_id"], 1, **SCOPE)
    assert missing.value.code == "RESOURCE_NOT_FOUND"
    directory = store.list_observed_agents(tenant, "alice", "support", **SCOPE)
    assert directory["agents"][0]["run_count"] == 2
    runs = store.get_agent_runs(tenant, "alice", "support", release, **SCOPE)["runs"]
    assert [(run["attempt"], run["worker_id"]) for run in runs] == [(2, "worker-2"), (1, None)]


def test_ac013_window_and_keyset_pagination_for_versions_and_runs(deployment):
    store, tenant, first_release = deployment
    other_release = register(store, tenant, 2)
    first = claimed(*deployment)
    second = claimed(*deployment)
    older = claimed(*deployment)
    store._tx(lambda cur: cur.execute("""UPDATE ag_task_event SET occurred_at=UTC_TIMESTAMP(6)-INTERVAL 2 HOUR
                                         WHERE task_id=%s AND event_type='claimed'""", (older["task_id"],)))
    claimed(store, tenant, other_release)
    options = dict(SCOPE, hours=1, limit=1)
    pages = store.list_observed_agents(tenant, "alice", "support", **options)
    assert pages["agents"][0]["agent_version_id"] == other_release
    next_page = store.list_observed_agents(tenant, "alice", "support", cursor=pages["next_cursor"], **options)
    agent, = next_page["agents"]
    assert (agent["profile"], agent["agent_version_id"], agent["run_count"]) == ("readonly-support/1", first_release, 2)
    assert next_page["window_end"] == pages["window_end"] and next_page["next_cursor"] is None
    store.get_run_trace(tenant, "alice", older["task_id"], 1, **SCOPE)

    # Force timestamps to tie. Pagination must use task_id as a stable tiebreaker.
    store._tx(lambda cur: cur.execute("""UPDATE ag_task_event SET occurred_at=UTC_TIMESTAMP(6)-INTERVAL 1 MINUTE
                                         WHERE task_id IN (%s,%s) AND event_type='claimed'""",
                                      (first["task_id"], second["task_id"])))
    run_args = (tenant, "alice", "support", first_release)
    initial = store.get_agent_runs(*run_args, **options)
    cursor = initial["next_cursor"]
    claimed(*deployment)  # Started after the pinned window, must not shift the second page.
    final = store.get_agent_runs(*run_args, cursor=cursor, **options)
    assert final["window_end"] == initial["window_end"] and final["next_cursor"] is None
    assert [page["runs"][0]["task_id"] for page in (initial, final)] == sorted(
        [first["task_id"], second["task_id"]], reverse=True)
    forged = json.loads(base64.urlsafe_b64decode(cursor))
    forged[3] = chr(0xD800)
    invalid_utf8_id = base64.urlsafe_b64encode(json.dumps(forged).encode()).decode()
    for invalid in ("%%%", pages["next_cursor"], invalid_utf8_id):
        with pytest.raises(PlatformError) as bad_cursor:
            store.get_agent_runs(*run_args, cursor=invalid, **options)
        assert bad_cursor.value.code == "INVALID_ARGUMENT"


def test_ac012_013_subject_project_tenant_and_revocation(deployment):
    store, tenant, release = deployment
    alice = claimed(*deployment)
    bob = claimed(*deployment, subject="bob")
    owned = store.get_agent_runs(tenant, "alice", "support", release, **SCOPE)
    assert owned["scope"] == "subject" and [r["task_id"] for r in owned["runs"]] == [alice["task_id"]]
    other_tenant = uuid4().hex
    store.set_access(other_tenant, "observer", "support", True)
    for request_tenant, actor, projects, monitors in [
        (tenant, "bob", ("support",), ()), (tenant, "observer", (), ("support",)),
        (other_tenant, "observer", ("support",), ("support",)),
    ]:
        with pytest.raises(PlatformError) as denied:
            store.get_run_trace(request_tenant, actor, alice["task_id"], 1,
                                allowed_projects=projects, monitor_project_ids=monitors)
        assert denied.value.code == "RESOURCE_NOT_FOUND"
    observed = store.get_agent_runs(tenant, "observer", "support", release, **SCOPE, include_all_subjects=True)
    assert observed["scope"] == "project" and {r["task_id"] for r in observed["runs"]} == {alice["task_id"], bob["task_id"]}
    trace_args = (tenant, "observer", bob["task_id"], 1)
    assert store.get_run_trace(*trace_args, **SCOPE, monitor_project_ids=("support",))["worker_id"] == "worker-1"
    assert not store.list_observed_agents(other_tenant, "observer", "support", **SCOPE, include_all_subjects=True)["agents"]
    store.set_access(tenant, "observer", "support", False)
    with pytest.raises(PlatformError) as revoked:
        store.get_run_trace(*trace_args, **SCOPE, monitor_project_ids=("support",))
    assert revoked.value.code == "RESOURCE_NOT_FOUND"
    with pytest.raises(PlatformError) as forbidden:
        store.list_observed_agents(tenant, "observer", "support", **SCOPE, include_all_subjects=True)
    assert forbidden.value.code == "FORBIDDEN"


def test_ac013_timeline_limit_is_applied_after_attempt_selection(deployment):
    store, tenant, release = deployment
    first = claimed(*deployment)
    original = store.start_capability(first, "context", "prepare_context", "evidence/1", release)
    second = reclaim(store, first)
    task_id = first["task_id"]
    span_ids = sorted(uuid4().hex for _ in range(501))

    def populate(cur):
        # Tie all three event streams to one exact timestamp. Pages cut through all three streams.
        cur.execute("SELECT occurred_at,sequence_no FROM ag_task_event WHERE task_id=%s AND attempt=2", (task_id,))
        claim = cur.fetchone()
        instant = claim["occurred_at"]
        cur.executemany("""INSERT INTO ag_capability_span (span_id,task_id,attempt,worker_id,lease_epoch,kind,
                        name,version,digest,outcome,started_at,ended_at)
                        VALUES (%s,%s,2,%s,%s,'context','prepare_context','evidence/1',%s,'succeeded',%s,%s)""",
                        [(span, task_id, second["worker_id"], second["lease_epoch"], release, instant, instant)
                         for span in span_ids])
        cur.executemany("""INSERT INTO ag_task_event (event_id,task_id,sequence_no,attempt,event_type,occurred_at)
                           VALUES (%s,%s,%s,2,'observation',%s)""",
                        [(uuid4().hex, task_id, claim["sequence_no"] + offset, instant) for offset in range(1, 502)])
        return claim["sequence_no"]

    claim_sequence = store._tx(populate)
    old = store.get_run_trace(tenant, "alice", task_id, 1, **SCOPE)
    assert not old["truncated"] and [e.get("span_id") for e in old["events"]] == [None, original]
    current = store.get_run_trace(tenant, "alice", task_id, 2, **SCOPE)
    assert current["truncated"] and current["has_capability_spans"] and len(current["events"]) == 500
    with pytest.raises(PlatformError) as other_attempt:
        store.get_run_trace(tenant, "alice", task_id, 1, **SCOPE, before=current["next_before"])
    assert other_attempt.value.code == "INVALID_ARGUMENT"
    events = current["events"]
    for expected_length in (500, 500, 4):
        current = store.get_run_trace(tenant, "alice", task_id, 2, **SCOPE, before=current["next_before"])
        assert len(current["events"]) == expected_length
        events = current["events"] + events
    assert current["next_before"] is None and not current["truncated"]
    assert all(e["attempt"] == 2 for e in events)
    observed = [(e["event"], e.get("span_id", e.get("sequence_no"))) for e in events]
    expected = [(event, span) for event in ("started", "succeeded") for span in span_ids]
    expected += [("claimed", claim_sequence)] + [("observation", claim_sequence + i) for i in range(1, 502)]
    assert observed == expected
