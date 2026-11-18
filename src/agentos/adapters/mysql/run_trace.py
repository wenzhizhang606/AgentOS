"""Browsable, metadata-only projections of worker claims and their execution attempts."""

import base64
import binascii
from datetime import datetime, timedelta
from hashlib import sha256
import json

from agentos.adapters.mysql import monitor
from agentos.contracts.errors import PlatformError


# Legacy claim events may lack a worker ID. Every fallback is fenced to this same attempt.
_RUN_COLUMNS = """
    t.task_id,e.attempt,e.occurred_at AS started_at,
    IF(t.attempt=e.attempt,t.state,'superseded') AS state,
    COALESCE(e.worker_id,IF(t.attempt=e.attempt,t.worker_id,NULL),
             (SELECT s.worker_id FROM ag_capability_span s
              WHERE s.task_id=t.task_id AND s.attempt=e.attempt LIMIT 1),
             (SELECT d.worker_id FROM ag_dispatch d WHERE d.task_id=t.task_id
              AND CAST(d.snapshot->>'$.attempt' AS UNSIGNED)=e.attempt)) AS worker_id
"""
_CLAIMS = """FROM ag_task t JOIN ag_task_event e ON e.task_id=t.task_id
             WHERE e.event_type='claimed' AND e.attempt>0"""


def _encode_cursor(fields):
    data = json.dumps(fields, ensure_ascii=False, separators=(",", ":"))
    return base64.urlsafe_b64encode(data.encode()).decode("ascii")


def _decode_cursor(cursor):
    if not isinstance(cursor, str) or not 1 <= len(cursor) <= 1024:
        raise ValueError()
    decoded = json.loads(base64.b64decode(cursor, altchars=b"-_", validate=True))
    if not isinstance(decoded, list):
        raise ValueError()
    return decoded


def _read_cursor(cursor, scope, now, hours, for_runs):
    if cursor is None:
        return now, ()
    try:
        decoded = _decode_cursor(cursor)
        if len(decoded) != (5 if for_runs else 4) or decoded[0] != scope:
            raise ValueError()
        end, started = (datetime.fromisoformat(value) for value in decoded[1:3])
        if (end.tzinfo is not None or started.tzinfo is not None or end.isoformat() != decoded[1]
                or started.isoformat() != decoded[2] or end > now or end.year < 1001
                or not end - timedelta(hours=hours) <= started <= end):
            raise ValueError()
        if not isinstance(decoded[3], str) or not 1 <= len(decoded[3]) <= 128:
            raise ValueError()
        decoded[3].encode("utf-8")  # Reject isolated surrogates before binding a MySQL parameter.
        if for_runs and (type(decoded[4]) is not int or not 1 <= decoded[4] < 2**64):
            raise ValueError()
        return end, (started, *decoded[3:])
    except (ValueError, TypeError, OverflowError, RecursionError, binascii.Error):
        raise PlatformError("INVALID_ARGUMENT", "Invalid run browser cursor") from None


def _read_before(before, task_id, attempt):
    if before is None:
        return None
    try:
        run_task_id, run_attempt, stamp, stream, identifier = _decode_cursor(before)
        if run_task_id != task_id or type(run_attempt) is not int or run_attempt != attempt:
            raise ValueError()
        if not isinstance(stamp, str) or not stamp.endswith("Z"):
            raise ValueError()
        at = datetime.fromisoformat(stamp[:-1])
        if at.tzinfo is not None or at.year < 1000 or monitor._stamp(at) != stamp:
            raise ValueError()
        if type(stream) is not int or stream not in (0, 1, 2):
            raise ValueError()
        if stream == 2:
            if type(identifier) is not int or not 1 <= identifier < 2**64:
                raise ValueError()
        elif not isinstance(identifier, str) or not 1 <= len(identifier) <= 32:
            raise ValueError()
        else:
            identifier.encode("utf-8")
        return at, stream, identifier
    except (ValueError, TypeError, OverflowError, RecursionError, binascii.Error):
        raise PlatformError("INVALID_ARGUMENT", "Invalid run event cursor") from None


def _run_view(row):
    return {**row, "run_id": f"{row['task_id']}:{row['attempt']}", "started_at": monitor._stamp(row["started_at"])}


