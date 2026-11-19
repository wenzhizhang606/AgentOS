"""Offline AC-014 discovery and capacity admission for the pinned SWE-bench Pro V2 Full split."""

import argparse
from dataclasses import asdict, dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess

try:
    import tomllib
except ModuleNotFoundError:  # A Python 3.10 benchmark host can install tomli.
    import tomli as tomllib

from .swe_pro import SUITE_COMMIT


FULL_COUNT = 642
IMAGE_REPOSITORY = "ghcr.io/scaleapi/swe-bench_pro-v2"
GRADER_FILES = frozenset({"config.json", "parser.py", "run_script.sh", "test_patch.patch", "test.sh"})


@dataclass(frozen=True)
class SuiteTask:
    instance_id: str
    instruction: str
    instruction_sha256: str
    image: str
    storage_mb: int


@dataclass(frozen=True)
class SpaceAdmission:
    available_bytes: int
    required_bytes: int
    reserve_bytes: int
    shortfall_bytes: int
    admitted: bool


def space_admission(available_bytes: int, required_bytes: int, reserve_bytes: int) -> SpaceAdmission:
    """Check one filesystem at admission time; an official task's storage_mb is not its image size."""
    for value in (available_bytes, required_bytes, reserve_bytes):
        if type(value) is not int or value < 0:
            raise ValueError("Space estimates must be nonnegative integer byte counts")
    shortfall = max(0, required_bytes + reserve_bytes - available_bytes)
    return SpaceAdmission(available_bytes, required_bytes, reserve_bytes, shortfall, shortfall == 0)


def _git(root, *args):
    env = {**os.environ, "GIT_NO_LAZY_FETCH": "1", "GIT_ALLOW_PROTOCOL": "",
           "GIT_NO_REPLACE_OBJECTS": "1", "GIT_TERMINAL_PROMPT": "0"}
    proc = subprocess.run(["git", "-C", str(root), *args], capture_output=True, env=env)
    if proc.returncode:
        raise ValueError(f"Cannot inspect local Git checkout: {proc.stderr.decode(errors='replace').strip()}")
    return proc.stdout


def _pinned_blobs(root, commit):
    """Read only Git tree entries. Never fetch missing objects or open solution blobs."""
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("Expected an exact Git commit SHA")
    top = Path(os.fsdecode(_git(root, "rev-parse", "--show-toplevel").strip())).resolve()
    if root != top or _git(root, "rev-parse", "HEAD").decode().strip() != commit:
        raise ValueError(f"Expected harness root checked out at {commit}")
    tree = _git(root, "ls-tree", "-rz", "--full-tree", commit, "--", "v2/tasks")
    blobs = {}
    for record in tree.split(b"\0"):
        if not record:
            continue
        header, path = record.split(b"\t", 1)
        _, _, name, relative = path.decode("utf-8").split("/", 3)
        if not re.fullmatch(r"instance_[a-zA-Z0-9._-]{1,119}", name):
            raise ValueError(f"Invalid V2 instance or image tag: {name}")
        entries = blobs.setdefault(name, {})
        if relative in {"instruction.md", "task.toml", "environment/Dockerfile"} or relative.startswith("tests/"):
            mode, kind, oid = header.decode().split()
            if kind != "blob" or mode not in {"100644", "100755"}:
                raise ValueError(f"Unexpected entry type for {name}/{relative}")
            entries[relative] = oid
    return blobs


def _checked_file(root, name, relative, entries, *, normalize_checkout=False):
    path = root / "v2" / "tasks" / name / relative
    if relative not in entries or path.resolve() != path or not path.is_file():
        raise ValueError(f"Missing or nonregular pinned file: {name}/{relative}")
    data = path.read_bytes()

    def object_id(raw):
        return hashlib.sha1(b"blob %d\x00" % len(raw) + raw).hexdigest()

    # A Windows Git checkout can use CRLF for public text. Return the actual committed bytes.
    if object_id(data) != entries[relative] and normalize_checkout:
        data = data.replace(b"\r\n", b"\n")
    if object_id(data) != entries[relative]:
        raise ValueError(f"File differs from pinned Git blob: {name}/{relative}")
    return data


