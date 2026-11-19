"""AC-014. Only a complete, pinned suite is admitted; inference never opens verifier/solution data."""

from dataclasses import asdict
from hashlib import sha256
import json
from pathlib import Path
import shutil
import subprocess

import pytest

from benchmarks.swe_pro_suite import GRADER_FILES, IMAGE_REPOSITORY, discover_suite, space_admission, verify_grader_files


def git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()


def commit_all(root):
    git(root, "add", "--all")
    git(root, "-c", "user.email=tests@example.invalid", "-c", "user.name=Fixture",
        "-c", "commit.gpgsign=false", "commit", "--quiet", "-m", "Fixture suite")
    return git(root, "rev-parse", "HEAD")


@pytest.fixture
def suite(tmp_path):
    git(tmp_path, "init", "--quiet")
    git(tmp_path, "config", "core.autocrlf", "false")
    for name in ("instance_repo-b", "instance_repo-a"):
        folder = tmp_path / "v2" / "tasks" / name
        (folder / "environment").mkdir(parents=True)
        (folder / "solution").mkdir()
        (folder / "tests").mkdir()
        image = f"{IMAGE_REPOSITORY}:{name}"
        (folder / "instruction.md").write_bytes(f"Public fix request for {name}\n".encode())
        (folder / "task.toml").write_bytes((f'[task]\nname = "swebench-pro/{name}"\n'
                                             f'[environment]\ndocker_image = "{image}"\nstorage_mb = 10240\n').encode())
        (folder / "environment" / "Dockerfile").write_bytes(f"FROM {image}\n".encode())
        (folder / "solution" / "gold_patch.diff").write_bytes(b"private reference")
        for filename in GRADER_FILES:
            (folder / "tests" / filename).write_bytes(b"private verifier contents")
    return tmp_path, commit_all(tmp_path)


def discover(suite):
    root, revision = suite
    return discover_suite(root, expected_commit=revision, expected_count=2)


def test_ac014_catalog_verifies_pinned_public_inputs_without_opening_private_files(suite, monkeypatch):
    root, revision = suite
    original_read = Path.read_bytes

    def read_public(path):
        assert "solution" not in path.relative_to(root).parts and "tests" not in path.relative_to(root).parts
        return original_read(path)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "read_bytes", read_public)
        tasks = discover(suite)
    assert [t.instance_id for t in tasks] == ["instance_repo-a", "instance_repo-b"]
    for task in tasks:
        assert task.instruction_sha256 == sha256(task.instruction.encode()).hexdigest()
        assert task.image == f"{IMAGE_REPOSITORY}:{task.instance_id}"
        assert task.storage_mb == 10240 and "private" not in json.dumps(asdict(task))
    verify_grader_files(root, tasks[:1], expected_commit=revision)
    verify_grader_files(root, tasks, expected_commit=revision)


def test_ac014_wrong_revision_or_unmaterialized_suite_cannot_pass(suite):
    root, revision = suite
    with pytest.raises(ValueError, match="checked out"):
        discover_suite(root, expected_commit="0" * 40, expected_count=2)
    with pytest.raises(ValueError, match="expected 642"):
        discover_suite(root, expected_commit=revision)
    shutil.rmtree(root / "v2" / "tasks" / "instance_repo-b")
    with pytest.raises(ValueError, match="1/2"):
        discover(suite)


@pytest.mark.parametrize("filename", ["instruction.md", "task.toml", "environment/Dockerfile"])
def test_ac014_missing_or_modified_checkout_files_are_rejected(suite, filename):
    root, _ = suite
    path = root / "v2" / "tasks" / "instance_repo-a" / filename
    path.write_bytes(b"modified")
    with pytest.raises(ValueError, match="differs from pinned"):
        discover(suite)
    path.unlink()
    with pytest.raises(ValueError, match="Missing or nonregular"):
        discover(suite)


@pytest.mark.parametrize("filename", ["task.toml", "environment/Dockerfile"])
def test_ac014_cross_instance_image_cannot_be_bound_even_in_fixed_tree(suite, filename):
    root, _ = suite
    path = root / "v2" / "tasks" / "instance_repo-a" / filename
    path.write_bytes(path.read_bytes().replace(b":instance_repo-a", b":instance_repo-b"))
    with pytest.raises(ValueError, match="binding mismatch"):
        discover((root, commit_all(root)))


def test_ac014_grading_rejects_injected_and_modified_verifier(suite):
    root, revision = suite
    tasks = discover(suite)
    folder = root / "v2" / "tasks" / tasks[1].instance_id / "tests"
    extra = folder / "injected.py"
    extra.write_bytes(b"untracked")
    verify_grader_files(root, tasks[:1], expected_commit=revision)
    with pytest.raises(ValueError, match="unexpected verifier"):
        verify_grader_files(root, tasks, expected_commit=revision)
    extra.unlink()
    (folder / "parser.py").write_bytes(b"changed")
    with pytest.raises(ValueError, match="differs from pinned"):
        verify_grader_files(root, tasks[1:], expected_commit=revision)
    (folder / "parser.py").unlink()
    with pytest.raises(ValueError, match="Missing or unexpected"):
        verify_grader_files(root, tasks, expected_commit=revision)


def test_ac014_disk_admission_reports_boundary_and_explicit_shortfall():
    assert space_admission(15, 10, 5).admitted
    assert space_admission(15, 0, 15).admitted
    low = space_admission(12, 10, 5)
    assert not low.admitted and low.shortfall_bytes == 3 and low.reserve_bytes == 5
    for invalid in ((True, 2, 1), (1, -1, 0), (3, 2, 0.5)):
        with pytest.raises(ValueError, match="integer"):
            space_admission(*invalid)
