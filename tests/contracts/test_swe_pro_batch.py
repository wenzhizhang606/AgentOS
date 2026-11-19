"""AC-014. Exercise disk admission, resumable scoring and full-split budgets offline."""

from dataclasses import replace
from datetime import datetime, timezone
from hashlib import sha256
from types import SimpleNamespace

import pytest

from benchmarks import managed
from benchmarks import swe_pro_batch as batch
from benchmarks import swe_pro_prediction as prediction
from benchmarks.swe_pro_suite import IMAGE_REPOSITORY, SuiteTask


DIGEST = "sha256:" + "d" * 64
PATCH = "--- a/value.py\n+++ b/value.py\n@@ -1 +1 @@\n-old\n+new\n"
RELEASE = {
    "id": "fixture-release", "model": managed.ALLOWED_MODEL, "endpoint": managed.ALLOWED_ENDPOINT,
    "window_tokens": 8192, "max_output_tokens": 512, "safety_margin_tokens": 256,
    "empty_swe_wire_bytes": 650,
    "reservation_micros": 1000,
}


def make_task(index):
    label = f"instance_example__repo-{index:03d}"
    issue = f"Implement value formatting for example {index}."
    return SuiteTask(label, issue, sha256(issue.encode()).hexdigest(), f"{IMAGE_REPOSITORY}:{label}", 10240)


def forbidden(*args, **kwargs):
    pytest.fail("An unadmitted or already completed step was invoked")


@pytest.fixture
def offline(monkeypatch):
    # Fail immediately if a fixture accidentally reaches any deployment or Docker entry point.
    monkeypatch.setattr(managed, "compose_python", forbidden)
    monkeypatch.setattr(managed, "HTTPConnection", forbidden)
    monkeypatch.setattr(batch, "docker", forbidden)
    monkeypatch.setattr(prediction, "repository_evidence", lambda *args: {
        "head": "a" * 40, "files": [{"path": "value.py", "start_line": 1, "content": "old\n",
                                     "sha256": sha256(b"old\n").hexdigest()}],
    })


def fake_runner(tasks, calls, *, answer=PATCH + "[E1]"):
    def factory(labels):
        assert tuple(labels) == tuple(task.instance_id for task in tasks)

        def run(label, goal, sources):
            calls.append(label)
            assert goal == prediction.GOAL and sources[0]["location"] == label
            return {
                "state": "succeeded", "task_id": "fixture-task", "run_id": "fixture-run",
                "attempt": 1, "result": {"answer": answer, "citations": [
                    {"label": "E1", "source_id": prediction.SOURCE_ID, "location": label},
                ]},
            }

        return SimpleNamespace(release=RELEASE.copy(), run_task=run)

    return factory


def verdict(status, reason, reward, *, override=None):
    """Mimic grade()'s evidence directory and provenance without running a subprocess."""
    def verify(task_dir, digest, destination, *, patch):
        assert task_dir.parent.name == "tasks" and task_dir.parent.parent.name == "v2"
        assert digest == DIGEST and patch.read_bytes() == PATCH.encode()
        destination.mkdir()
        result = {
            "status": status, "reason": reason, "official_reward": reward, "instance_id": task_dir.name,
            "suite_commit": batch.SUITE_COMMIT, "image_digest": digest, "phase": "prediction",
            "patch_sha256": sha256(patch.read_bytes()).hexdigest(),
        }
        if reward is not None:
            result.update(fail_to_pass={"passed": reward, "total": 1}, pass_to_pass={"passed": 1, "total": 1})
        if override:
            result.update(override)
        managed.write_json(destination / "evaluation.json", result)
        return result

    return verify


def run(directory, tasks, **options):
    params = {
        "reserve_bytes": 2 * batch.GIB, "available": lambda: 20 * batch.GIB,
        "image": lambda *args: DIGEST, "runner_factory": forbidden, "predict": prediction.predict,
        "verifier": forbidden,
    }
    return batch.run_queue(directory, directory / "harness", tasks, **(params | options))


def test_ac014_full_queue_insufficient_disk_makes_zero_external_calls(tmp_path, offline):
    tasks = tuple(make_task(index) for index in range(642))
    result = run(tmp_path, tasks, available=lambda: 12 * batch.GIB - 1, fetch_images=True,
                 image=forbidden, predict=forbidden)
    assert result["paused"] == "insufficient_disk"
    assert result["next_case"] == tasks[0].instance_id
    assert result["total"] == result["pending"] == 642
    assert result["official_verdicts"] == result["completed"] == 0
    assert result["resolved_rate"] is None
    assert not list(tmp_path.rglob("inference.json"))


def test_ac014_capacity_rechecked_after_fetch_before_model(tmp_path, offline):
    task = make_task(0)
    free = iter([12 * batch.GIB, 12 * batch.GIB - 1])
    lookups = []

    def image(current, pinned, fetch):
        lookups.append((current.instance_id, pinned, fetch))
        return DIGEST

    result = run(tmp_path, [task], available=lambda: next(free), image=image, fetch_images=True,
                 predict=forbidden)
    assert lookups == [(task.instance_id, None, True)]
    assert result["paused"] == "insufficient_disk" and result["completed"] == 0
    assert result["resolved_rate"] is None


