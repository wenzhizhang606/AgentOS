"""A single frozen SWE-bench Pro V2 case through the AgentOS managed worker (AC-014)."""

import argparse
import ast
import difflib
from hashlib import sha256
import json
from pathlib import Path

from .coding import extract_candidate
from .managed import ManagedRunner, write_json


SUITE_COMMIT = "66f92766bba642462d4bbe5479e83f91f9211862"
INSTANCE = "instance_internetarchive__openlibrary-00bec1e7c8f3272c469a58e1377df03f955ed478-v13642507b4fc1f8d234172bf8129942da2c2ca26"
SOURCE_PATH = "openlibrary/plugins/importapi/import_validator.py"
BASE_COMMIT = "02f647f7d525286b6e3a661133c3772649e585cd"
IMAGE = "ghcr.io/scaleapi/swe-bench_pro-v2@sha256:03205f63224d992c930df7a2b898353d9d3dface4ab4d921eebc73dc909c1ba6"
GOAL = (
    "Implement the Open Library import validator requirements in the supplied task specification. "
    "Return the complete updated Python file for openlibrary/plugins/importapi/import_validator.py. "
    "Use concise code, preserve caller compatibility and include necessary imports. "
    "After the Python source put [E1] alone on the last line. Omit all other prose."
)


def candidate_patch(baseline, answer):
    candidate = extract_candidate(answer)
    if candidate is None:
        return None
    updated = candidate.rstrip("\n") + "\n"
    return "".join(difflib.unified_diff(
        baseline.splitlines(keepends=True), updated.splitlines(keepends=True),
        fromfile="a/" + SOURCE_PATH, tofile="b/" + SOURCE_PATH,
    ))


def predict(workdir, name, port):
    task_text = (workdir / "instruction.md").read_text(encoding="utf-8")
    base = (workdir / "baseline.py").read_text(encoding="utf-8")
    if sha256(base.encode()).hexdigest() != "dfe771112955beaaa36c6079882f743169f7c88eb4815a7d6576761fac59f531":
        raise ValueError("Open Library source differs from the fixed baseline")
    if sha256(task_text.encode()).hexdigest() != "1107240051f4c98286d1309202f6530363be1b033b5a820792416ea2468e8d5b":
        raise ValueError("SWE-bench instruction differs from the frozen official prompt")
    evidence = task_text + "\n\nBase source at " + SOURCE_PATH + ":\n\n" + base
    source = {"source_id": "openlibrary-base", "title": "Open Library import validator specification",
              "text": evidence, "location": SOURCE_PATH}
    runner = ManagedRunner(name, port)
    plan = {
        "dataset": "SWE-bench Pro V2 / public", "suite_commit": SUITE_COMMIT, "instance_id": INSTANCE,
        "source_path": SOURCE_PATH, "source_base_commit": BASE_COMMIT, "image": IMAGE,
        "sample": "First lexicographic Open Library instance (book metadata validation)",
        "scaffold": "AgentOS read-only profile, one source file supplied with the issue, one model attempt",
        "provider_release": runner.release, "baseline_sha256": sha256(base.encode()).hexdigest(),
        "instruction_sha256": sha256(task_text.encode()).hexdigest(), "goal": GOAL,
    }
    write_json(workdir / "protocol.json", plan)
    task = runner.run_task("swe-pro-openlibrary-00bec1e7", GOAL, [source])
    result = task.get("result") or {}
    patch = candidate_patch(base, result.get("answer"))
    (workdir / "candidate.patch").write_bytes((patch or "").encode("utf-8"))
    row = {key: task.get(key) for key in (
        "task_id", "run_id", "attempt", "state", "error_code", "accounting", "elapsed_seconds", "snapshot_id",
    )}
    row.update(instance_id=INSTANCE, patch_produced=bool(patch),
               patch_sha256=sha256((patch or "").encode()).hexdigest())
    write_json(workdir / "inference.json", row)
    print(json.dumps(row, ensure_ascii=True))


def required_tests(value):
    if isinstance(value, str):
        value = ast.literal_eval(value)
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError("Unexpected official test list")
    return set(value)


def score(workdir):
    config = json.loads((workdir / "config.json").read_text(encoding="utf-8"))
    report = {"dataset": "SWE-bench Pro V2", "instance_id": INSTANCE, "suite_commit": SUITE_COMMIT}
    for phase in ("baseline", "prediction"):
        logs = workdir / phase
        raw = json.loads((logs / "output.json").read_text(encoding="utf-8"))
        seen = {test["name"]: test["status"] for test in raw["tests"]}
        passed_names = {test["name"] for test in raw["tests"] if test["status"] == "PASSED"}
        reward = int((logs / "reward.txt").read_text(encoding="ascii").strip())
        groups = {}
        for label in ("fail_to_pass", "pass_to_pass"):
            expected = required_tests(config[label])
            passed = expected & passed_names
            groups[label] = {"total": len(expected), "passed": len(passed),
                             "unpassed": {n: seen.get(n, "MISSING") for n in sorted(expected - passed)}}
        complete = all(v["passed"] == v["total"] for v in groups.values())
        if reward != int(complete):
            raise ValueError("Official reward and required test breakdown disagree")
        report[phase] = {"official_reward": reward, **groups}
    write_json(workdir / "evaluation.json", report)
    print(json.dumps(report, ensure_ascii=True))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    inference = commands.add_parser("predict")
    inference.add_argument("workdir", type=Path)
    inference.add_argument("name")
    inference.add_argument("--port", type=int, required=True)
    scoring = commands.add_parser("score")
    scoring.add_argument("workdir", type=Path)
    args = parser.parse_args()
    if args.command == "predict":
        predict(args.workdir, args.name, args.port)
    else:
        score(args.workdir)
