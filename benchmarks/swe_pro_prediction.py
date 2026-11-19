"""One read-only, one-shot repository repair through AgentOS (AC-014)."""
from hashlib import sha256
import json
from pathlib import Path
import re
import subprocess
from uuid import uuid4

from .managed import write_json
from .swe_pro_suite import IMAGE_REPOSITORY, SuiteTask
from .swe_pro_verifier import DOCKER


GOAL = (
    "Use the SWE repository repair evidence to implement the issue. Return one unified Git diff "
    "against the supplied baseline with repository-relative a/ and b/ paths and correct context. "
    "Multiple files are allowed. Include only the patch followed by [E1] alone on the final line."
)
SOURCE_ID = "swe-repository-repair"
SOURCE_TITLE = "SWE repository repair"
COLLECTOR = Path(__file__).with_name("_swe_pro_repository.py")


def wire_text_bytes(text: str) -> int:
    """Byte increment when the context compiler embeds this string inside two JSON strings."""
    inner = json.dumps(text, ensure_ascii=False)[1:-1]
    return len(json.dumps(inner, ensure_ascii=False).encode("utf-8")) - 2


def _fitted_prefix(text, available):
    """Slice on Unicode code points, respecting escaped wire bytes."""
    low, high = 0, min(len(text), available)
    while low < high:
        mid = (low + high + 1) // 2
        if wire_text_bytes(text[:mid]) <= available:
            low = mid
        else:
            high = mid - 1
    return text[:low]


