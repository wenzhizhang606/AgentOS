"""AC-014. Local synthetic fixtures for bounded multi-repository patch prediction."""
from hashlib import sha256
import json
import subprocess

import pytest

from agentos.context.service import _request
from benchmarks._swe_pro_repository import select
from benchmarks import swe_pro_prediction as prediction
from benchmarks.swe_pro_suite import IMAGE_REPOSITORY, SuiteTask


def fixture_task():
    instruction = "Repair both files used by parse_profile."
    return SuiteTask("example-case", instruction, sha256(instruction.encode()).hexdigest(),
                     IMAGE_REPOSITORY + ":example-case", 128)


DIFF = ("diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1,2 +1,2 @@\n-old\n+new\n \n"
        "diff --git a/b.py b/b.py\n--- a/b.py\n+++ b/b.py\n@@ -1 +1 @@\n-old\n+new\n")
DIGEST = "sha256:" + "a" * 64


def test_ac014_selector_uses_only_regular_base_blobs_and_ranks_symbol_matches(tmp_path):
    def git(*args):
        subprocess.run(["git", "-C", str(tmp_path), *args], check=True, capture_output=True)
    git("init", "-q")
    for name, content in {"a.py": "x = 2\n", "z.py": "def parse_profile():\n    return 2\n",
                          "credentials.json": '{"parse_profile":"private"}',
                          ".env": "SECRET=1", "vendor/z.py": "def parse_profile(): pass\n",
                          "tests/test.py": "raise Exception('fixture')\n"}.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content.encode())
    git("-c", "core.autocrlf=false", "add", ".")
    git("-c", "user.name=fixture", "-c", "user.email=fixture@example.test",
        "-c", "commit.gpgsign=false", "commit", "-qm", "synthetic baseline")
    (tmp_path / "z.py").write_bytes(b"uncommitted private draft\n")
    result = select(tmp_path, "Repair parse_profile", 1500)
    assert [entry["path"] for entry in result["files"]] == ["z.py", "a.py"]
    assert result["files"][0]["content"].startswith("def parse_profile")


def test_ac014_predict_saves_multifile_patch_and_recovers_without_model_call(tmp_path, monkeypatch):
    task = fixture_task()
    fake_source = {"head": "b"*40, "files": [{"path": "a.py", "start_line": 1,
                   "sha256": "a"*64, "content": "old\n"}]}
    monkeypatch.setattr(prediction, "repository_evidence", lambda *args: fake_source)
    def run(label, goal, sources):
        assert label == task.instance_id and len(sources) == 1
        assert sources[0]["text"].endswith("old\n") and task.instruction in sources[0]["text"]
        return {"state": "succeeded", "task_id": "t1", "run_id": "r1", "result": {
            "answer": "\x60\x60\x60diff\n" + DIFF + "\x60\x60\x60\n[E1]",
            "citations": [{"source_id": prediction.SOURCE_ID, "location": task.instance_id, "label": "E1"}]}}
    options = dict(workdir=tmp_path, evidence_bytes=1500, image_digest=DIGEST)
    row = prediction.predict(task, run_task=run, **options)
    assert row["status"] == "candidate"
    assert (tmp_path/"candidate.patch").read_bytes() == DIFF.encode()
    assert row["patch_sha256"] == sha256(DIFF.encode()).hexdigest()
    assert json.loads((tmp_path/"sources.json").read_text())["files"][0]["path"] == "a.py"
    monkeypatch.setattr(prediction, "repository_evidence", lambda *args: pytest.fail("re-read"))
    assert prediction.predict(task, run_task=lambda *args: pytest.fail("re-submit"), **options) == row
    (tmp_path/"candidate.patch").write_bytes(b"modified")
    with pytest.raises(ValueError, match="differs"):
        prediction.predict(task, run_task=run, **options)


