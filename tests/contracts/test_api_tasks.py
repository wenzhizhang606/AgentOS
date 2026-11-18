"""Public ingress and credential-boundary cases for the M1 read-only profile."""

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from unittest.mock import Mock

from fastapi.testclient import TestClient
import pytest

from agentos.api.app import TaskStore, create_app
from agentos.contracts.errors import PlatformError
from agentos.governance.auth import Authenticator


UTC = timezone.utc
RELEASE = "5" * 64
TOKEN = "issued-to-alice"
HEADERS = {"Authorization": f"Bearer {TOKEN}", "Idempotency-Key": "create-1"}


def timestamp(delta):
    return (datetime.now(UTC) + delta).isoformat().replace("+00:00", "Z")


@pytest.fixture
def credential():
    return {
        "token_sha256": sha256(TOKEN.encode()).hexdigest(),
        "tenant_id": "tenant-A", "subject_id": "alice", "project_ids": ["support"],
        "expires_at": timestamp(timedelta(days=1)),
    }


def load_credentials(tmp_path, records):
    path = tmp_path / "credentials.json"
    path.write_text(json.dumps({"credentials": records}), encoding="utf-8")
    return Authenticator.from_file(path)


@pytest.fixture
def client_store(tmp_path, credential):
    auth = load_credentials(tmp_path, [credential])
    store = Mock(spec=TaskStore)
    store.create_task.return_value = {"task_id": "t1", "state": "queued", "version": 1}
    application = create_app(store, auth, RELEASE)
    with TestClient(application) as client:
        yield client, store


@pytest.fixture
def task():
    return {
        "agent_version_id": RELEASE, "goal": "给出该订单的售后期限和依据", "project_id": "support",
        "completion_criteria_ref": "criteria_support_readonly_v1",
        "deadline_at": timestamp(timedelta(minutes=20)),
        "budget": {"limit_micros": 10_000, "currency": "CNY"},
    }


def test_ac_001_ac_012_submit_derives_identity_from_bearer(client_store, task):
    client, store = client_store
    response = client.post("/v1/tasks", headers={**HEADERS, "X-Tenant-Id": "tenant-B"}, json=task)
    assert response.status_code == 202
    assert response.json() == store.create_task.return_value
    store.create_task.assert_called_once_with("tenant-A", "alice", "create-1", {
        "project_id": "support", "agent_version_id": RELEASE, "goal": task["goal"],
        "deadline_at": task["deadline_at"], "limit_micros": 10_000,
    }, replay_only=False)


def test_ac_013_monitoring_uses_server_side_scope(client_store):
    client, store = client_store
    store.get_monitoring.return_value = {"scope": "subject"}
    response = client.get("/v1/projects/support/monitoring?include_all_subjects=true", headers=HEADERS)
    assert response.status_code == 200 and response.json()["scope"] == "subject"
    store.get_monitoring.assert_called_once_with("tenant-A", "alice", "support",
                                                 allowed_projects=("support",), include_all_subjects=False,
                                                 hours=24, limit=50)
    assert client.get("/v1/projects/payroll/monitoring", headers=HEADERS).status_code == 403
    assert client.get("/v1/projects/support/monitoring?hours=169", headers=HEADERS).status_code == 400
    assert store.get_monitoring.call_count == 1


def test_ac_013_monitoring_grant_is_project_scoped(tmp_path, credential):
    credential["monitor_project_ids"] = ["payroll"]
    with pytest.raises(ValueError):
        load_credentials(tmp_path, [credential])
    credential["monitor_project_ids"] = ["support"]
    auth = load_credentials(tmp_path, [credential])
    store = Mock(spec=TaskStore)
    store.get_monitoring.return_value = {"scope": "project"}
    store.get_timeline.return_value = {"events": []}
    catalog = [{"kind": "skill", "name": "guidance", "version": "1", "digest": "b" * 64}]
    with TestClient(create_app(store, auth, RELEASE, capability_catalog=catalog)) as client:
        info = client.get("/v1/projects/support/monitoring?hours=12&limit=10", headers=HEADERS).json()
        assert info["active_release"] == {"version_id": RELEASE, "capabilities": catalog}
        assert store.get_monitoring.call_args.kwargs == {
            "allowed_projects": ("support",), "include_all_subjects": True, "hours": 12, "limit": 10,
        }
        assert client.get("/v1/tasks/t1/timeline", headers=HEADERS).status_code == 200
        store.get_timeline.assert_called_once_with("tenant-A", "alice", "t1", allowed_projects=("support",),
                                                   monitor_project_ids=("support",))


