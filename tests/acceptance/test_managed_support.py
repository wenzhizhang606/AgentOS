"""M1 API -> MySQL -> worker -> model double -> authorized delivery."""

from datetime import datetime, timedelta, timezone
from hashlib import sha256
from io import BytesIO
import json
import os
from pathlib import Path
from unittest.mock import Mock
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest

from agentos.adapters.mysql.store import MySQLStore
from agentos.api.app import create_app
from agentos.contracts.errors import PlatformError
from agentos.control.release import Release
from agentos.gateways.model.client import ChatModel, ModelAnswer
from agentos.governance.auth import Authenticator, Identity
from agentos.runtime.managed_task import run_one


pytestmark = [pytest.mark.mysql, pytest.mark.skipif(os.getenv("AGENTOS_TEST_MYSQL") != "1",
              reason="Requires a migrated, disposable MySQL database")]


@pytest.fixture
def deployment(request):
    store = MySQLStore.from_env()
    tenant = uuid4().hex
    config = {}
    if getattr(request, "param", None) == "with_capabilities":
        sample = Path(__file__).resolve().parents[2] / "examples/m1/release-capabilities.json"
        config["capabilities"] = json.loads(sample.read_text(encoding="utf-8"))["capabilities"]
    release = Release("https://model.example.invalid/v1/chat/completions", tenant, 8192,
                      128, 64, 1_000_000, 2_000_000, "metadata_only", **config)
    token = uuid4().hex
    now = datetime.now(timezone.utc)
    identity = Identity(tenant, "operator", ("support",), now + timedelta(hours=1))
    auth = Authenticator({sha256(token.encode()).hexdigest(): identity})
    store.register_release(release.version_id, release.manifest)
    store.set_access(tenant, "operator", "support", True)
    store.put_source(tenant, "support", "return-policy", "售后政策", "签收后七天内可退货。", "section-1")
    client = TestClient(create_app(store, auth, release.version_id,
                                   capability_catalog=release.catalog_metadata()))
    client.headers.update({"Authorization": "Bearer " + token})
    body = {"agent_version_id": release.version_id, "project_id": "support",
               "goal": "售后政策允许多少天内退货？", "completion_criteria_ref": "criteria_support_readonly_v1",
               "deadline_at": (now + timedelta(minutes=5)).isoformat().replace("+00:00", "Z"),
               "budget": {"currency": "CNY", "limit_micros": 10000}}
    with client:
        yield store, release, identity, client, body


class Model:
    def __init__(self, unknown=False):
        self.calls = 0
        self.unknown = unknown

    def generate(self, prepared, *, send_before):
        self.calls += 1
        if self.unknown:
            raise PlatformError("EXTERNAL_OUTCOME_UNKNOWN", "Simulated lost receipt")
        return ModelAnswer("七天 [E1]", prepared.source_refs, 100, 10, "receipt-" + prepared.snapshot_id)


def submit(client, request):
    response = client.post("/v1/tasks", headers={"Idempotency-Key": "create"}, json=request)
    assert response.status_code == 202, response.text
    return response.json()["task_id"]


@pytest.mark.parametrize("deployment", ["with_capabilities"], indirect=True)
def test_ac_013_023_trace_from_skill_and_tool_to_settled_model(deployment, caplog):
    store, release, _, client, request = deployment
    task_id = submit(client, request)
    model = ChatModel(release.endpoint, "fixture-key")
    model._transport = Mock()
    reply = {"id": "observed-receipt", "usage": {"prompt_tokens": 100, "completion_tokens": 10},
             "choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": "七天 [E1]"}}]}
    model._transport.open.return_value = BytesIO(json.dumps(reply).encode())
    with caplog.at_level("INFO", logger="agentos.runtime.managed_task"):
        assert run_one(store, model, release, "traced-worker")
    sent = json.loads(model._transport.open.call_args.args[0].data)
    model_input = json.loads(sent["messages"][1]["content"])
    assert model_input["skills"][0]["name"] == "support.answer"

    task_url = "/v1/tasks/" + task_id
    task = client.get(task_url).json()
    assert task["state"] == "succeeded"
    snapshot = client.get("/v1/context-snapshots/" + task["snapshot_id"]).json()
    assert snapshot["capability_refs"] == release.catalog_metadata()
    response = client.get(task_url + "/timeline")
    assert response.status_code == 200
    events = response.json()["events"]
    context, = [e for e in events if e["kind"] == "context" and e["event"] == "succeeded"]
    assert context["snapshot_id"] == task["snapshot_id"]
    successful = [e for e in events if e["kind"] in {"skill", "tool"} and e["event"] == "succeeded"]
    assert {e["digest"] for e in successful} == {c["digest"] for c in release.catalog_metadata()}
    assert all(e["parent_span_id"] == context["span_id"] for e in successful)
    assert model_input["skills"][0]["text"] not in response.text
    assert request["goal"] not in response.text
    assert any(e["event"] == "receipt_settled" and e["dispatch_attempt_id"] == task["dispatch_attempt_id"]
               for e in events)
    overview = client.get("/v1/projects/support/monitoring").json()
    assert overview["summary"]["tasks_with_spans"] == overview["summary"]["dispatches"] == 1
    assert overview["summary"]["settled_micros"] == "120"
    assert overview["active_release"]["capabilities"] == release.catalog_metadata()

    directory = "/v1/projects/support/agents"
    agent, = client.get(directory).json()["agents"]
    assert agent["agent_version_id"] == release.version_id
    run, = client.get(f"{directory}/{release.version_id}/runs").json()["runs"]
    assert (run["task_id"], run["attempt"], run["worker_id"]) == (task_id, 1, "traced-worker")
    assert f'run_id={run["run_id"]} ' in caplog.text
    trace = client.get(f'{task_url}/runs/{run["attempt"]}/trace')
    assert trace.status_code == 200
    detail = trace.json()
    assert detail["run_id"] == run["run_id"] and detail["state"] == "succeeded"
    assert detail["accounting"]["settled_micros"] == "120"
    assert {e["kind"] for e in detail["events"]} == {"task", "context", "skill", "tool", "model"}
    assert {e["attempt"] for e in detail["events"]} == {1}
    assert request["goal"] not in trace.text and model_input["skills"][0]["text"] not in trace.text


