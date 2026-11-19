"""AC-014. Plan, resume and independently grade a frozen SWE-bench Pro V2 Full run."""

import argparse
from contextlib import contextmanager
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
from uuid import uuid4

from .managed import ManagedRunner, private_dir, provision, write_json
from .swe_pro import SUITE_COMMIT
from .swe_pro_suite import discover_suite, space_admission, verify_grader_files, IMAGE_REPOSITORY
from .swe_pro_verifier import DOCKER, grade

GIB = 1024 ** 3
FINAL = {"resolved", "unresolved", "agent_failure"}


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


@contextmanager
def exclusive(directory):
    """The shared host executes one queue writer. The OS releases the lock after a crash."""
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (directory / "batch.lock").open("a+b") as handle:
        if os.name == "nt":  # Offline planning/testing can also run on Windows.
            import msvcrt
            handle.seek(0)
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise RuntimeError("This batch already has a writer") from exc
        else:
            import fcntl
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise RuntimeError("This batch already has a writer") from exc
        yield


def freeze(directory, tasks):
    manifest = {"suite_commit": SUITE_COMMIT, "scaffold": "agentos-swe-full/1",
                "tasks": {t.instance_id: {"instruction_sha256": t.instruction_sha256, "image": t.image,
                                          "storage_mb": t.storage_mb} for t in tasks}}
    path = directory / "suite.json"
    if path.exists():
        if read(path) != manifest:
            raise ValueError("Frozen suite differs; use the original checkout and run directory")
    else:
        if any(directory.glob("*/plan.json")) or (directory / "cases").exists():
            raise ValueError("Run directory already contains another benchmark")
        write_json(path, manifest)
    return manifest


def case_dir(directory, instance):
    return directory / "cases" / sha256(instance.encode()).hexdigest()[:24]


def pin_implementation(directory):
    files = ("managed.py", "swe_pro.py", "_swe_pro_repository.py", "swe_pro_suite.py",
             "swe_pro_prediction.py", "swe_pro_batch.py", "swe_pro_verifier.py")
    hashes = {name: sha256(Path(__file__).with_name(name).read_text(encoding="utf-8").encode()).hexdigest()
              for name in files}
    path = directory / "implementation.json"
    if path.exists():
        if read(path) != hashes:
            raise ValueError("Benchmark implementation changed during this batch")
    else:
        write_json(path, hashes)


def summary(directory):
    manifest = read(directory / "suite.json")
    counts = dict.fromkeys(("pending", "awaiting_grade", "infrastructure_error", "resolved",
                            "unresolved", "agent_failure"), 0)
    official_verdicts = 0
    for instance in manifest["tasks"]:
        path = case_dir(directory, instance) / "state.json"
        state = read(path) if path.exists() else {"status": "pending"}
        counts[state["status"]] += 1
        if state["status"] in {"resolved", "unresolved"} and state.get("official_reward") in (0, 1):
            official_verdicts += 1
    total = len(manifest["tasks"])
    completed = sum(counts[state] for state in FINAL)
    return {"suite_commit": manifest["suite_commit"], "total": total, **counts, "completed": completed,
            "official_verdicts": official_verdicts,
            "resolved_rate": counts["resolved"] / total if total and completed == total else None}


def docker(*args, timeout=60):
    return subprocess.check_output([*DOCKER, *args], text=True, timeout=timeout).strip()


def local_space():
    root = Path(docker("info", "--format", "{{.DockerRootDir}}"))
    if not root.is_absolute() or not root.is_dir():
        raise RuntimeError("Run the batch on the Linux host holding the Docker data directory")
    return shutil.disk_usage(root).free


