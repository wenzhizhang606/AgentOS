"""AC-014 contracts for the pilot coding benchmark and standalone grader."""

import gzip
from hashlib import sha256
import json
import subprocess

import pytest

from benchmarks import coding
from benchmarks.grade_humaneval import DockerHumanEvalGrader, GraderUnavailable


def test_ac014_coding_extracts_only_the_cited_source():
    assert coding.extract_candidate("def f():\n    return 1\n[E1]") == "def f():\n    return 1"
    assert coding.extract_candidate("```python\r\ndef f():\r\n    return 1\r\n```\r\n[E1]") == "def f():\n    return 1"
    assert coding.extract_candidate("def f():\n    return 1 [E1]") is None
    assert coding.extract_candidate("```python\ndef f():\n    return 1\n[E1]") is None


def test_ac014_coding_keeps_failed_and_ungraded_tasks_in_denominator(tmp_path, monkeypatch):
    test_cases = [
        {"task_id": tid, "entry_point": "f", "prompt": "Python Coding Specification",
         "canonical_solution": "private-reference", "test": "private-test"}
        for tid in coding.TASK_IDS[:3]
    ]
    data = gzip.compress("\n".join(json.dumps(item) for item in test_cases).encode())
    dataset = tmp_path / "sample.jsonl.gz"
    dataset.write_bytes(data)
    monkeypatch.setattr(coding, "TASK_IDS", coding.TASK_IDS[:3])
    monkeypatch.setattr(coding, "DATASET_SHA256", sha256(data).hexdigest())
    calls = []

    def run_task(case_label, question, sources):
        calls.append(case_label)
        source = sources[0]
        assert source["text"] == "Python Coding Specification"
        assert "private-" not in question and "private-" not in str(sources)
        if len(calls) == 2:
            return {"state": "failed", "error_code": "MISSING_EVIDENCE"}
        return {"state": "succeeded", "result": {
            "answer": "def f():\n    return 1\n[E1]",
            "citations": [{**source, "label": "E1"}],
        }}

    def grade(code, problem):
        assert "return 1" in code and problem["test"] == "private-test"
        if len(calls) == 3:
            raise GraderUnavailable("Docker unavailable")
        return "passed"

    report = coding.run_coding(run_task, dataset, grade=grade)
    assert report["cases"] == 3 and report["passed"] == 1 and report["grader_unavailable"] == 1
    assert report["pass_at_1"] is None
    assert [case["verdict"] for case in report["rows"]] == ["passed", "task_failed", "grader_unavailable"]
    assert "private-" not in json.dumps(report)
    dataset.write_bytes(b"different artifact")
    with pytest.raises(ValueError, match="SHA-256"):
        coding.run_coding(run_task, dataset, grade=grade)


def test_ac014_coding_grader_uses_local_restricted_docker_image(monkeypatch):
    def mock_run(command, **options):
        if command[1:3] == ["image", "inspect"]:
            return subprocess.CompletedProcess(command, 0, "sha256:" + "a" * 64 + " linux\n", "")
        assert command[1] == "run"
        assert {"--pull=never", "--network=none", "--read-only", "--user=65534:65534",
                "--cap-drop=ALL", "--security-opt=no-new-privileges", "--rm"} <= set(command)
        assert not {"--env", "--env-file", "--volume", "--mount", "--privileged"} & set(command)
        assert options["timeout"] > 3
        assert json.loads(options["input"]) == {"candidate": "def f(): pass", "test": "test", "entry_point": "f"}
        return subprocess.CompletedProcess(command, 0, b'{"verdict": "passed"}', b"")

    monkeypatch.setattr(subprocess, "run", mock_run)
    grader = DockerHumanEvalGrader()
    assert grader("def f(): pass", {"test": "test", "entry_point": "f"}) == "passed"