def test_ac_001_009_012_answer_charge_and_withdrawal(deployment):
    store, release, identity, client, request = deployment
    task_id = submit(client, request)
    assert submit(client, request) == task_id
    model = ChatModel(release.endpoint, "fixture-key")
    model._transport = Mock()
    reply = {"id": "provider-receipt", "usage": {"prompt_tokens": 100, "completion_tokens": 10},
             "choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": "七天 [E1]"}}]}
    model._transport.open.return_value = BytesIO(json.dumps(reply).encode())
    assert run_one(store, model, release, "worker")
    result = client.get("/v1/tasks/" + task_id).json()
    assert result["state"] == "succeeded"
    assert result["settled_micros"] == 120 and result["reserved_micros"] == 0
    answer = result["result"]
    assert answer["citations"][0]["source_id"] == "return-policy"
    snapshot_url = "/v1/context-snapshots/" + answer["snapshot_id"]
    snapshot = client.get(snapshot_url).json()
    assert snapshot["source_refs"] == answer["citations"]
    actual_request = model._transport.open.call_args.args[0]
    assert sha256(actual_request.data).hexdigest() == snapshot["wire_digest"]
    assert snapshot.get("retained_payload") is None
    assert not run_one(store, model, release, "worker-2")
    model._transport.open.assert_called_once()
    store.delete_source(identity.tenant_id, "support", "return-policy")
    assert client.get("/v1/tasks/" + task_id).json()["result"] is None
    assert client.get(snapshot_url).status_code in {403, 404, 409, 410}


def test_ac_009_unknown_outcome_is_not_redispatched(deployment):
    store, release, _, client, request = deployment
    task_id = submit(client, request)
    model = Model(unknown=True)
    assert run_one(store, model, release, "worker")
    run_one(store, model, release, "replacement")
    result = client.get("/v1/tasks/" + task_id).json()
    assert model.calls == 1
    assert result["accounting_state"] == "pending"
    assert result["reserved_micros"] == release.reservation_micros
    assert result["result"] is None


def test_ac_009_budget_denied_before_external_send(deployment):
    store, release, _, client, request = deployment
    request["budget"]["limit_micros"] = release.reservation_micros - 1
    task_id = submit(client, request)
    model = Model()
    assert run_one(store, model, release, "worker")
    result = client.get("/v1/tasks/" + task_id).json()
    assert result["error_code"] == "BUDGET_EXHAUSTED"
    assert result["settled_micros"] == model.calls == 0


def test_ac_012_revoke_between_preparation_and_admission(deployment, monkeypatch):
    store, release, identity, client, request = deployment
    task_id = submit(client, request)
    original = store.admit

    def revoke_then_admit(*args):
        store.set_access(identity.tenant_id, identity.subject_id, "support", False)
        return original(*args)

    monkeypatch.setattr(store, "admit", revoke_then_admit)
    model = Model()
    assert run_one(store, model, release, "worker")
    assert model.calls == 0
    assert client.get("/v1/tasks/" + task_id).status_code == 404
    store.set_access(identity.tenant_id, identity.subject_id, "support", True)
    task = client.get("/v1/tasks/" + task_id).json()
    assert task["error_code"] == "FORBIDDEN" and task["dispatch_attempt_id"] is None


def test_ac_002_revision_race_rebuilds_input_once(deployment, monkeypatch):
    store, release, identity, client, request = deployment
    task_id = submit(client, request)
    original = store.admit
    versions = []

    def revise_then_admit(task, snapshot, reserve, retained):
        versions.append(snapshot["source_refs"][0]["version"])
        if len(versions) == 1:
            store.put_source(identity.tenant_id, "support", "return-policy", "售后政策", "七天内退货。", "section-1")
        return original(task, snapshot, reserve, retained)

    monkeypatch.setattr(store, "admit", revise_then_admit)
    model = Model()
    assert run_one(store, model, release, "worker")
    assert versions == [1, 2] and model.calls == 1
    assert client.get("/v1/tasks/" + task_id).json()["result"]["citations"][0]["version"] == 2