def cached_image(task, pinned, fetch):
    tag = task.image
    cached = docker("image", "ls", "--quiet", tag)
    if not cached:
        if not fetch:
            raise RuntimeError(f"Image not cached for {task.instance_id}")
        ref = f"{IMAGE_REPOSITORY}@{pinned}" if pinned else tag
        docker("pull", "--platform", "linux/amd64", ref, timeout=3600)
        if pinned:
            docker("tag", ref, tag)
    spec = json.loads(docker("image", "inspect", tag))[0]
    if (spec["Os"], spec["Architecture"]) != ("linux", "amd64"):
        raise RuntimeError("Cached image platform is not linux/amd64")
    prefix = IMAGE_REPOSITORY + "@"
    digests = {r[len(prefix):] for r in spec.get("RepoDigests", []) if r.startswith(prefix)}
    if pinned:
        if pinned not in digests:
            raise RuntimeError("Tag no longer matches this case's pinned image digest")
        return pinned
    if len(digests) != 1 or not re.fullmatch(r"sha256:[0-9a-f]{64}", next(iter(digests), "")):
        raise RuntimeError("Expected one official registry manifest digest for this image")
    return digests.pop()


def disk_pause(directory, task, reserve, available):
    check = space_admission(available(), task.storage_mb * 1024**2, reserve)
    if not check.admitted:
        return {**summary(directory), "paused": "insufficient_disk", "next_case": task.instance_id,
                "available_bytes": check.available_bytes, "required_bytes": check.required_bytes,
                "reserve_bytes": reserve, "shortfall_bytes": check.shortfall_bytes}


def evidence_limit(release, task):
    from .swe_pro_prediction import GOAL, SOURCE_ID, SOURCE_TITLE, wire_text_bytes
    fields = (GOAL, SOURCE_ID, SOURCE_TITLE, task.instance_id)
    return (release["window_tokens"] - release["max_output_tokens"] - release["safety_margin_tokens"]
            - release["empty_swe_wire_bytes"] - sum(wire_text_bytes(value) for value in fields))


def grade_candidate(harness, task, digest, case, state, patch_digest, verifier):
    patch = case / "candidate.patch"
    if not patch.is_file() or patch.is_symlink() or sha256(patch.read_bytes()).hexdigest() != patch_digest:
        raise ValueError("Candidate is missing or differs from the recorded digest")
    # A completed verdict may have reached disk just before the queue process stopped.
    last = state.get("evidence", "")
    saved = None
    if state["status"] == "awaiting_grade" and re.fullmatch(r"verify-[0-9a-f]{32}", last):
        result_path = case / last / "evaluation.json"
        if result_path.is_file():
            saved = read(result_path)
    if saved is None:
        attempt = case / ("verify-" + uuid4().hex)
        state.update(status="awaiting_grade", evidence=attempt.name)
        write_json(case / "state.json", state)
        saved = verifier(harness / "v2" / "tasks" / task.instance_id, digest, attempt, patch=patch)
    if (saved.get("instance_id"), saved.get("suite_commit"), saved.get("image_digest"), saved.get("phase")) != (
            task.instance_id, SUITE_COMMIT, digest, "prediction"):
        raise ValueError("Verifier receipt does not match the pinned case")
    status = saved["status"]
    if status not in {"resolved", "unresolved", "infrastructure_error"}:
        raise ValueError("Unknown verifier verdict")
    if status != "infrastructure_error" and saved.get("patch_sha256") != patch_digest:
        raise ValueError("Verifier receipt does not match the recorded candidate")
    reward = saved.get("official_reward")
    if reward is None:
        if status != "infrastructure_error" and (status, saved.get("reason")) != ("unresolved", "patch_rejected"):
            raise ValueError("Missing official reward in verifier receipt")
    elif type(reward) is not int or (status, reward) not in {("resolved", 1), ("unresolved", 0)}:
        raise ValueError("Official reward conflicts with the recorded verdict")
    state.update(status=status, reason=saved.get("reason"), official_reward=reward)


