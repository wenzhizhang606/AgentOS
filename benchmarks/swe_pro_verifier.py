"""AC-014. Replay one SWE-bench Pro V2 patch on a local Linux Docker host."""

import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
import tempfile
from uuid import uuid4

try:
    import tomllib
except ModuleNotFoundError:  # Evaluation host on Python 3.10.
    import tomli as tomllib

from .managed import write_json
from .swe_pro import SUITE_COMMIT, required_tests


REPOSITORY = "ghcr.io/scaleapi/swe-bench_pro-v2"
DOCKER = ["docker", "--host", "unix:///var/run/docker.sock"]
VERIFIER_FILES = ("config.json", "parser.py", "run_script.sh", "test.sh", "test_patch.patch")
MIB = 1024 * 1024
INVALID_PATCH = 65
BOOTSTRAP = """set -eu
for root in /app /testbed; do
  if [ "$(git -C "$root" rev-parse --show-toplevel 2>/dev/null)" = "$root" ]; then
    cd "$root"
    break
  fi
done
test "$(git rev-parse --show-toplevel 2>/dev/null)" = "$root" && test "$PWD" = "$root" || exit 66
mkdir -p /logs/verifier
if [ -s /tmp/agentos.patch ]; then
  git apply --check -- /tmp/agentos.patch || exit 65
  git apply -- /tmp/agentos.patch || exit 65
fi
exec bash /tests/test.sh
"""


def _infra(reason):
    return {"status": "infrastructure_error", "reason": reason, "official_reward": None}


def classify(config, output, reward, state, *, timed_out=False, dependency_failure=False):
    """Require the official reward, both test groups, and the container exit to agree."""
    if timed_out:
        return _infra("verifier_timeout")
    if state.get("OOMKilled"):
        return _infra("verifier_oom")
    if state.get("Status") != "exited" or state.get("Error"):
        return _infra("container_error")
    code = state.get("ExitCode")
    if code == INVALID_PATCH and output is None and reward is None:
        return {"status": "unresolved", "reason": "patch_rejected", "official_reward": None}
    if code not in (0, 1):
        return _infra("verifier_process_error")
    if output is None or reward is None:
        return _infra("dependency_failure" if dependency_failure else "missing_verifier_artifacts")
    if reward not in ("0", "1") or not isinstance(output, dict) or not isinstance(output.get("tests"), list):
        return _infra("malformed_verifier_artifacts")

    seen = {}
    for test in output["tests"]:
        if (not isinstance(test, dict) or not isinstance(test.get("name"), str)
                or not isinstance(test.get("status"), str)):
            return _infra("malformed_verifier_artifacts")
        # The official verifier counts a name as passed if *any* of its records passed.
        if seen.get(test["name"]) != "PASSED":
            seen[test["name"]] = test["status"]
    groups = {}
    for key in ("fail_to_pass", "pass_to_pass"):
        try:
            expected = required_tests(config[key])
        except (ValueError, KeyError, SyntaxError, TypeError):
            return _infra("invalid_test_contract")
        missing = {name: seen.get(name, "MISSING") for name in sorted(expected) if seen.get(name) != "PASSED"}
        groups[key] = {"total": len(expected), "passed": len(expected) - len(missing), "unpassed": missing}
    if not sum(group["total"] for group in groups.values()):
        return _infra("empty_test_contract")
    passed = all(group["total"] == group["passed"] for group in groups.values())
    if int(reward) != int(passed) or (code == 0) != passed:
        return {**_infra("official_reward_mismatch"), **groups}
    if not passed:
        if dependency_failure:
            return {**_infra("dependency_failure"), **groups}
        if any("MISSING" in group["unpassed"].values() for group in groups.values()):
            return {**_infra("incomplete_test_discovery"), **groups}
    return {"status": "resolved" if passed else "unresolved", "reason": "official_verifier",
            "official_reward": int(reward), **groups}


def _check_identifiers(task_dir, digest):
    instance = task_dir.name
    if (not re.fullmatch(r"instance_[A-Za-z0-9_.-]{1,119}", instance)
            or task_dir.parent.name != "tasks" or task_dir.parent.parent.name != "v2"):
        raise ValueError("Expected v2/tasks/<official instance ID>")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        raise ValueError("Expected the immutable image manifest digest")
    return instance


def _frozen_inputs(task_dir, instance, tests):
    """Read verifier files from fixed Git objects, independent of working-tree changes."""
    suite = task_dir.parent.parent.parent.resolve()
    def blob(name):
        return subprocess.run(
            ["git", "-C", str(suite), "show", f"{SUITE_COMMIT}:v2/tasks/{instance}/{name}"],
            capture_output=True, check=True, timeout=30,
            env={**os.environ, "GIT_NO_REPLACE_OBJECTS": "1", "GIT_NO_LAZY_FETCH": "1",
                 "GIT_TERMINAL_PROMPT": "0", "GIT_ALLOW_PROTOCOL": ""}).stdout

    metadata = tomllib.loads(blob("task.toml").decode())
    tag = REPOSITORY + ":" + instance
    if metadata["environment"]["docker_image"] != tag:
        raise ValueError("Official task metadata does not match its image tag")
    tests.mkdir()
    for name in VERIFIER_FILES:
        (tests / name).write_bytes(blob("tests/" + name))
    return metadata, json.loads((tests / "config.json").read_bytes())


def _docker(*args):
    return subprocess.run([*DOCKER, *args], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          check=True, timeout=120).stdout