def discover_suite(harness: Path, *, expected_commit=SUITE_COMMIT, expected_count=FULL_COUNT) -> tuple[SuiteTask, ...]:
    """Return verified inference inputs in stable instance order. Partial checkouts do not qualify."""
    root = Path(harness).resolve()
    catalog = _pinned_blobs(root, expected_commit)
    if type(expected_count) is not int or expected_count < 1 or len(catalog) != expected_count:
        raise ValueError(f"Pinned tree contains {len(catalog)} instances, expected {expected_count}")
    task_root = root / "v2" / "tasks"
    if task_root.resolve() != task_root or not task_root.is_dir():
        raise ValueError("The checkout is missing v2/tasks")
    found = {p.name for p in task_root.iterdir() if p.is_dir()}
    if found != catalog.keys():
        raise ValueError(f"Incomplete or unexpected task directories: {len(found & catalog.keys())}/{expected_count} "
                         f"pinned instances materialized, {len(found - catalog.keys())} unexpected")
    tasks = []
    for name, entries in sorted(catalog.items()):
        raw = _checked_file(root, name, "instruction.md", entries, normalize_checkout=True)
        instruction = raw.decode("utf-8")
        if not instruction.strip():
            raise ValueError(f"Empty instruction for {name}")
        config = tomllib.loads(_checked_file(root, name, "task.toml", entries, normalize_checkout=True).decode())
        image = f"{IMAGE_REPOSITORY}:{name}"
        environment = config.get("environment", {})
        if config.get("task", {}).get("name") != f"swebench-pro/{name}" or environment.get("docker_image") != image:
            raise ValueError(f"Official task/image binding mismatch: {name}")
        storage = environment.get("storage_mb")
        if type(storage) is not int or storage <= 0:
            raise ValueError(f"Invalid storage_mb for {name}")
        dockerfile = _checked_file(root, name, "environment/Dockerfile", entries, normalize_checkout=True).decode()
        inputs = re.findall(r"(?im)^\s*FROM[ \t]+([^\r\n]+)", dockerfile)
        if [value.strip() for value in inputs] != [image]:
            raise ValueError(f"Official Dockerfile/image binding mismatch: {name}")
        tasks.append(SuiteTask(name, instruction, hashlib.sha256(raw).hexdigest(), image, storage))
    return tuple(tasks)


def verify_grader_files(harness: Path, tasks, *, expected_commit=SUITE_COMMIT) -> None:
    """Validate exactly the files to be copied into fresh verifiers, for one task or the entire suite."""
    root = Path(harness).resolve()
    catalog = _pinned_blobs(root, expected_commit)
    for task in tasks:
        name = task.instance_id
        if name not in catalog:
            raise ValueError(f"Unknown grader instance: {name}")
        folder = root / "v2" / "tasks" / name / "tests"
        if folder.resolve() != folder or not folder.is_dir():
            raise ValueError(f"Missing regular grader directory for {name}")
        pinned = {p.removeprefix("tests/") for p in catalog[name] if p.startswith("tests/")}
        actual = {item.name for item in folder.iterdir()}
        if actual != GRADER_FILES or pinned != GRADER_FILES:
            raise ValueError(f"Missing or unexpected verifier files for {name}")
        for filename in sorted(GRADER_FILES):
            _checked_file(root, name, "tests/" + filename, catalog[name])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    audit = commands.add_parser("inspect", help="verify the pinned 642-task local checkout")
    audit.add_argument("harness", type=Path)
    audit.add_argument("--verify-graders", action="store_true", help="also audit isolated verifier inputs")
    disk = commands.add_parser("preflight", help="check the mount used by the next job or startup operation")
    disk.add_argument("path", type=Path)
    disk.add_argument("--required-bytes", required=True, type=int)
    disk.add_argument("--reserve-bytes", required=True, type=int)
    args = parser.parse_args(argv)
    try:
        if args.command == "preflight":
            result = space_admission(shutil.disk_usage(args.path).free, args.required_bytes, args.reserve_bytes)
            print(json.dumps(asdict(result)))
            return 0 if result.admitted else 1
        tasks = discover_suite(args.harness)
        if args.verify_graders:
            verify_grader_files(args.harness, tasks)
        print(json.dumps({"suite_commit": SUITE_COMMIT, "task_count": len(tasks),
                          "graders_verified": args.verify_graders}))
        return 0
    except (ValueError, OSError) as exc:
        parser.exit(2, f"{parser.prog}: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
