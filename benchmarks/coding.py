"""Evaluate one AgentOS attempt per frozen HumanEval Python task (AC-014)."""

import gzip
from hashlib import sha256
import json
from pathlib import Path
import re

from .grade_humaneval import DockerHumanEvalGrader, GraderUnavailable, VERDICTS


DATASET_COMMIT = "6d43fb980f9fee3c892a914eda09951f772ad10d"
DATASET_URL = f"https://raw.githubusercontent.com/openai/human-eval/{DATASET_COMMIT}/data/HumanEval.jsonl.gz"
DATASET_SHA256 = "b796127e635a67f93fb35c04f4cb03cf06f38c8072ee7cee8833d7bee06979ef"
TASK_IDS = ("HumanEval/0", "HumanEval/17", "HumanEval/31", "HumanEval/64", "HumanEval/113", "HumanEval/142")
SOURCE_ID = "coding-spec"
FENCE = re.compile(r"\A```(?:python|py)?[ \t]*\n(.+?)\n```\Z", re.DOTALL | re.IGNORECASE)


def load_humaneval(dataset: Path):
    compressed = dataset.read_bytes()
    if sha256(compressed).hexdigest() != DATASET_SHA256:
        raise ValueError("HumanEval dataset SHA-256 differs from the frozen official artifact")
    problems = {}
    for line in gzip.decompress(compressed).splitlines():
        problem = json.loads(line)
        tid = problem["task_id"]
        if tid in TASK_IDS:
            if tid in problems or not problem["entry_point"].isidentifier():
                raise ValueError(f"Invalid HumanEval metadata for {tid}")
            problems[tid] = problem
    if set(problems) != set(TASK_IDS):
        raise ValueError("The frozen HumanEval subset is incomplete")
    return [problems[tid] for tid in TASK_IDS]


def extract_candidate(answer):
    if not isinstance(answer, str):
        return None
    text = answer.replace("\r\n", "\n").strip()
    response, separator, citation = text.rpartition("\n")
    if not separator or citation.strip() != "[E1]":
        return None
    response = response.strip()
    if response.startswith("```"):
        match = FENCE.fullmatch(response)
        return match[1] if match else None
    return response or None


def _grade_task(task, problem, grade):
    if task["state"] != "succeeded":
        return {"verdict": "task_failed"}
    result = task.get("result") or {}
    candidate = extract_candidate(result.get("answer"))
    cites_spec = any(
        (c["label"], c["source_id"], c["location"]) == ("E1", SOURCE_ID, problem["task_id"])
        for c in result.get("citations", [])
    )
    if candidate is None or not cites_spec:
        return {"verdict": "invalid_answer_format", "answer": result.get("answer")}
    try:
        verdict = grade(candidate, problem)
        if verdict not in VERDICTS:
            raise GraderUnavailable("Unrecognized grader result")
    except GraderUnavailable as exc:
        return {"verdict": "grader_unavailable", "grader_error": str(exc), "candidate": candidate}
    return {"verdict": verdict, "candidate": candidate}


def run_coding(run_task, dataset: Path, *, grade=None):
    """Use the managed task callback for inference and independently score code."""
    problems = load_humaneval(dataset)
    image = None
    if grade is None:
        grade = DockerHumanEvalGrader()
        image = grade.image_id
        for problem in problems:
            reference = problem["prompt"] + problem["canonical_solution"]
            if grade(reference, problem) != "passed":
                raise GraderUnavailable(f"Official reference failed preflight for {problem['task_id']}")
    rows = []
    for problem in problems:
        case_id = problem["task_id"]
        source = {"source_id": SOURCE_ID, "title": "Python Coding Specification",
                  "text": problem["prompt"], "location": case_id}
        question = (
            f"Using the Python Coding Specification, implement {problem['entry_point']}. "
            "Return a complete standalone Python source file with essential imports and concise code. "
            "Use standard algorithmic Python libraries and pure functions. "
            "After the Python source put [E1] alone on the last line. Omit all other prose."
        )
        task = run_task("coding-" + case_id.rsplit("/", 1)[1], question, [source])
        assessment = _grade_task(task, problem, grade)
        provenance = {key: task.get(key) for key in (
            "task_id", "run_id", "attempt", "state", "error_code", "snapshot_id", "accounting",
            "settled_micros", "reserved_micros",
        )}
        rows.append({"case_id": case_id, "task": provenance, **assessment})
    passed = sum(row["verdict"] == "passed" for row in rows)
    ungraded = sum(row["verdict"] == "grader_unavailable" for row in rows)
    return {
        "suite": "HumanEval base / AgentOS pilot",
        "dataset_commit": DATASET_COMMIT,
        "dataset_sha256": DATASET_SHA256,
        "grader_image_id": image,
        "cases": len(rows),
        "passed": passed,
        "grader_unavailable": ungraded,
        "pass_at_1": passed / len(rows) if not ungraded else None,
        "rows": rows,
    }