def test_ac_013_023_resources_show_active_release_even_without_activity(tmp_path, credential):
    store = Mock(spec=TaskStore)
    store.get_monitoring.return_value = {"summary": {"tasks": 0}}
    entries = [
        {"kind": "skill", "name": "guidance", "version": "1", "digest": "a" * 64},
        {"kind": "tool", "name": "lookup", "version": "2", "digest": "b" * 64},
        {"kind": "skill", "name": "returns", "version": "3", "digest": "c" * 64},
    ]
    catalog = [dict(entry, text="private prompt", auth_ref="private credential") for entry in entries]
    catalog.append({"kind": "context", "name": "private context"})
    with TestClient(create_app(store, load_credentials(tmp_path, [credential]), RELEASE,
                               capability_catalog=catalog)) as client:
        catalog[0]["version"] = "unpublished"
        catalog.clear()
        response = client.get("/v1/projects/support/resources", headers=HEADERS)
        assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
        assert response.json() == {"project_id": "support", "agent_version_id": RELEASE,
                                   "counts": {"skill": 2, "tool": 1}, "resources": entries}
        store.require_project_access.assert_called_once_with("tenant-A", "alice", "support",
                                                             allowed_projects=("support",))
        store.get_monitoring.assert_not_called()
        report = client.get("/v1/projects/support/monitoring", headers=HEADERS).json()
        assert report["active_release"] == {"version_id": RELEASE, "capabilities": entries}


def test_ac_012_013_resource_endpoints_keep_catalog_private(client_store):
    client, store = client_store
    page = client.get("/resources")
    monitor = client.get("/monitor")
    assert page.status_code == 200 and page.content == monitor.content and not store.mock_calls
    for header in ("content-security-policy", "referrer-policy", "cache-control"):
        assert page.headers[header] == monitor.headers[header]
    path = "/v1/projects/support/resources"
    assert client.get(path).status_code == 401
    assert client.get("/v1/projects/other/resources", headers=HEADERS).status_code == 403
    assert not store.mock_calls
    assert client.get(path, headers=HEADERS).json() == {
        "project_id": "support", "agent_version_id": RELEASE, "counts": {"skill": 0, "tool": 0}, "resources": []}
    store.require_project_access.side_effect = PlatformError("FORBIDDEN", "Project membership revoked")
    rejected = client.get(path, headers=HEADERS)
    assert rejected.status_code == 403 and "resources" not in rejected.json()


