"""Score a standalone Python submission in a fresh, restricted Linux container."""

import json
from pathlib import Path
import re
import subprocess
from uuid import uuid4


IMAGE = "python:3.12-slim-bookworm"
VERDICTS = frozenset({"passed", "test_failed", "invalid_python", "missing_entry_point",
                      "unsupported_code", "runtime_error", "runtime_timeout"})


class GraderUnavailable(RuntimeError):
    """The grader could not produce an answer verdict."""


class DockerHumanEvalGrader:
    def __init__(self, image=IMAGE):
        try:
            check = subprocess.run(
                ["docker", "image", "inspect", "--format", "{{.Id}} {{.Os}}", image],
                capture_output=True, timeout=10, check=False, text=True,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise GraderUnavailable("Unable to inspect the local Python Docker image") from exc
        match = re.fullmatch(r"(sha256:[0-9a-f]{64}) linux", check.stdout.strip())
        if check.returncode != 0 or not match:
            raise GraderUnavailable(f"Linux image {image} must be available locally")
        self.image_id = match[1]
        self.worker = Path(__file__).with_name("_humaneval_worker.py").read_text(encoding="utf-8")

    def __call__(self, code, problem):
        name = "agentos-humaneval-" + uuid4().hex
        request = json.dumps({
            "candidate": code, "test": problem["test"], "entry_point": problem["entry_point"],
        }).encode("utf-8")
        command = [
            "docker", "run", "--rm", "-i", "--name", name,
            "--pull=never", "--network=none", "--read-only", "--user=65534:65534",
            "--cap-drop=ALL", "--security-opt=no-new-privileges", "--pids-limit=16",
            "--cpus=1", "--memory=128m", "--memory-swap=128m", "--log-driver=none",
            self.image_id, "python", "-I", "-B", "-c", self.worker,
        ]
        try:
            finished = subprocess.run(command, input=request, capture_output=True, timeout=25, check=False)
        except subprocess.TimeoutExpired as exc:
            try:
                subprocess.run(
                    ["docker", "container", "rm", "--force", name],
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    timeout=5, check=False,
                )
            except (OSError, subprocess.TimeoutExpired) as cleanup:
                raise GraderUnavailable(f"Container timed out and cleanup was unconfirmed ({name})") from cleanup
            raise GraderUnavailable("Docker did not return a verdict before the outer deadline") from exc
        except OSError as exc:
            raise GraderUnavailable("Unable to run the Docker grader") from exc
        try:
            response = json.loads(finished.stdout)
            verdict = response["verdict"]
            if finished.returncode != 0 or verdict not in VERDICTS:
                raise ValueError()
        except (ValueError, TypeError, KeyError) as exc:
            raise GraderUnavailable(f"Invalid Docker grader result (exit {finished.returncode})") from exc
        return verdict