def browse(cur, tenant, project, subject, now, hours, limit, cursor, release=None):
    # Pin both pages to the same DB clock window and bind cursors to their authorized cohort.
    scope = sha256(json.dumps([tenant, project, subject, hours, release]).encode()).hexdigest()
    end, key = _read_cursor(cursor, scope, now, hours, release is not None)
    start = end - timedelta(hours=hours)
    cohort = _CLAIMS + " AND t.tenant_id=%s AND t.project_id=%s AND e.occurred_at BETWEEN %s AND %s"
    args = (tenant, project, start, end)
    if subject is not None:
        cohort += " AND t.subject_id=%s"
        args += (subject,)
    result = {"project_id": project, "scope": "project" if subject is None else "subject",
              "window_start": monitor._stamp(start), "window_end": monitor._stamp(end), "next_cursor": None}
    if release is None:
        boundary = "WHERE (g.last_started_at,g.agent_version_id)<(%s,%s)" if key else ""
        cur.execute(f"""WITH grouped AS (
                          SELECT t.agent_version_id,COUNT(*) AS run_count,MAX(e.occurred_at) AS last_started_at
                          {cohort} GROUP BY t.agent_version_id)
                        SELECT g.*,r.manifest->>'$.profile' AS profile FROM grouped g
                        LEFT JOIN ag_release r ON r.version_id=g.agent_version_id {boundary}
                        ORDER BY g.last_started_at DESC,g.agent_version_id DESC LIMIT %s""",
                    (*args, *key, limit + 1))
        rows = cur.fetchall()
        visible = rows[:limit]
        result["agents"] = [dict(row, last_started_at=monitor._stamp(row["last_started_at"])) for row in visible]
        cursor_fields = ("last_started_at", "agent_version_id")
    else:
        boundary = "AND (e.occurred_at,t.task_id,e.attempt)<(%s,%s,%s)" if key else ""
        cur.execute(f"""SELECT {_RUN_COLUMNS} {cohort} AND t.agent_version_id=%s {boundary}
                        ORDER BY e.occurred_at DESC,t.task_id DESC,e.attempt DESC LIMIT %s""",
                    (*args, release, *key, limit + 1))
        rows = cur.fetchall()
        visible = rows[:limit]
        result.update(agent_version_id=release, runs=[_run_view(row) for row in visible])
        cursor_fields = ("started_at", "task_id", "attempt")
    if len(rows) > limit:
        at, *identifiers = (visible[-1][field] for field in cursor_fields)
        result["next_cursor"] = _encode_cursor([scope, end.isoformat(), at.isoformat(), *identifiers])
    return result


def _accounting(dispatch):
    if dispatch is None:
        return {"state": "no_dispatch", "settled_micros": "0", "reserved_micros": "0",
                "input_tokens": None, "output_tokens": None}
    pending = dispatch["receipt_ref"] is None
    return {"state": "pending" if pending else "final",
            "settled_micros": "0" if pending else str(dispatch["actual_micros"]),
            "reserved_micros": str(dispatch["reservation"]) if pending else "0",
            "input_tokens": None if dispatch["input_tokens"] is None else str(dispatch["input_tokens"]),
            "output_tokens": None if dispatch["output_tokens"] is None else str(dispatch["output_tokens"])}


def trace(cur, task, attempt, before):
    task_id = task["task_id"]
    cur.execute(f"""SELECT {_RUN_COLUMNS},
                      (SELECT r.manifest->>'$.profile' FROM ag_release r
                       WHERE r.version_id=t.agent_version_id) AS profile,
                      EXISTS(SELECT 1 FROM ag_capability_span s
                             WHERE s.task_id=t.task_id AND s.attempt=e.attempt) AS has_capability_spans,
                      (SELECT x.occurred_at FROM ag_task_event x WHERE x.task_id=t.task_id AND x.attempt=e.attempt
                       AND x.event_type IN ('cancelled','succeeded','failed','timed_out')
                       ORDER BY x.sequence_no LIMIT 1) AS ended_at
                    {_CLAIMS} AND t.task_id=%s AND e.attempt=%s""", (task_id, attempt))
    row = cur.fetchone()
    if not row:
        raise PlatformError("RESOURCE_NOT_FOUND", "Run not found")
    run = _run_view(row)
    run["has_capability_spans"] = bool(run["has_capability_spans"])
    if run["ended_at"] is not None:
        run["ended_at"] = monitor._stamp(run["ended_at"])
    dispatch = monitor.read_dispatch(cur, task_id)
    if dispatch and dispatch["attempt"] != attempt:
        dispatch = None
    page = monitor.timeline_events(cur, task_id, dispatch, attempt, _read_before(before, task_id, attempt))
    page["next_before"] = (_encode_cursor([task_id, attempt, *monitor.event_position(page["events"][0])])
                           if page["truncated"] else None)
    return {**run, "project_id": task["project_id"], "agent_version_id": task["agent_version_id"],
            "error_code": task["error_code"] if task["attempt"] == attempt else None,
            "accounting": _accounting(dispatch), **page}