def test_ac014_infrastructure_retry_reuses_candidate_and_final_is_idempotent(tmp_path, offline):
    task = make_task(1)
    tasks = [task]
    calls = []
    lookups = []

    def image(current, pinned, fetch):
        lookups.append((current.instance_id, pinned))
        return DIGEST

    first = run(tmp_path, tasks, image=image, runner_factory=fake_runner(tasks, calls),
                verifier=verdict("infrastructure_error", "verifier_timed_out", None))
    assert first["infrastructure_error"] == 1 and first["completed"] == first["official_verdicts"] == 0
    assert first["resolved_rate"] is None
    case = batch.case_dir(tmp_path, task.instance_id)
    original = (case / "inference.json").read_bytes()
    second = run(tmp_path, tasks, image=image, predict=forbidden,
                 verifier=verdict("resolved", "official_verifier", 1))
    assert second["resolved"] == second["completed"] == second["official_verdicts"] == 1
    assert second["infrastructure_error"] == 0 and second["resolved_rate"] == 1
    assert (case / "inference.json").read_bytes() == original
    assert len(list(case.glob("verify-*/evaluation.json"))) == 2
    assert lookups == [(task.instance_id, None), (task.instance_id, DIGEST)]
    assert calls == [task.instance_id]
    replay = run(tmp_path, tasks, available=forbidden, image=forbidden, predict=forbidden)
    assert replay == second


def test_ac014_resume_after_verifier_commit_reuses_evidence_without_another_attempt(tmp_path, offline):
    tasks = [make_task(7)]
    calls = []

    def interrupted(*args, **kwargs):
        verdict("resolved", "official_verifier", 1)(*args, **kwargs)
        raise SystemExit("simulated interruption between evidence and state commits")

    with pytest.raises(SystemExit, match="interruption"):
        run(tmp_path, tasks, runner_factory=fake_runner(tasks, calls), verifier=interrupted)
    assert batch.summary(tmp_path)["awaiting_grade"] == 1
    recovered = run(tmp_path, tasks, predict=forbidden)
    assert recovered["completed"] == recovered["resolved"] == recovered["official_verdicts"] == 1
    assert len(list(tmp_path.rglob("evaluation.json"))) == len(calls) == 1


def test_ac014_evidence_budget_deducts_release_reservations_and_fixed_wire_bytes(tmp_path, offline):
    tasks = [make_task(8)]
    result = run(tmp_path, tasks, runner_factory=fake_runner(tasks, []),
                 verifier=verdict("resolved", "official_verifier", 1))
    assert result["completed"] == result["official_verdicts"] == 1
    inference = batch.read(batch.case_dir(tmp_path, tasks[0].instance_id) / "inference.json")
    ceiling = RELEASE["window_tokens"] - RELEASE["max_output_tokens"] - RELEASE["safety_margin_tokens"]
    assert 0 < inference["evidence_bytes"] <= ceiling - RELEASE["empty_swe_wire_bytes"]


@pytest.mark.parametrize(("answer", "grade_result", "state", "official"), [
    ("no patch", None, "agent_failure", 0),
    (PATCH + "[E1]", ("unresolved", "patch_rejected", None), "unresolved", 0),
    (PATCH + "[E1]", ("unresolved", "official_verifier", 0), "unresolved", 1),
])
def test_ac014_completed_failures_distinguish_official_verdicts(tmp_path, offline, answer, grade_result,
                                                              state, official):
    tasks = [make_task(2)]
    calls = []
    result = run(tmp_path, tasks, runner_factory=fake_runner(tasks, calls, answer=answer),
                 verifier=verdict(*grade_result) if grade_result else forbidden)
    assert len(calls) == 1
    assert result[state] == result["completed"] == 1
    assert result["official_verdicts"] == official and result["resolved_rate"] == 0


@pytest.mark.parametrize(("field", "value"), [
    ("instance_id", "instance_example__repo-other"), ("image_digest", "sha256:" + "f" * 64),
    ("suite_commit", "f" * 40), ("phase", "baseline"), ("patch_sha256", "e" * 64),
])
def test_ac014_wrong_verifier_provenance_cannot_score(tmp_path, offline, field, value):
    tasks = [make_task(3)]
    result = run(tmp_path, tasks, runner_factory=fake_runner(tasks, []),
                 verifier=verdict("resolved", "official_verifier", 1, override={field: value}))
    assert result["infrastructure_error"] == 1
    assert result["official_verdicts"] == result["completed"] == 0
    assert result["resolved_rate"] is None


