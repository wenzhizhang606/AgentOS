"""AC-017 deployment migration contracts; the database driver is a test double."""

from hashlib import sha256
from pathlib import Path
from runpy import run_path
from unittest.mock import MagicMock, call

import pymysql
from pymysql.constants import CLIENT
import pytest


# This entry point ships in the container alongside migrations, outside the
# importable agentos package. Load it without running its CLI.
runner = run_path(str(Path(__file__).resolve().parents[2] / "deploy" / "migrate.py"))
discover = runner["discover"]
apply = runner["apply"]
MigrationError = runner["MigrationError"]


def connection(history=(), acquired=1):
    conn = MagicMock()
    cursor = conn.cursor.return_value.__enter__.return_value
    cursor.fetchone.return_value = (acquired,)
    cursor.fetchall.return_value = history
    cursor.nextset.return_value = None
    return conn, cursor


def history_row(migration):
    return migration.version, migration.filename, migration.digest


def logged_writes(cursor):
    return [args for args, _ in cursor.execute.call_args_list
            if args[0].lstrip().startswith("INSERT INTO ag_schema_migration")]


def test_ac017_discovers_in_numeric_order_and_normalizes_line_endings(tmp_path):
    (tmp_path / "10_extend.sql").write_bytes(b"SELECT 10;\n")
    script = tmp_path / "2_create.sql"
    script.write_bytes(b"SELECT 1;\r\nSELECT 2;\r\n")
    first, second = discover(tmp_path)
    assert [first.version, second.version] == [2, 10]
    assert first.digest == sha256(b"SELECT 1;\nSELECT 2;\n").hexdigest()
    script.write_bytes(first.sql.encode("utf-8"))
    assert first == discover(tmp_path)[0]
    (tmp_path / "002_collision.sql").write_text("SELECT 3;", encoding="utf-8")
    with pytest.raises(MigrationError, match="duplicate"):
        discover(tmp_path)


def test_ac017_only_applies_pending_sql_and_drains_all_results(tmp_path):
    (tmp_path / "001_applied.sql").write_text("SELECT 1;", encoding="utf-8")
    sql = "SET @stmt = 'SELECT ''a;b'''; PREPARE q FROM @stmt; EXECUTE q; DEALLOCATE PREPARE q;"
    (tmp_path / "002_pending.sql").write_text(sql, encoding="utf-8")
    applied, pending = discover(tmp_path)
    conn, cursor = connection([history_row(applied)])
    cursor.nextset.side_effect = (True, True, True, None)
    apply(conn, [applied, pending])
    assert call(applied.sql) not in cursor.execute.call_args_list
    assert cursor.method_calls[-6:-1] == [call.execute(sql)] + [call.nextset()] * 4
    assert [args[1] for args in logged_writes(cursor)] == [history_row(pending)]

    conn, cursor = connection([history_row(applied), history_row(pending)])
    apply(conn, [applied, pending])
    assert not logged_writes(cursor)
    assert call(pending.sql) not in cursor.execute.call_args_list


@pytest.mark.parametrize("history", [
    [(1, "001_old.sql", "9" * 64)],
    [(1, "001_current.sql", "9" * 64)],
    [(2, "002_newer.sql", "9" * 64)],
])
def test_ac017_abort_before_running_sql_on_history_drift(tmp_path, history):
    (tmp_path / "001_current.sql").write_text("SELECT 5;", encoding="utf-8")
    (tmp_path / "003_later.sql").write_text("SELECT 6;", encoding="utf-8")
    migrations = discover(tmp_path)
    conn, cursor = connection(history)
    with pytest.raises(MigrationError, match="history differs"):
        apply(conn, migrations)
    assert all(call(m.sql) not in cursor.execute.call_args_list for m in migrations)
    assert not logged_writes(cursor)


def test_ac017_failure_in_later_statement_does_not_record_version(tmp_path):
    (tmp_path / "001_partial.sql").write_text("SELECT 1; SELECT broken;", encoding="utf-8")
    conn, cursor = connection()
    cursor.nextset.side_effect = pymysql.ProgrammingError(1054, "later statement failed")
    with pytest.raises(pymysql.ProgrammingError):
        apply(conn, discover(tmp_path))
    assert not logged_writes(cursor)


def test_ac017_lock_timeout_stops_before_ddl():
    conn, cursor = connection(acquired=0)
    with pytest.raises(MigrationError, match="lock"):
        apply(conn, [])
    assert cursor.execute.call_count == 1
    assert "GET_LOCK" in cursor.execute.call_args.args[0]


def test_ac017_driver_error_exit_code_and_credentials(monkeypatch, capsys):
    for key, value in {"USER": "migration-user", "PASSWORD": "fixture-secret", "DATABASE": "agentos"}.items():
        monkeypatch.setenv("AGENTOS_MYSQL_" + key, value)
    connect = MagicMock(side_effect=pymysql.OperationalError(1045, "denied fixture-secret"))
    monkeypatch.setattr(pymysql, "connect", connect)
    assert runner["main"]() == 1
    err = capsys.readouterr().err
    assert "1045" in err and "fixture-secret" not in err
    assert connect.call_args.kwargs["client_flag"] & CLIENT.MULTI_STATEMENTS
    assert connect.call_args.kwargs["autocommit"] is True