def test_ac_013_monitor_only_credential_reads_metadata_and_rejects_task_access(tmp_path, credential, task):
    credential["monitor_project_ids"] = ["support"]
    path = tmp_path / "credentials.json"
    path.write_text(json.dumps({"credentials": [], "monitor_credentials": [credential]}), encoding="utf-8")
    store = Mock(spec=TaskStore)
    for method in (store.get_monitoring, store.list_observed_agents, store.get_agent_runs,
                   store.get_timeline, store.get_run_trace):
        method.return_value = {}
    with TestClient(create_app(store, Authenticator.from_file(path), RELEASE)) as client:
        for route in ("/v1/projects/support/monitoring", "/v1/projects/support/resources", "/v1/projects/support/agents",
                      "/v1/projects/support/agents/ver/runs", "/v1/tasks/t1/timeline", "/v1/tasks/t1/runs/1/trace"):
            assert client.get(route, headers=HEADERS).status_code == 200
        store.reset_mock()
        assert client.get("/v1/projects/other/monitoring", headers=HEADERS).status_code == 403
        assert client.get("/v1/tasks/t1", headers=HEADERS).status_code == 403
        assert client.get("/v1/context-snapshots/s1", headers=HEADERS).status_code == 403
        assert client.post("/v1/tasks", json=task, headers=HEADERS).status_code == 403
        assert client.post("/v1/tasks/t1/cancel", headers={**HEADERS, "If-Match": "1"}).status_code == 403
        assert not store.mock_calls
    path.write_text(json.dumps({"credentials": [credential], "monitor_credentials": [credential]}), encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate"):
        Authenticator.from_file(path)


@pytest.mark.parametrize("project_monitor", [False, True])
def test_ac_013_browse_agent_then_read_one_attempt(tmp_path, credential, project_monitor):
    if project_monitor:
        credential["monitor_project_ids"] = ["support"]
    store = Mock(spec=TaskStore)
    store.list_observed_agents.return_value = {"agents": [{"agent_version_id": RELEASE}]}
    store.get_agent_runs.return_value = {"runs": [{"task_id": "t1", "attempt": 2}]}
    store.get_run_trace.return_value = {"events": [{"attempt": 2, "event": "claimed"}]}
    with TestClient(create_app(store, load_credentials(tmp_path, [credential]), RELEASE)) as client:
        base = "/v1/projects/support/agents"
        result = client.get(base + "?include_all_subjects=true", headers=HEADERS)
        assert result.status_code == 200 and result.json() == store.list_observed_agents.return_value
        store.list_observed_agents.assert_called_once_with(
            "tenant-A", "alice", "support", allowed_projects=("support",), include_all_subjects=project_monitor,
            hours=24, limit=50, cursor=None)
        runs = client.get(f"{base}/{RELEASE}/runs?hours=12&limit=5&cursor=page2", headers=HEADERS)
        assert runs.status_code == 200 and runs.json() == store.get_agent_runs.return_value
        store.get_agent_runs.assert_called_once_with(
            "tenant-A", "alice", "support", RELEASE, allowed_projects=("support",),
            include_all_subjects=project_monitor, hours=12, limit=5, cursor="page2")
        trace = client.get("/v1/tasks/t1/runs/2/trace?before=earlier&monitor_project_ids=support", headers=HEADERS)
        assert trace.status_code == 200 and trace.json() == store.get_run_trace.return_value
        assert trace.headers["Cache-Control"] == "no-store"
        store.get_run_trace.assert_called_once_with(
            "tenant-A", "alice", "t1", 2, allowed_projects=("support",),
            monitor_project_ids=("support",) if project_monitor else (), before="earlier")


@pytest.mark.parametrize(("path", "status"), [
    ("/v1/projects/payroll/agents", 403),
    (f"/v1/projects/payroll/agents/{RELEASE}/runs", 403),
    ("/v1/projects/support/agents?hours=169", 400),
    (f"/v1/projects/support/agents/{RELEASE}/runs?limit=201", 400),
    (f"/v1/projects/support/agents/{RELEASE}/runs?cursor=", 400),
    ("/v1/tasks/t1/runs/0/trace", 400),
    ("/v1/tasks/t1/runs/18446744073709551616/trace", 400),
    ("/v1/tasks/t1/runs/1/trace?before=", 400),
])
def test_ac_012_013_run_browser_rejects_invalid_scope_and_bounds(client_store, path, status):
    client, store = client_store
    assert client.get(path, headers=HEADERS).status_code == status
    assert not store.mock_calls


@pytest.mark.parametrize("path", ["/v1/projects/support/agents", f"/v1/projects/support/agents/{RELEASE}/runs",
                                  "/v1/tasks/t1/runs/1/trace"])
def test_ac_012_run_browser_requires_credentials(client_store, path):
    client, store = client_store
    assert client.get(path).status_code == 401
    assert not store.mock_calls


@pytest.mark.parametrize("authorization", [None, "Basic arbitrary", "Bearer invalid"])
def test_ac_012_invalid_credentials_never_reach_store(client_store, task, authorization):
    client, store = client_store
    headers = {"Idempotency-Key": "op1"}
    if authorization:
        headers["Authorization"] = authorization
    response = client.post("/v1/tasks", headers=headers, json=task)
    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"
    assert response.json()["error"]["request_id"] == response.headers["X-Request-Id"]
    assert not store.mock_calls


def test_ac_012_expired_token_and_duplicate_provisioning(tmp_path, credential):
    credential["expires_at"] = timestamp(-timedelta(seconds=1))
    auth = load_credentials(tmp_path, [credential])
    with pytest.raises(PlatformError) as error:
        auth.authenticate(f"Bearer {TOKEN}")
    assert error.value.code == "UNAUTHENTICATED"
    with pytest.raises(ValueError, match="duplicate"):
        load_credentials(tmp_path, [credential, credential])


@pytest.mark.parametrize(("change", "status"), [
    ({"tenant_id": "tenant-B"}, 400),
    ({"project_id": "payroll"}, 403),
    ({"completion_criteria_ref": "arbitrary"}, 400),
    ({"input_refs": ["unresolved-ref"]}, 400),
    ({"deadline_at": "2199-01-01T00:00:00Z"}, 400),
    ({"deadline_at": "2030-01-01"}, 400),
    ({"budget": {"currency": "CNY", "limit_micros": True}}, 400),
    ({"budget": {"currency": "USD", "limit_micros": 1}}, 400),
    ({"budget": {"currency": "CNY", "limit_micros": 1, "max_concurrency": 2}}, 400),
])
def test_ac_009_ac_012_submit_checks_scope_release_and_budget(client_store, task, change, status):
    client, store = client_store
    response = client.post("/v1/tasks", headers=HEADERS, json={**task, **change})
    assert response.status_code == status
    assert response.json()["error"]["retryable"] is False
    assert not store.mock_calls


def test_ac_008_old_release_can_only_replay_a_committed_task(client_store, task):
    client, store = client_store
    task["agent_version_id"] = "4" * 64
    store.create_task.side_effect = [
        {"task_id": "old-task", "state": "queued", "version": 1},
        PlatformError("INVALID_ARGUMENT", "Unsupported Agent release"),
    ]
    result = client.post("/v1/tasks", headers=HEADERS, json=task)
    assert result.status_code == 202
    assert result.json()["task_id"] == "old-task"
    assert store.create_task.call_args.kwargs == {"replay_only": True}
    assert store.create_task.call_args.args[3]["agent_version_id"] == task["agent_version_id"]
    missing = client.post("/v1/tasks", headers={**HEADERS, "Idempotency-Key": "new"}, json=task)
    assert missing.status_code == 400
    assert store.create_task.call_args.kwargs == {"replay_only": True}


def test_ac_012_invalid_unicode_is_rejected_without_leaking_request(client_store, task):
    client, store = client_store
    task["goal"] = "private-" + chr(0xd800)
    response = client.post("/v1/tasks", headers={**HEADERS, "Content-Type": "application/json"},
                           content=json.dumps(task, ensure_ascii=True))
    assert response.status_code == 400
    assert "private-" not in response.text
    store.create_task.assert_not_called()


def test_ac_008_idempotency_key_required_and_conflict_reported(client_store, task):
    client, store = client_store
    assert client.post("/v1/tasks", headers={"Authorization": HEADERS["Authorization"]},
                       json=task).status_code == 400
    store.create_task.assert_not_called()
    store.create_task.side_effect = PlatformError("IDEMPOTENCY_CONFLICT", "Key used for another request")
    result = client.post("/v1/tasks", headers=HEADERS, json=task)
    assert result.status_code == 409
    assert result.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"


def test_ac_008_read_version_and_conditional_cancel(client_store):
    client, store = client_store
    task = {"task_id": "t1", "state": "queued", "version": 2}
    store.get_task.return_value = task
    current = client.get("/v1/tasks/t1", headers=HEADERS)
    assert current.json() == task
    assert current.headers["ETag"] == '"2"'
    store.get_task.assert_called_once_with("tenant-A", "alice", "t1", allowed_projects=("support",))
    for etag in ["*", 'W/"2"', "0", "2,3"]:
        assert client.post("/v1/tasks/t1/cancel", headers={**HEADERS, "If-Match": etag}).status_code == 400
    store.cancel_task.assert_not_called()
    store.cancel_task.return_value = {**task, "state": "cancelled", "version": 3}
    result = client.post("/v1/tasks/t1/cancel", headers={**HEADERS, "If-Match": current.headers["ETag"]})
    assert result.status_code == 200
    assert result.json() == store.cancel_task.return_value
    store.cancel_task.assert_called_once_with("tenant-A", "alice", "t1", "create-1", 2,
                                             allowed_projects=("support",))


def test_ac_001_ac_012_snapshot_checks_current_scope_on_every_read(client_store):
    client, store = client_store
    store.get_snapshot.side_effect = [
        {"snapshot_id": "ctx", "evidence": ["allowed"]},
        PlatformError("RESOURCE_NOT_FOUND", "Snapshot unavailable"),
    ]
    path = "/v1/context-snapshots/ctx"
    first = client.get(path, headers=HEADERS)
    assert first.status_code == 200
    assert first.headers["Cache-Control"] == "no-store"
    second = client.get(path, headers=HEADERS)
    assert second.status_code == 404
    assert "allowed" not in second.text
    assert store.get_snapshot.call_count == 2
    store.get_snapshot.assert_called_with("tenant-A", "alice", "ctx", allowed_projects=("support",))