def _issue_evidence(instruction, budget):
    header = SOURCE_TITLE + "\n\n"
    body_budget = budget - wire_text_bytes(header)
    reserved_for_code = max(160, min(1024, body_budget // 4))
    rule = "full"
    prefix, suffix = instruction, ""
    excerpt = instruction
    if wire_text_bytes(instruction) > body_budget - reserved_for_code:
        rule = "prefix_60_suffix_40_wire_v1"
        marker = "\n[... middle of original issue omitted ...]\n"
        available = min(body_budget - reserved_for_code, body_budget * 2 // 3) - wire_text_bytes(marker)
        if available < 32:
            raise ValueError("Configured evidence window cannot fit the SWE issue excerpt header")
        prefix = _fitted_prefix(instruction, available * 3 // 5)
        suffix = _fitted_prefix(instruction[::-1], available - wire_text_bytes(prefix))[::-1]
        excerpt = prefix + marker + suffix
    return header + excerpt, {
        "rule": rule, "original_bytes": len(instruction.encode()), "prefix_bytes": len(prefix.encode()),
        "suffix_bytes": len(suffix.encode()), "excerpt_sha256": sha256(excerpt.encode()).hexdigest(),
    }


def repository_evidence(task, image_digest, limit):
    """Inspect only a cached image, run only the fixed selector, and mount no host files."""
    if task.image != IMAGE_REPOSITORY + ":" + task.instance_id or not re.fullmatch(r"sha256:[0-9a-f]{64}", image_digest):
        raise ValueError("Unexpected SWE repository image or digest")
    immutable = IMAGE_REPOSITORY + "@" + image_digest
    output = subprocess.check_output([*DOCKER, "image", "inspect", "--format", "{{json .RepoDigests}}", task.image],
                                     timeout=30)
    if immutable not in (json.loads(output) or []):
        raise ValueError("Cached task image does not match the requested repository digest")
    name = "agentos-swe-read-" + uuid4().hex
    command = [*DOCKER, "run", "--rm", "-i", "--name", name, "--pull=never", "--network=none", "--read-only",
               "--cap-drop=ALL", "--security-opt=no-new-privileges", "--cpus=1", "--memory=1g",
               "--pids-limit=128", "--entrypoint", "python3", immutable, "-I", "-B", "-c",
               COLLECTOR.read_text(encoding="utf-8")]
    request = json.dumps({"instruction": task.instruction, "limit": limit}).encode("utf-8")
    try:
        raw = subprocess.check_output(command, input=request, timeout=120)
    except subprocess.TimeoutExpired:
        subprocess.run([*DOCKER, "rm", "-f", name], stdout=subprocess.DEVNULL, timeout=30)
        raise
    return json.loads(raw)


def extract_diff(answer):
    """Preserve diff whitespace, including context lines that consist of a single space."""
    if not isinstance(answer, str) or "\0" in answer:
        return None
    patch, end, cite = answer.replace("\r\n", "\n").rstrip("\n").rpartition("\n")
    if not end or cite.strip() != "[E1]":
        return None
    lines = patch.strip("\n").split("\n")
    if lines[0].startswith("\x60\x60\x60"):
        if lines[0].strip() not in ("\x60\x60\x60", "\x60\x60\x60diff", "\x60\x60\x60patch") or lines[-1].strip() != "\x60\x60\x60":
            return None
        lines = lines[1:-1]
    if (not lines or not lines[0].startswith(("diff --git ", "--- "))
            or not any(line.startswith("@@ ") for line in lines)
            or not any(line.startswith("+++ ") for line in lines)):
        return None
    return "\n".join(lines) + "\n"


def predict(task: SuiteTask, *, image_digest: str, run_task, workdir: Path, evidence_bytes: int) -> dict:
    """Write a resumable model result. The caller owns one case lock and a stable managed run."""
    checksum = sha256(task.instruction.encode()).hexdigest()
    if checksum != task.instruction_sha256:
        raise ValueError("SWE issue text differs from its verified task digest")
    if type(evidence_bytes) is not int or evidence_bytes < 1:
        raise ValueError("Invalid SWE evidence byte budget")
    # The selector is bounded even when deployed with a very large model window.
    evidence_bytes = min(evidence_bytes, 1_000_000)
    workdir.mkdir(parents=True, exist_ok=True)
    receipt_path = workdir / "inference.json"
    patch_path = workdir / "candidate.patch"
    provenance = {"prediction_protocol": "bounded-wire/1",
                  "instance_id": task.instance_id, "instruction_sha256": checksum, "image_digest": image_digest,
                  "evidence_bytes": evidence_bytes, "selector_sha256": sha256(COLLECTOR.read_bytes()).hexdigest(),
                  "goal_sha256": sha256(GOAL.encode()).hexdigest()}
    if receipt_path.exists():
        row = json.loads(receipt_path.read_text(encoding="utf-8"))
        if any(row.get(k) != val for k, val in provenance.items()):
            raise ValueError("SWE inference checkpoint belongs to a different prediction protocol")
        if row["status"] == "candidate":
            if sha256(patch_path.read_bytes()).hexdigest() != row["patch_sha256"]:
                raise ValueError("Stored candidate differs from its inference receipt")
        elif row["status"] not in {"model_failure", "infrastructure_error"}:
            raise ValueError("Unrecognized stored inference status")
        return row
    evidence, issue_excerpt = _issue_evidence(task.instruction, evidence_bytes)
    limit = evidence_bytes - wire_text_bytes(evidence)
    if limit < 160:
        raise ValueError("Configured evidence window leaves insufficient bytes for repository evidence")
    snapshot = repository_evidence(task, image_digest, limit)
    files = []
    for item in snapshot["files"]:
        heading = f"\n\nFile {item['path']} from line {item['start_line']}:\n"
        remaining = evidence_bytes - wire_text_bytes(evidence + heading)
        if remaining <= 0:
            break
        content = item["content"]
        if wire_text_bytes(content) > remaining:
            content = _fitted_prefix(content, remaining).rpartition("\n")[0]
        if not content.strip():
            continue
        evidence += heading + content
        files.append({k: item[k] for k in ("path", "start_line", "sha256")} |
                     {"excerpt_sha256": sha256(content.encode()).hexdigest(), "excerpt_bytes": len(content.encode())})
    source = {"source_id": SOURCE_ID, "title": SOURCE_TITLE, "text": evidence,
              "location": task.instance_id}
    write_json(workdir / "sources.json", {**provenance, "image_repo_head": snapshot["head"], "files": files,
                                         "issue_excerpt": issue_excerpt, "evidence_wire_bytes": wire_text_bytes(evidence),
                                         "evidence_sha256": sha256(evidence.encode()).hexdigest()})
    outcome = run_task(task.instance_id, GOAL, [source])
    if outcome["state"] not in {"succeeded", "failed", "cancelled", "timed_out"}:
        raise RuntimeError("Managed task has no terminal result yet")
    result = outcome.get("result")
    result_available = isinstance(result, dict)
    result = result if result_available else {}
    patch = extract_diff(result.get("answer"))
    citations = result.get("citations")
    cited = isinstance(citations, list) and any(isinstance(ref, dict) and ref.get("label") == "E1"
                and ref.get("source_id") == SOURCE_ID and ref.get("location") == task.instance_id for ref in citations)
    row = {**provenance, **{k: outcome.get(k) for k in (
        "state", "task_id", "run_id", "snapshot_id", "dispatch_attempt_id", "attempt", "accounting",
        "elapsed_seconds", "error_code", "result_error")}}
    if outcome["state"] != "succeeded":
        code = outcome.get("error_code")
        accounting = outcome.get("accounting") or {}
        # MISSING_EVIDENCE can also originate in retrieval before any provider dispatch.
        provider_refusal = (code == "MISSING_EVIDENCE" and bool(outcome.get("snapshot_id"))
                            and accounting.get("state") == "final" and accounting.get("input_tokens") is not None)
        model_error = outcome["state"] == "failed" and (code == "INVALID_MODEL_OUTPUT" or provider_refusal)
        row.update(status="model_failure" if model_error else "infrastructure_error", reason=code or outcome["state"])
    elif outcome.get("result_error") or not result_available:
        row.update(status="infrastructure_error", reason=outcome.get("result_error") or "result_unavailable")
    elif patch is None or not cited:
        row.update(status="model_failure", reason="invalid_answer_format" if patch is None else "invalid_citation")
    else:
        encoded = patch.encode("utf-8")
        tmp = workdir / "candidate.patch.part"
        tmp.write_bytes(encoded)
        tmp.replace(patch_path)
        row.update(status="candidate", reason=None, patch_sha256=sha256(encoded).hexdigest())
    write_json(receipt_path, row)
    return row