def _artifact(container, name, limit):
    """Read only a bounded regular file from the container. Never extract its archive."""
    with subprocess.Popen([*DOCKER, "cp", f"{container}:/logs/verifier/{name}", "-"],
                          stdout=subprocess.PIPE, stderr=subprocess.DEVNULL) as cp:
        try:
            with tarfile.open(fileobj=cp.stdout, mode="r|") as tar:
                member = tar.next()
                if member is None or member.name != name or not member.isfile() or member.size > limit:
                    return None
                return tar.extractfile(member).read()
        except (OSError, tarfile.TarError):
            return None
        finally:
            cp.kill()


DEPENDENCY_FAILURE = re.compile(
    rb"network is unreachable|temporary failure in name resolution|Could not resolve host:|"
    rb"dial (?:tcp|udp).*?: (?:i/o timeout|network is unreachable)|"
    rb"module lookup disabled by GOPROXY=off|could not resolve dependencies for project|"
    rb"failed to get successful HTTP response from", re.IGNORECASE)


def _replay(task_dir, instance, digest, destination, patch, timeout_seconds):
    with tempfile.TemporaryDirectory() as tmp:
        tests = Path(tmp) / "tests"
        metadata, config = _frozen_inputs(task_dir, instance, tests)
        spec = metadata["environment"]
        image = REPOSITORY + "@" + digest
        root = Path(_docker("info", "--format", "{{.DockerRootDir}}").decode().strip())
        if not root.is_absolute() or not root.is_dir():
            return _infra("docker_storage_unavailable")
        if shutil.disk_usage(root).free < spec["storage_mb"] * MIB:
            return _infra("insufficient_docker_disk")
        try:
            cached = json.loads(_docker("image", "inspect", spec["docker_image"]))[0]
        except subprocess.CalledProcessError:
            return _infra("official_image_not_cached")
        if image not in (cached.get("RepoDigests") or []):
            return _infra("image_digest_mismatch")
        if (cached.get("Os"), cached.get("Architecture")) != ("linux", "amd64"):
            return _infra("unsupported_image_platform")

        candidate = Path(tmp) / "candidate.patch"
        if patch is not None:
            if not patch.is_file() or patch.is_symlink():
                raise ValueError("Expected a regular patch file")
            shutil.copyfile(patch, candidate)
        timeout = min(timeout_seconds, int(metadata["verifier"]["timeout_sec"]))
        container = "agentos-swe-grade-" + uuid4().hex
        _docker("create", "--name", container, "--pull=never", "--network", "none",
                "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--pids-limit", "256",
                "--cpus", str(spec["cpus"]), "--memory", f'{spec["memory_mb"]}m',
                "--memory-swap", f'{spec["memory_mb"]}m', "--log-driver", "none",
                "--entrypoint", "bash", image, "-c", BOOTSTRAP)
        try:
            _docker("cp", str(tests), container + ":/tests")
            if patch is not None:
                _docker("cp", str(candidate), container + ":/tmp/agentos.patch")
            _docker("start", container)
            try:
                subprocess.run([*DOCKER, "wait", container], capture_output=True, check=True, timeout=timeout)
            except subprocess.TimeoutExpired:
                return _infra("verifier_timeout")
            state = json.loads(_docker("inspect", "--format", "{{json .State}}", container))
            write_json(destination / "container-state.json", state)
            evidence = {}
            for name, limit in (("output.json", 32 * MIB), ("reward.txt", 64),
                                ("run-script-stderr.txt", 8 * MIB), ("run-script-stdout.txt", 8 * MIB)):
                data = _artifact(container, name, limit)
                if data is not None:
                    (destination / name).write_bytes(data)
                    evidence[name] = data
            try:
                output = json.loads(evidence["output.json"]) if "output.json" in evidence else None
                reward = evidence["reward.txt"].decode("ascii").strip() if "reward.txt" in evidence else None
            except (ValueError, UnicodeError):
                return _infra("malformed_verifier_artifacts")
            dep_error = any(DEPENDENCY_FAILURE.search(evidence.get(n, b""))
                            for n in ("run-script-stderr.txt", "run-script-stdout.txt"))
            result = classify(config, output, reward, state, dependency_failure=dep_error)
            if patch is not None:
                result["patch_sha256"] = sha256(candidate.read_bytes()).hexdigest()
            return result
        finally:
            _docker("rm", "--force", "--volumes", container)


def grade(task_dir: Path, image_digest: str, destination: Path, patch: Path | None = None, *, timeout_seconds=3000):
    """Run on the Linux Docker host. Always use a fresh output directory for each attempt."""
    task_dir, destination = Path(task_dir), Path(destination)
    instance = _check_identifiers(task_dir, image_digest)
    if timeout_seconds <= 0:
        raise ValueError("Timeout must be positive")
    destination.mkdir(parents=True, exist_ok=False)
    try:
        result = _replay(task_dir, instance, image_digest, destination,
                         Path(patch) if patch is not None else None, timeout_seconds)
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
        result = _infra("verifier_setup_or_runtime_error")
        result["detail"] = type(exc).__name__
    result.update(instance_id=instance, suite_commit=SUITE_COMMIT, image_digest=image_digest,
                  phase="prediction" if patch is not None else "baseline")
    write_json(destination / "evaluation.json", result)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("task_dir", type=Path)
    parser.add_argument("digest")
    parser.add_argument("destination", type=Path)
    parser.add_argument("--patch", type=Path)
    args = parser.parse_args()
    result = grade(args.task_dir, args.digest, args.destination, args.patch)
    print(json.dumps(result, ensure_ascii=True))
    raise SystemExit(2 if result["status"] == "infrastructure_error" else 0)
