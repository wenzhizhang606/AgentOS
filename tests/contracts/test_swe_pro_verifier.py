"""AC-014. Separate scored fixes from failures of an isolated SWE Pro verifier."""

import io
import json
from pathlib import Path
import subprocess
import tarfile
from types import SimpleNamespace

import pytest

from benchmarks import swe_pro_verifier as verifier


CONFIG = {"fail_to_pass": '["repair"]', "pass_to_pass": ["existing"]}
DIGEST = "sha256:" + "b" * 64


def state(code):
    return {"Status": "exited", "ExitCode": code, "OOMKilled": False, "Error": ""}


def output(repair="PASSED", existing="PASSED"):
    return {"tests": [{"name": "repair", "status": repair}, {"name": "existing", "status": existing}]}


def test_ac014_swe_official_reward_validates_both_groups_and_exit_code():
    good = verifier.classify(CONFIG, output(), "1", state(0))
    assert good["status"] == "resolved" and good["official_reward"] == 1
    regression = verifier.classify(CONFIG, output(existing="FAILED"), "0", state(1))
    assert regression["status"] == "unresolved"
    assert regression["pass_to_pass"]["unpassed"] == {"existing": "FAILED"}
    for reward, code in (("0", 0), ("1", 1)):
        assert verifier.classify(CONFIG, output(), reward, state(code))["reason"] == "official_reward_mismatch"


@pytest.mark.parametrize(("raw", "reward", "process", "opts", "reason"), [
    (None, None, state(137), {"timed_out": True}, "verifier_timeout"),
    (output(), "0", {**state(137), "OOMKilled": True}, {}, "verifier_oom"),
    (None, None, state(1), {}, "missing_verifier_artifacts"),
    ({"tests": []}, "0", state(1), {}, "incomplete_test_discovery"),
    (None, "0", state(1), {"dependency_failure": True}, "dependency_failure"),
    (output(repair="FAILED"), "0", state(1), {"dependency_failure": True}, "dependency_failure"),
    (output(), "1", state(66), {}, "verifier_process_error"),
    ({"tests": ["non-object"]}, "0", state(1), {}, "malformed_verifier_artifacts"),
])
def test_ac014_swe_infrastructure_is_not_a_wrong_answer(raw, reward, process, opts, reason):
    verdict = verifier.classify(CONFIG, raw, reward, process, **opts)
    assert verdict["status"] == "infrastructure_error" and verdict["official_reward"] is None
    assert verdict["reason"] == reason


def test_ac014_swe_rejected_patch_has_no_invented_official_reward():
    verdict = verifier.classify(CONFIG, None, None, state(verifier.INVALID_PATCH))
    assert verdict == {"status": "unresolved", "reason": "patch_rejected", "official_reward": None}


def test_ac014_swe_frozen_identity_and_single_use_artifact_directory(tmp_path, monkeypatch):
    task = tmp_path / "suite" / "v2" / "tasks" / "instance_some__repo-a-vnan"
    with pytest.raises(ValueError, match="immutable"):
        verifier.grade(task, "latest", tmp_path / "bad")
    with pytest.raises(ValueError, match="instance"):
        verifier.grade(task.parent / ".." / "other", DIGEST, tmp_path / "bad")
    monkeypatch.setattr(verifier, "_replay", lambda *args: verifier._infra("official_image_not_cached"))
    directory = tmp_path / "attempt-1"
    report = verifier.grade(task, DIGEST, directory)
    assert report["instance_id"] == task.name and report["image_digest"] == DIGEST
    assert json.loads((directory / "evaluation.json").read_text()) == report
    with pytest.raises(FileExistsError):
        verifier.grade(task, DIGEST, directory)


def test_ac014_swe_low_disk_never_creates_a_container(tmp_path, monkeypatch):
    spec = {"environment": {"storage_mb": 10240}}
    monkeypatch.setattr(verifier, "_frozen_inputs", lambda *args: (spec, CONFIG))
    calls = []
    def docker(*args):
        calls.append(args)
        assert args[0] == "info"
        return str(tmp_path).encode()
    monkeypatch.setattr(verifier, "_docker", docker)
    monkeypatch.setattr(verifier.shutil, "disk_usage", lambda p: SimpleNamespace(free=1))
    result = verifier._replay(Path(), "instance_x", DIGEST, tmp_path, None, 3000)
    assert result["reason"] == "insufficient_docker_disk" and len(calls) == 1


def test_ac014_swe_timeout_removes_only_the_new_container(tmp_path, monkeypatch):
    spec = {"environment": {"storage_mb": 0, "docker_image": verifier.REPOSITORY + ":instance_x",
                            "cpus": 1, "memory_mb": 4096}, "verifier": {"timeout_sec": 60}}
    monkeypatch.setattr(verifier, "_frozen_inputs", lambda *args: (spec, CONFIG))
    calls = []
    def docker(*args):
        calls.append(args)
        if args[0] == "info":
            return str(tmp_path).encode()
        if args[:2] == ("image", "inspect"):
            return json.dumps([{"Os": "linux", "Architecture": "amd64",
                                "RepoDigests": [verifier.REPOSITORY + "@" + DIGEST]}]).encode()
        return b""
    def expire(cmd, **kwargs):
        assert "wait" in cmd and kwargs["timeout"] == 60
        raise subprocess.TimeoutExpired(cmd, 60)
    monkeypatch.setattr(verifier, "_docker", docker)
    monkeypatch.setattr(verifier.subprocess, "run", expire)
    assert verifier._replay(Path(), "instance_x", DIGEST, tmp_path, None, 3000)["reason"] == "verifier_timeout"
    create = next(cmd for cmd in calls if cmd[0] == "create")
    assert create[create.index("--network") + 1] == "none" and "--pull=never" in create
    assert not set(create) & {"-v", "--mount", "--privileged"}
    assert calls[-1] == ("rm", "--force", "--volumes", create[create.index("--name") + 1])


@pytest.mark.parametrize("member_type", [tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.DIRTYPE])
def test_ac014_swe_container_artifacts_never_follow_links(monkeypatch, member_type):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as out:
        member = tarfile.TarInfo("output.json")
        member.type = member_type
        member.linkname = "/outside/result"
        out.addfile(member)
    buf.seek(0)
    class FakeProcess:
        stdout = buf
        def __enter__(self):
            return self
        def __exit__(self, *_exc):
            self.stdout.close()
        def kill(self):
            pass
    monkeypatch.setattr(verifier.subprocess, "Popen", lambda *a, **k: FakeProcess())
    assert verifier._artifact("some-container", "output.json", 64) is None
