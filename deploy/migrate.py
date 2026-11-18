"""Apply ordered MySQL scripts on one connection before starting AgentOS."""

from dataclasses import dataclass
from hashlib import sha256
import os
from pathlib import Path
import re
import sys

import pymysql
from pymysql.constants import CLIENT


MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations" / "mysql"
LOCK_NAME = "SHA2(CONCAT('agentos:migrations:', LOWER(DATABASE())), 256)"
HISTORY_DDL = """
    CREATE TABLE IF NOT EXISTS ag_schema_migration (
        migration_version INT UNSIGNED PRIMARY KEY,
        filename VARCHAR(255) NOT NULL,
        sha256 CHAR(64) NOT NULL,
        applied_at DATETIME(6) NOT NULL
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_bin
"""


class MigrationError(Exception):
    """Operator-facing message without connection credentials or SQL parameters."""


@dataclass(frozen=True)
class Migration:
    version: int
    filename: str
    sql: str
    digest: str


def discover(directory: Path) -> list[Migration]:
    files = {}
    for path in directory.glob("*.sql"):
        match = re.fullmatch(r"([0-9]+)_[A-Za-z0-9_-]+\.sql", path.name)
        if match is None:
            raise MigrationError(f"Expected a numbered SQL filename: {path.name}")
        version = int(match[1])
        if version in files or not 1 <= version <= 4_294_967_295:
            raise MigrationError(f"Invalid or duplicate migration number: {path.name}")
        # read_text folds CRLF and CR to LF on Linux and Windows.
        sql = path.read_text(encoding="utf-8-sig")
        if not sql.strip():
            raise MigrationError(f"Empty migration: {path.name}")
        files[version] = Migration(version, path.name, sql, sha256(sql.encode("utf-8")).hexdigest())
    if not files:
        raise MigrationError(f"No SQL migrations found in {directory}")
    return [files[v] for v in sorted(files)]


def check_history(migrations, history):
    """Applied versions must be an unchanged prefix of the image's migration files."""
    for index, (version, filename, digest) in enumerate(history):
        if index >= len(migrations):
            raise MigrationError(f"Database has a newer or missing migration: {filename}")
        expected = migrations[index]
        if (version, filename, digest) != (expected.version, expected.filename, expected.digest):
            raise MigrationError(f"Migration history differs at version {version}: {filename}")


def apply(conn, migrations):
    # Caller owns this single, autocommit connection. Closing it releases the
    # named lock even after DDL, partial execution, or an uncertain ledger write.
    with conn.cursor() as cursor:
        cursor.execute(f"SELECT GET_LOCK({LOCK_NAME}, 60)")
        if cursor.fetchone()[0] != 1:
            raise MigrationError("Could not acquire the database migration lock within 60 seconds")
        cursor.execute(HISTORY_DDL)
        cursor.execute("SELECT migration_version, filename, sha256 FROM ag_schema_migration ORDER BY migration_version")
        history = cursor.fetchall()
        check_history(migrations, history)
        for migration in migrations[len(history):]:
            print(f"Applying {migration.filename}", flush=True)
            cursor.execute(migration.sql)
            # An error from a later statement is reported by nextset(), including
            # failures after EXECUTE in scripts using session variables or PREPARE.
            while cursor.nextset():
                pass
            cursor.execute("""
                INSERT INTO ag_schema_migration (migration_version, filename, sha256, applied_at)
                VALUES (%s, %s, %s, UTC_TIMESTAMP(6))
            """, (migration.version, migration.filename, migration.digest))


def main() -> int:
    try:
        migrations = discover(MIGRATIONS)
        with pymysql.connect(
            host=os.environ.get("AGENTOS_MYSQL_HOST", "127.0.0.1"),
            port=int(os.environ.get("AGENTOS_MYSQL_PORT", "3306")),
            user=os.environ["AGENTOS_MYSQL_USER"],
            password=os.environ["AGENTOS_MYSQL_PASSWORD"],
            database=os.environ["AGENTOS_MYSQL_DATABASE"],
            connect_timeout=10, charset="utf8mb4", autocommit=True,
            client_flag=CLIENT.MULTI_STATEMENTS,
        ) as conn:
            apply(conn, migrations)
    except MigrationError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except pymysql.MySQLError as exc:
        number = exc.args[0] if exc.args and type(exc.args[0]) is int else "unknown"
        print(f"Migration failed (MySQL error {number})", file=sys.stderr)
        return 1
    except (KeyError, OSError, ValueError):
        print("Invalid migration input or database connection settings", file=sys.stderr)
        return 1
    print("Database migrations are current")
    return 0


if __name__ == "__main__":
    sys.exit(main())