@pytest.mark.parametrize("state,code,settled,answer,status,reason", [
    ("failed", None, False, None, "infrastructure_error", "failed"),
    ("failed", "EXTERNAL_OUTCOME_UNKNOWN", False, None, "infrastructure_error", "EXTERNAL_OUTCOME_UNKNOWN"),
    ("timed_out", None, False, None, "infrastructure_error", "timed_out"),
    ("cancelled", None, False, None, "infrastructure_error", "cancelled"),
    ("failed", "MISSING_EVIDENCE", False, None, "infrastructure_error", "MISSING_EVIDENCE"),
    ("failed", "MISSING_EVIDENCE", True, None, "model_failure", "MISSING_EVIDENCE"),
    ("failed", "INVALID_MODEL_OUTPUT", True, None, "model_failure", "INVALID_MODEL_OUTPUT"),
    ("succeeded", None, True, "not a patch\n[E1]", "model_failure", "invalid_answer_format"),
    ("succeeded", None, True, DIFF + "[E1]", "model_failure", "invalid_citation"),
])
def test_ac014_unsuccessful_prediction_has_no_candidate(tmp_path, monkeypatch, state, code, settled, answer, status, reason):
    monkeypatch.setattr(prediction, "repository_evidence", lambda *args: {"head": "a"*40, "files": []})
    def run(*args):
        return {"state": state, "task_id": "x", "run_id": "r", "result": {"answer": answer},
                "error_code": code, "snapshot_id": "s" if settled else None,
                "accounting": {"state": "final", "input_tokens": 420} if settled else None}
    options = dict(image_digest=DIGEST, evidence_bytes=700, workdir=tmp_path)
    row = prediction.predict(fixture_task(), run_task=run, **options)
    assert (row["status"], row["reason"], row["task_id"], row["run_id"]) == (status, reason, "x", "r")
    assert prediction.predict(fixture_task(), run_task=lambda *a: pytest.fail("duplicate call"), **options) == row
    assert not (tmp_path/"candidate.patch").exists()


def test_ac014_long_issue_retains_auditable_prefix_suffix_and_respects_wire_budget(tmp_path, monkeypatch):
    issue = "Opening requirement.\n" + ('glyph 字🙂 "\\\n' * 500) + "\nClosing requirement."
    task = SuiteTask("long", issue, sha256(issue.encode()).hexdigest(), IMAGE_REPOSITORY + ":long", 128)
    cap = 1500
    monkeypatch.setattr(prediction, "repository_evidence", lambda *_: {
        "head": "f"*40, "files": [{"path": "service.py", "start_line": 2, "sha256": "c"*64,
                                  "content": 'value = "\\\\"\n' * 400}]})
    def run(label, goal, sources):
        source = sources[0]
        wire_entry = {**source, "version": 1, "label": "E1"}
        baseline = _request("m", 800, goal, [{**wire_entry, "text": ""}], [])
        with_text = _request("m", 800, goal, [wire_entry], [])
        assert len(with_text) - len(baseline) == prediction.wire_text_bytes(source["text"]) <= cap
        assert "Opening requirement." in source["text"] and "Closing requirement." in source["text"]
        assert "middle of original issue omitted" in source["text"] and "File service.py from line 2" in source["text"]
        return {"state": "cancelled", "task_id": "x"}
    prediction.predict(task, image_digest=DIGEST, evidence_bytes=cap, workdir=tmp_path, run_task=run)
    note = json.loads((tmp_path / "sources.json").read_text(encoding="utf-8"))
    excerpt = note["issue_excerpt"]
    assert note["instruction_sha256"] == task.instruction_sha256 and note["evidence_wire_bytes"] <= cap
    assert excerpt["rule"] == "prefix_60_suffix_40_wire_v1"
    data = issue.encode()
    rebuilt = (data[:excerpt["prefix_bytes"]] + b"\n[... middle of original issue omitted ...]\n"
               + data[-excerpt["suffix_bytes"]:])
    assert sha256(rebuilt).hexdigest() == excerpt["excerpt_sha256"]


def test_ac014_success_with_withheld_result_keeps_access_failure_receipt(tmp_path, monkeypatch):
    monkeypatch.setattr(prediction, "repository_evidence", lambda *args: {"head": "a"*40, "files": []})
    row = prediction.predict(fixture_task(), image_digest=DIGEST, evidence_bytes=700, workdir=tmp_path,
                             run_task=lambda *_: {"state": "succeeded", "result": None,
                                                  "task_id": "t", "result_error": "PERMISSION_DENIED"})
    assert (row["status"], row["reason"], row["task_id"]) == ("infrastructure_error", "PERMISSION_DENIED", "t")


def test_ac014_collector_rejects_image_mismatch_and_never_pulls_or_mounts(tmp_path, monkeypatch):
    commands = []
    def inspect(cmd, **kwargs):
        commands.append(cmd)
        if "inspect" in cmd:
            return json.dumps([IMAGE_REPOSITORY + "@" + DIGEST]).encode()
        return b'{"files": [], "head": "public"}'
    monkeypatch.setattr(subprocess, "check_output", inspect)
    with pytest.raises(ValueError, match="digest"):
        prediction.repository_evidence(fixture_task(), "sha256:"+"d"*64, 900)
    assert len(commands) == 1
    assert prediction.repository_evidence(fixture_task(), DIGEST, 900)["head"] == "public"
    cmd = commands[-1]
    assert {"--network=none", "--pull=never", "--read-only", "--cap-drop=ALL",
            IMAGE_REPOSITORY + "@" + DIGEST} <= set(cmd)
    assert not {"-v", "--mount", "--env", "--env-file"} & set(cmd)
    assert fixture_task().instruction not in " ".join(cmd)