@pytest.mark.parametrize("field", ["instance_id", "instruction_sha256", "image_digest", "patch_sha256"])
def test_ac014_drifted_inference_checkpoint_never_reaches_grader(tmp_path, offline, field):
    task = make_task(4)
    first = run(tmp_path, [task], runner_factory=fake_runner([task], []),
                verifier=verdict("infrastructure_error", "verifier_timed_out", None))
    assert first["infrastructure_error"] == 1
    inference = batch.case_dir(tmp_path, task.instance_id) / "inference.json"
    checkpoint = batch.read(inference)
    checkpoint[field] = "unrelated"
    managed.write_json(inference, checkpoint)
    retry = run(tmp_path, [task], predict=forbidden)
    assert retry["infrastructure_error"] == 1 and retry["completed"] == 0
    assert retry["resolved_rate"] is None


def test_ac014_freeze_prevents_changed_task_and_release_from_mixing(tmp_path, offline):
    tasks = [make_task(5), make_task(6)]
    calls = []
    first = run(tmp_path, tasks, runner_factory=fake_runner(tasks, calls),
                available=lambda: 0 if calls else 20 * batch.GIB,
                verifier=verdict("resolved", "official_verifier", 1))
    assert first["resolved"] == first["pending"] == 1 and first["resolved_rate"] is None
    assert first["next_case"] == tasks[1].instance_id
    snapshot = (tmp_path / "suite.json").read_bytes()
    with pytest.raises(ValueError, match="Frozen suite"):
        run(tmp_path, [tasks[0], replace(tasks[1], storage_mb=20480)], available=forbidden)
    assert (tmp_path / "suite.json").read_bytes() == snapshot
    changed = lambda _: SimpleNamespace(release=RELEASE | {"id": "another-release"}, run_task=forbidden)
    second = run(tmp_path, tasks, runner_factory=changed, predict=forbidden)
    assert second["infrastructure_error"] == 1 and second["resolved"] == 1
    assert second["official_verdicts"] == 1 and second["resolved_rate"] is None
    assert batch.read(tmp_path / "release.json") == RELEASE


def test_ac014_managed_runner_budgets_all_642_labels_and_rejects_unassigned(tmp_path, monkeypatch, offline):
    labels = tuple(make_task(index).instance_id for index in range(642))
    name = "bench-full"
    (tmp_path / "token").write_text("local-fixture-only", encoding="ascii")
    monkeypatch.setattr(managed, "private_dir", lambda _: tmp_path)
    monkeypatch.setattr(managed, "read_release", lambda: RELEASE.copy())
    requests = []
    monkeypatch.setattr(managed.ManagedRunner, "api", lambda self, method, path, **kwargs:
                        requests.append((method, path)))
    total = len(labels) * RELEASE["reservation_micros"]
    with pytest.raises(ValueError, match="reservation envelope"):
        managed.ManagedRunner(name, 0, case_labels=labels, total_reserved_micros=total - 1)
    with pytest.raises(ValueError, match="Duplicate"):
        managed.ManagedRunner(name, 0, case_labels=labels + (labels[0],), total_reserved_micros=total)
    with pytest.raises(ValueError, match="642"):
        managed.ManagedRunner(name, 0, case_labels=labels + ("extra",), total_reserved_micros=total * 2)
    assert requests == []
    runner = managed.ManagedRunner(name, 0, case_labels=labels, total_reserved_micros=total)
    assert list(runner.assigned_projects) == list(labels)
    assert list(runner.assigned_projects.values()) == [f"{name}-{slot:02d}" for slot in range(1, 643)]
    with pytest.raises(ValueError, match="absent"):
        runner.run_task("unknown-case", "fixture", [])
    assert requests == [("GET", "/readyz")]
    assert not list(tmp_path.rglob("plan.json"))


@pytest.mark.parametrize(("status", "reward"), [("resolved", None), ("resolved", 0), ("unresolved", 1)])
def test_ac014_conflicting_reward_cannot_complete_case(tmp_path, offline, status, reward):
    tasks = [make_task(1)]
    result = run(tmp_path, tasks, runner_factory=fake_runner(tasks, []),
                 verifier=verdict(status, "official_verifier", reward))
    assert result["infrastructure_error"] == 1 and result["official_verdicts"] == 0
    assert result["completed"] == 0 and result["resolved_rate"] is None


def test_ac014_full_batch_credential_validity_and_scope(tmp_path, monkeypatch, offline):
    monkeypatch.setattr(managed, "private_dir", lambda _: tmp_path)
    with pytest.raises(ValueError, match="positive"):
        managed.provision("bench-ttl", credential_hours=0)
    before = datetime.now(timezone.utc)
    managed.provision("bench-ttl", count=642, credential_hours=24)
    credential, = batch.read(tmp_path / "credentials.json")["credentials"]
    assert credential["project_ids"] == managed.projects("bench-ttl", 642)
    expires = datetime.fromisoformat(credential["expires_at"].replace("Z", "+00:00"))
    assert abs((expires - before).total_seconds() - 86400) < 5
