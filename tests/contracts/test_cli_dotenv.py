"""AC-010/012 native entry points read the project .env before composing a release."""

import os
from pathlib import Path
import sys
from unittest.mock import Mock, patch

import pytest

from agentos import cli


@pytest.mark.parametrize("command", ["release-id", "worker"])
def test_native_cli_reads_project_env_preserving_process_and_worker_scope(tmp_path, monkeypatch, command, capsys):
    sample = Path(__file__).resolve().parents[2] / "examples/m1/release.json"
    (tmp_path / ".env").write_text(
        f"AGENTOS_RELEASE_FILE='{sample.as_posix()}'\n"
        "AGENTOS_MODEL_API_KEY='fixture-secret'\nAGENTOS_MYSQL_USER=file-user\n"
        "MYSQL_ROOT_PASSWORD=root-only\nAGENTOS_MIGRATION_PASSWORD=migrator-only\n",
        encoding="utf-8-sig",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["agentos", command])
    store = Mock()
    with patch.dict(os.environ, {"AGENTOS_MYSQL_USER": "process-user"}, clear=True), \
            patch("agentos.adapters.mysql.store.MySQLStore.from_env", return_value=store), \
            patch("agentos.gateways.model.client.ChatModel") as model, \
            patch("agentos.worker.service.serve"):
        cli.main()
        assert os.environ["AGENTOS_MYSQL_USER"] == "process-user"
        assert "MYSQL_ROOT_PASSWORD" not in os.environ and "AGENTOS_MIGRATION_PASSWORD" not in os.environ
        if command == "worker":
            assert model.call_args.args[1] == "fixture-secret"
            assert "fixture-secret" not in str(store.register_release.call_args)
        else:
            assert "AGENTOS_MODEL_API_KEY" not in os.environ
            assert len(capsys.readouterr().out.strip()) == 64