def run_queue(directory, harness, tasks, *, runner_factory, predict, reserve_bytes,
              fetch_images=False, available=local_space, image=cached_image, verifier=grade):
    """Check capacity before model admission or Docker pull, and never resubmit a stored candidate."""
    manifest = freeze(directory, tasks)
    pin_implementation(directory)
    runner = None
    for task in tasks:
        case = case_dir(directory, task.instance_id)
        case.mkdir(parents=True, exist_ok=True)
        state_file = case / "state.json"
        state = read(state_file) if state_file.exists() else {"status": "pending", "instance_id": task.instance_id}
        if state["status"] in FINAL:
            continue
        try:
            pause = disk_pause(directory, task, reserve_bytes, available)
            if pause:
                return pause
            digest = image(task, state.get("image_digest"), fetch_images)
            state.update(image_digest=digest)
            write_json(state_file, state)
            if fetch_images:
                pause = disk_pause(directory, task, reserve_bytes, available)
                if pause:
                    return pause
            inference = case / "inference.json"
            if not inference.exists():
                if runner is None:
                    runner = runner_factory(tuple(manifest["tasks"]))
                    release_file = directory / "release.json"
                    if release_file.exists() and read(release_file) != runner.release:
                        raise ValueError("Deployed model release changed during this batch")
                    write_json(release_file, runner.release)
                prediction = predict(task, image_digest=digest, run_task=runner.run_task, workdir=case,
                                     evidence_bytes=evidence_limit(runner.release, task))
                write_json(inference, prediction)
            prediction = read(inference)
            if (prediction.get("instance_id"), prediction.get("instruction_sha256"), prediction.get("image_digest")) != (
                    task.instance_id, task.instruction_sha256, digest):
                raise ValueError("Inference receipt does not match the pinned case")
            if prediction["status"] == "infrastructure_error":
                state.update(status="infrastructure_error", reason=prediction["reason"])
            elif prediction["status"] == "model_failure":
                state.update(status="agent_failure", reason=prediction["reason"])
            elif prediction["status"] == "candidate":
                grade_candidate(harness, task, digest, case, state, prediction["patch_sha256"], verifier)
            else:
                raise ValueError("Unrecognized stored inference status")
            write_json(state_file, state)
        except (OSError, RuntimeError, ValueError, KeyError, subprocess.SubprocessError) as exc:
            # Keep the model receipt, patch and the pinned digest for a later retry.
            state.update(status="infrastructure_error", reason=f"{type(exc).__name__}: {exc}")
            write_json(state_file, state)
        if state["status"] == "infrastructure_error":
            return {**summary(directory), "paused": state["reason"], "next_case": task.instance_id}
    return summary(directory)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("plan", "status", "provision", "run"):
        command = sub.add_parser(name)
        command.add_argument("run_name", help="A new bench-* run identifier")
        if name in ("plan", "run"):
            command.add_argument("harness", type=Path)
        if name == "provision":
            command.add_argument("--credential-hours", type=int, default=4,
                                 help="Validity for the private benchmark API credential")
        if name == "run":
            command.add_argument("--port", type=int, required=True, help="Loopback benchmark API port")
            command.add_argument("--budget-micros", type=int, required=True, help="Total reservation limit in CNY micro-units")
            command.add_argument("--reserve-gib", type=int, default=2, help="Free space retained for other services")
            command.add_argument("--fetch-missing", action="store_true", help="Allow pulling the current case after disk admission")
    args = parser.parse_args(argv)
    os.umask(0o077)
    directory = private_dir(args.run_name)
    if args.command == "status":
        print(json.dumps(summary(directory)))
        return 0
    with exclusive(directory):
        if args.command == "provision":
            manifest = read(directory / "suite.json")
            provision(args.run_name, count=len(manifest["tasks"]), credential_hours=args.credential_hours)
            return 0
        tasks = discover_suite(args.harness)
        if args.command == "plan":
            freeze(directory, tasks)
            result = summary(directory)
        else:
            from .swe_pro_prediction import predict
            verify_grader_files(args.harness, tasks)
            if args.budget_micros <= 0 or args.reserve_gib < 0:
                parser.error("Invalid budget or disk reserve")
            def factory(labels):
                return ManagedRunner(args.run_name, args.port, case_labels=labels,
                                     total_reserved_micros=args.budget_micros)
            result = run_queue(directory, args.harness, tasks, runner_factory=factory, predict=predict,
                               reserve_bytes=args.reserve_gib * GIB, fetch_images=args.fetch_missing)
        print(json.dumps(result))
        return 2 if result.get("paused") else 0


if __name__ == "__main__":
    raise SystemExit(main())
