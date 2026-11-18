"""MySQL 8.4 primary-store transactions for single-step, single-project tasks."""

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import os
import time
from uuid import uuid4

import pymysql
from pymysql.cursors import DictCursor

from agentos.contracts.errors import PlatformError


TERMINAL = {"cancelled", "failed", "succeeded", "timed_out"}


def _error(code):
    return PlatformError(code, code.replace("_", " ").capitalize())


def _json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def _id(value):
    if not isinstance(value, str) or not 0 < len(value) <= 128:
        raise _error("INVALID_ARGUMENT")
    return value


def _amount(value):
    if type(value) is not int or not 0 <= value < 2**63:
        raise _error("INVALID_ARGUMENT")
    return value


def _timestamp(value):
    return value.isoformat(timespec="microseconds") + "Z" if value is not None else None


def _one(cur, sql, args=()):
    cur.execute(sql, args)
    return cur.fetchone()


def _now(cur):
    return _one(cur, "SELECT UTC_TIMESTAMP(6) AS clock")["clock"]


def _policy(cur, tenant, project, subject):
    head = _one(cur, "SELECT epoch FROM ag_policy_head WHERE tenant_id=%s AND project_id=%s FOR SHARE",
                (tenant, project))
    member = _one(cur, """SELECT enabled FROM ag_project_member
                          WHERE tenant_id=%s AND project_id=%s AND subject_id=%s""",
                  (tenant, project, subject)) if head else None
    if not member or not member["enabled"]:
        raise _error("FORBIDDEN")
    return head["epoch"]


def _writer_head(cur, tenant, project):
    # The duplicate-key update also serializes concurrent writers on a new project.
    cur.execute("""INSERT INTO ag_policy_head (tenant_id, project_id) VALUES (%s,%s)
                   ON DUPLICATE KEY UPDATE epoch=epoch""", (tenant, project))


def _task(cur, task_id):
    row = _one(cur, "SELECT * FROM ag_task WHERE task_id=%s FOR UPDATE", (task_id,))
    if row is None:
        raise _error("RESOURCE_NOT_FOUND")
    return row


def _event(cur, task_id, kind):
    cur.execute("""INSERT INTO ag_task_event
                   (event_id,task_id,sequence_no,attempt,event_type,occurred_at)
                   SELECT %s,task_id,version,attempt,%s,UTC_TIMESTAMP(6)
                   FROM ag_task WHERE task_id=%s""", (uuid4().hex, kind, task_id))


def _lease(cur, row, ticket):
    if row["state"] == "cancelled":
        raise _error("CANCELLED")
    clock = _now(cur)
    if row["deadline_at"] <= clock:
        raise _error("DEADLINE_EXCEEDED")
    if (row["state"] != "running" or row["worker_id"] != ticket["worker_id"]
            or row["lease_epoch"] != ticket["lease_epoch"] or row["lease_until"] <= clock):
        raise _error("LEASE_LOST")


def _check_refs(cur, row, refs):
    for ref in sorted(refs, key=lambda item: item["source_id"]):
        if (ref.get("tenant_id", row["tenant_id"]) != row["tenant_id"]
                or ref.get("project_id", row["project_id"]) != row["project_id"]):
            raise _error("CONTEXT_STALE")
        source = _one(cur, """SELECT version, deleted, location FROM ag_source
                              WHERE tenant_id=%s AND project_id=%s AND source_id=%s""",
                      (row["tenant_id"], row["project_id"], ref["source_id"]))
        if (not source or source["deleted"] or source["version"] != ref["version"]
                or source["location"] != ref["location"]):
            raise _error("CONTEXT_STALE")


def _view(cur, row):
    task = {key: row[key] for key in (
        "task_id", "tenant_id", "subject_id", "project_id", "goal", "agent_version_id", "state",
        "version", "attempt", "worker_id", "lease_epoch", "limit_micros", "settled_micros",
        "reserved_micros", "error_code")}
    task.update(deadline_at=_timestamp(row["deadline_at"]), root_task_id=row["task_id"],
                parent_task_id=None, budget_account_id=row["task_id"], identity_ref=row["subject_id"],
                currency="CNY", completion_criteria_ref="criteria_support_readonly_v1",
                result=None, snapshot_id=None, dispatch_attempt_id=None, accounting_state="final")
    dispatch = _one(cur, """SELECT dispatch_attempt_id,snapshot_id,snapshot,receipt_id,reservation,
                            actual_micros,input_tokens,output_tokens
                            FROM ag_dispatch WHERE task_id=%s""", (row["task_id"],))
    if dispatch:
        task.update(dispatch_attempt_id=dispatch["dispatch_attempt_id"], snapshot_id=dispatch["snapshot_id"],
                    accounting_state="pending" if dispatch["receipt_id"] is None else "final")
        task["usage"] = {"input_tokens": dispatch["input_tokens"], "output_tokens": dispatch["output_tokens"]}
        task["budget_overrun"] = (dispatch["actual_micros"] is not None and
                                  dispatch["actual_micros"] > dispatch["reservation"])
        if row["result"]:
            try:
                _check_refs(cur, row, json.loads(dispatch["snapshot"])["source_refs"])
            except PlatformError as exc:
                task["result_error"] = exc.code
            else:
                task["result"] = json.loads(row["result"])
    return task


def _owned(cur, tenant, subject, task_id, allowed_projects):
    scope = _one(cur, """SELECT project_id FROM ag_task
                         WHERE tenant_id=%s AND subject_id=%s AND task_id=%s""", (tenant, subject, task_id))
    if not scope or (allowed_projects is not None and scope["project_id"] not in allowed_projects):
        raise _error("RESOURCE_NOT_FOUND")
    try:
        _policy(cur, tenant, scope["project_id"], subject)
    except PlatformError:
        raise _error("RESOURCE_NOT_FOUND") from None
    return _task(cur, task_id)


def _terminal(cur, row, state, code):
    cur.execute("""UPDATE ag_task SET state=%s,error_code=%s,version=version+1,
                   worker_id=NULL,lease_until=NULL WHERE task_id=%s""", (state, code, row["task_id"]))
    _event(cur, row["task_id"], state)


def _validate_receipt(actual_micros, receipt_id, input_tokens, output_tokens):
    _amount(actual_micros)
    if not isinstance(receipt_id, str) or not 0 < len(receipt_id) <= 256:
        raise _error("INVALID_ARGUMENT")
    if (input_tokens is None) != (output_tokens is None):
        raise _error("INVALID_ARGUMENT")
    if input_tokens is not None:
        _amount(input_tokens)
        _amount(output_tokens)


def _settle(cur, dispatch, receipt_id, actual_micros, input_tokens, output_tokens):
    facts = (receipt_id, actual_micros, input_tokens, output_tokens)
    if dispatch["receipt_id"] is not None:
        if tuple(dispatch[key] for key in ("receipt_id", "actual_micros", "input_tokens", "output_tokens")) != facts:
            raise _error("IDEMPOTENCY_CONFLICT")
        return False
    cur.execute("""UPDATE ag_dispatch SET receipt_id=%s,actual_micros=%s,input_tokens=%s,output_tokens=%s
                   WHERE dispatch_attempt_id=%s""", (*facts, dispatch["dispatch_attempt_id"]))
    cur.execute("""UPDATE ag_task SET reserved_micros=reserved_micros-%s,
                   settled_micros=settled_micros+%s,version=version+1 WHERE task_id=%s""",
                (dispatch["reservation"], actual_micros, dispatch["task_id"]))
    _event(cur, dispatch["task_id"], "receipt_settled")
    return True


class MySQLStore:
    def __init__(self, **connection):
        self._connection = connection

    @classmethod
    def from_env(cls):
        return cls(host=os.environ.get("AGENTOS_MYSQL_HOST", "127.0.0.1"),
                   port=int(os.environ.get("AGENTOS_MYSQL_PORT", "3306")),
                   user=os.environ["AGENTOS_MYSQL_USER"], password=os.environ["AGENTOS_MYSQL_PASSWORD"],
                   database=os.environ["AGENTOS_MYSQL_DATABASE"])

    def _tx(self, work):
        for attempt in range(3):
            conn = None
            try:
                conn = pymysql.connect(**self._connection, cursorclass=DictCursor, charset="utf8mb4",
                                       autocommit=False, connect_timeout=5, read_timeout=15, write_timeout=15)
                with conn.cursor() as cur:
                    cur.execute("SET TRANSACTION ISOLATION LEVEL READ COMMITTED")
                    conn.begin()
                    result = work(cur)
                conn.commit()
                return result
            except pymysql.MySQLError as exc:
                # Closing the connection rolls back the entire transaction even for error 1205.
                if not (exc.args and exc.args[0] in {1205, 1213} and attempt < 2):
                    # An unconfirmed COMMIT must never grant dispatch permission.
                    raise _error("TRANSIENT_DEPENDENCY_FAILURE") from exc
            finally:
                if conn is not None:
                    conn.close()
            time.sleep(0.025 * 2**attempt)

    def register_release(self, version_id, manifest):
        if not isinstance(manifest, dict):
            raise _error("INVALID_ARGUMENT")
        encoded = _json(manifest)
        if sha256(encoded.encode("utf-8")).hexdigest() != version_id:
            raise _error("INVALID_ARGUMENT")

        def work(cur):
            cur.execute("""INSERT INTO ag_release (version_id,manifest,registered_at)
                           VALUES (%s,%s,UTC_TIMESTAMP(6)) ON DUPLICATE KEY UPDATE version_id=version_id""",
                        (version_id, encoded))
            stored = _one(cur, "SELECT manifest FROM ag_release WHERE version_id=%s FOR UPDATE", (version_id,))
            if _json(json.loads(stored["manifest"])) != encoded:
                raise _error("STATE_CONFLICT")
        self._tx(work)

    def set_access(self, tenant_id, subject_id, project_id, enabled):
        tenant, subject, project = map(_id, (tenant_id, subject_id, project_id))
        def work(cur):
            _writer_head(cur, tenant, project)
            cur.execute("""INSERT INTO ag_project_member VALUES (%s,%s,%s,%s)
                           ON DUPLICATE KEY UPDATE enabled=%s""", (tenant, project, subject, enabled, enabled))
            if cur.rowcount:
                cur.execute("UPDATE ag_policy_head SET epoch=epoch+1 WHERE tenant_id=%s AND project_id=%s",
                            (tenant, project))
        self._tx(work)

    def put_source(self, tenant_id, project_id, source_id, title, text, location):
        tenant, project, source = map(_id, (tenant_id, project_id, source_id))
        if not all(isinstance(x, str) for x in (title, text, location)) or not text or not location:
            raise _error("INVALID_ARGUMENT")
        def work(cur):
            _writer_head(cur, tenant, project)
            old = _one(cur, """SELECT version,deleted,title,body,location FROM ag_source
                               WHERE tenant_id=%s AND project_id=%s AND source_id=%s""", (tenant, project, source))
            if old and not old["deleted"] and (old["title"],old["body"],old["location"]) == (title,text,location):
                return old["version"]
            revision = old["version"] + 1 if old else 1
            cur.execute("""INSERT INTO ag_source (tenant_id,project_id,source_id,version,title,body,location)
                           VALUES (%s,%s,%s,%s,%s,%s,%s)
                           ON DUPLICATE KEY UPDATE version=%s,title=%s,body=%s,location=%s,deleted=FALSE""",
                        (tenant,project,source,revision,title,text,location,revision,title,text,location))
            return revision
        return self._tx(work)

    def delete_source(self, tenant_id, project_id, source_id):
        def work(cur):
            _writer_head(cur, tenant_id, project_id)
            cur.execute("""UPDATE ag_source SET deleted=TRUE,version=version+1,title='',body='',location=''
                           WHERE tenant_id=%s AND project_id=%s AND source_id=%s AND deleted=FALSE""",
                        (tenant_id, project_id, source_id))
        self._tx(work)

    def create_task(self, tenant_id, subject_id, idempotency_key, request, *, replay_only=False):
        tenant, subject, key = map(_id, (tenant_id, subject_id, idempotency_key))
        project = _id(request["project_id"])
        release = _id(request["agent_version_id"])
        limit = _amount(request["limit_micros"])
        goal = request["goal"]
        if not isinstance(goal, str) or not goal.strip() or len(goal.encode()) > 65535:
            raise _error("INVALID_ARGUMENT")
        try:
            stamp = datetime.fromisoformat(request["deadline_at"].replace("Z", "+00:00"))
            if stamp.tzinfo is None:
                raise ValueError
            deadline = stamp.astimezone(timezone.utc).replace(tzinfo=None)
        except (ValueError, TypeError, AttributeError):
            raise _error("INVALID_ARGUMENT") from None
        digest = sha256(_json(request).encode()).hexdigest()
        new_id = uuid4().hex

        def work(cur):
            _policy(cur, tenant, project, subject)
            if replay_only:
                prior = _one(cur, """SELECT * FROM ag_task WHERE tenant_id=%s AND subject_id=%s
                                     AND idempotency_key=%s FOR UPDATE""", (tenant, subject, key))
                if not prior:
                    raise _error("INVALID_ARGUMENT")
                if prior["request_hash"] != digest:
                    raise _error("IDEMPOTENCY_CONFLICT")
                return _view(cur, prior)
            cur.execute("""INSERT INTO ag_task
                           (task_id,tenant_id,project_id,subject_id,idempotency_key,request_hash,
                            agent_version_id,goal,state,ready_at,deadline_at,limit_micros)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'queued',UTC_TIMESTAMP(6),%s,%s)
                           ON DUPLICATE KEY UPDATE task_id=task_id""",
                        (new_id, tenant, project, subject, key, digest, release, goal, deadline, limit))
            row = _one(cur, """SELECT * FROM ag_task WHERE tenant_id=%s AND subject_id=%s
                               AND idempotency_key=%s FOR UPDATE""", (tenant, subject, key))
            if row["request_hash"] != digest:
                raise _error("IDEMPOTENCY_CONFLICT")
            if row["task_id"] == new_id:
                if deadline <= _now(cur):
                    raise _error("DEADLINE_EXCEEDED")
                _event(cur, new_id, "queued")
            return _view(cur, row)
        return self._tx(work)

    def get_task(self, tenant_id, subject_id, task_id, *, allowed_projects=None):
        return self._tx(lambda cur: _view(cur, _owned(cur, tenant_id, subject_id, task_id, allowed_projects)))

    def cancel_task(self, tenant_id, subject_id, task_id, idempotency_key, expected_version, *, allowed_projects=None):
        key = _id(idempotency_key)
        def work(cur):
            row = _owned(cur, tenant_id, subject_id, task_id, allowed_projects)
            cur.execute("""INSERT INTO ag_cancel_request (tenant_id,subject_id,idempotency_key,task_id,expected_version)
                           VALUES (%s,%s,%s,%s,%s) ON DUPLICATE KEY UPDATE task_id=task_id""",
                        (tenant_id, subject_id, key, task_id, expected_version))
            if cur.rowcount == 0:
                prior = _one(cur, """SELECT task_id,expected_version FROM ag_cancel_request
                                     WHERE tenant_id=%s AND subject_id=%s AND idempotency_key=%s""",
                             (tenant_id, subject_id, key))
                if (prior["task_id"], prior["expected_version"]) != (task_id, expected_version):
                    raise _error("IDEMPOTENCY_CONFLICT")
            else:
                if row["version"] != expected_version:
                    raise _error("VERSION_CONFLICT")
                if row["state"] not in TERMINAL:
                    expired = row["deadline_at"] <= _now(cur)
                    _terminal(cur, row, "timed_out" if expired else "cancelled",
                              "DEADLINE_EXCEEDED" if expired else "CANCELLED")
            return _view(cur, _task(cur, task_id))
        return self._tx(work)

    def claim(self, worker_id, lease_seconds=60, agent_version_id=None):
        _id(worker_id)
        if type(lease_seconds) is not int or not 1 <= lease_seconds <= 86400:
            raise _error("INVALID_ARGUMENT")

        def work(cur):
            scope = "" if agent_version_id is None else "AND agent_version_id=%s"
            args = () if agent_version_id is None else (_id(agent_version_id),)
            row = _one(cur, f"""SELECT * FROM ag_task WHERE claimable=1
                                AND ready_at<=UTC_TIMESTAMP(6) {scope}
                                ORDER BY ready_at,task_id LIMIT 1 FOR UPDATE SKIP LOCKED""", args)
            if not row:
                return None
            clock = _now(cur)
            if row["deadline_at"] <= clock:
                _terminal(cur, row, "timed_out", "DEADLINE_EXCEEDED")
                return None
            sent = _one(cur, "SELECT receipt_id FROM ag_dispatch WHERE task_id=%s", (row["task_id"],))
            if sent:
                # A crash between commit and send is indistinguishable from a lost response.
                _terminal(cur, row, "failed", "EXTERNAL_OUTCOME_UNKNOWN" if sent["receipt_id"] is None else "LEASE_LOST")
                return None
            expiry = min(row["deadline_at"], clock + timedelta(seconds=lease_seconds))
            cur.execute("""UPDATE ag_task SET state='running',version=version+1,attempt=attempt+1,
                           lease_epoch=lease_epoch+1,worker_id=%s,lease_until=%s,ready_at=%s WHERE task_id=%s""",
                        (worker_id, expiry, expiry, row["task_id"]))
            _event(cur, row["task_id"], "claimed")
            return {"task_id": row["task_id"], "tenant_id": row["tenant_id"], "project_id": row["project_id"],
                    "subject_id": row["subject_id"], "worker_id": worker_id, "lease_epoch": row["lease_epoch"] + 1}
        ticket = self._tx(work)
        if ticket is None:
            return None

        def deliver(cur):
            # Queue selection never takes an upstream lock. Reauthorize before disclosing the goal.
            try:
                _policy(cur, ticket["tenant_id"], ticket["project_id"], ticket["subject_id"])
            except PlatformError:
                row = _task(cur, ticket["task_id"])
                _lease(cur, row, ticket)
                _terminal(cur, row, "failed", "FORBIDDEN")
                return None
            row = _task(cur, ticket["task_id"])
            _lease(cur, row, ticket)
            return _view(cur, row)
        return self._tx(deliver)

    def evidence(self, task):
        def work(cur):
            epoch = _policy(cur, task["tenant_id"], task["project_id"], task["subject_id"])
            row = _task(cur, task["task_id"])
            _lease(cur, row, task)
            if (row["tenant_id"], row["project_id"], row["subject_id"]) != (
                    task["tenant_id"], task["project_id"], task["subject_id"]):
                raise _error("FORBIDDEN")
            cur.execute("""SELECT tenant_id,project_id,source_id,version,title,body AS text,location
                           FROM ag_source WHERE tenant_id=%s AND project_id=%s AND deleted=FALSE
                           ORDER BY source_id""", (row["tenant_id"], row["project_id"]))
            return epoch, list(cur.fetchall())
        return self._tx(work)

    def admit(self, task, snapshot, reserve_micros, retained_payload=None):
        _amount(reserve_micros)
        mode = snapshot.get("retention_mode")
        if mode not in {"full", "redacted", "metadata_only"}:
            raise _error("INVALID_ARGUMENT")
        if (mode == "metadata_only") != (retained_payload is None):
            raise _error("INVALID_ARGUMENT")
        digest = sha256(retained_payload).hexdigest() if retained_payload is not None else None
        if (digest != snapshot.get("stored_payload_digest")
                or mode == "full" and digest != snapshot["payload_digest"]):
            raise _error("CONTEXT_STALE")
        serialized = _json(snapshot)

        def work(cur):
            epoch = _policy(cur, task["tenant_id"], task["project_id"], task["subject_id"])
            row = _task(cur, task["task_id"])
            _lease(cur, row, task)
            if ((row["tenant_id"], row["project_id"], row["subject_id"]) != (
                    task["tenant_id"], task["project_id"], task["subject_id"])
                    or snapshot["identity_ref"] != row["subject_id"]
                    or snapshot["task_id"] != row["task_id"]
                    or snapshot["agent_version_id"] != row["agent_version_id"]
                    or snapshot["attempt"] != row["attempt"]
                    or snapshot["authorization_revision"] != epoch):
                raise _error("CONTEXT_STALE")
            _check_refs(cur, row, snapshot["source_refs"])
            if _one(cur, "SELECT task_id FROM ag_dispatch WHERE task_id=%s", (row["task_id"],)):
                raise _error("EXTERNAL_OUTCOME_UNKNOWN")
            if row["settled_micros"] + row["reserved_micros"] + reserve_micros > row["limit_micros"]:
                raise _error("BUDGET_EXHAUSTED")
            clock = _now(cur)
            until = min(clock + timedelta(seconds=5), row["deadline_at"], row["lease_until"])
            if until <= clock:
                raise _error("LEASE_LOST")
            dispatch = uuid4().hex
            cur.execute("""INSERT INTO ag_dispatch
                           (dispatch_attempt_id,task_id,worker_id,lease_epoch,snapshot_id,snapshot,
                            retained_payload,reservation,admitted_at,valid_until)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                        (dispatch, row["task_id"], row["worker_id"], row["lease_epoch"],
                         snapshot["snapshot_id"], serialized, retained_payload, reserve_micros, clock, until))
            cur.execute("""UPDATE ag_task SET reserved_micros=reserved_micros+%s,version=version+1
                           WHERE task_id=%s""", (reserve_micros, row["task_id"]))
            _event(cur, row["task_id"], "dispatch_admitted")
            return {"dispatch_attempt_id": dispatch, "valid_for_seconds": (until-clock).total_seconds()}
        return self._tx(work)

    def finish(self, task, dispatch_attempt_id, result, actual_micros, receipt_id, *,
               input_tokens=None, output_tokens=None):
        _validate_receipt(actual_micros, receipt_id, input_tokens, output_tokens)

        def work(cur):
            # Receipts remain billable after revocation or cancellation. Only delivery requires access.
            denial = None
            try:
                _policy(cur, task["tenant_id"], task["project_id"], task["subject_id"])
            except PlatformError as exc:
                denial = exc.code
            row = _task(cur, task["task_id"])
            dispatch = _one(cur, """SELECT * FROM ag_dispatch WHERE task_id=%s AND dispatch_attempt_id=%s
                                    FOR UPDATE""", (row["task_id"], dispatch_attempt_id))
            if not dispatch or (dispatch["worker_id"], dispatch["lease_epoch"]) != (
                    task["worker_id"], task["lease_epoch"]):
                raise _error("LEASE_LOST")
            if not _settle(cur, dispatch, receipt_id, actual_micros, input_tokens, output_tokens):
                return
            if row["state"] in TERMINAL:
                return
            try:
                _lease(cur, row, task)
            except PlatformError as exc:
                if exc.code == "DEADLINE_EXCEEDED":
                    _terminal(cur, row, "timed_out", exc.code)
                return
            if not denial:
                try:
                    _check_refs(cur, row, json.loads(dispatch["snapshot"])["source_refs"])
                except PlatformError as exc:
                    denial = exc.code
            if denial or result.get("status") == "failed":
                _terminal(cur, row, "failed", denial or result["error"])
            elif row["settled_micros"] + actual_micros > row["limit_micros"]:
                _terminal(cur, row, "failed", "BUDGET_EXHAUSTED")
            else:
                cur.execute("UPDATE ag_task SET result=%s WHERE task_id=%s", (_json(result), row["task_id"]))
                _terminal(cur, row, "succeeded", None)
        self._tx(work)

    def reconcile(self, tenant_id, dispatch_attempt_id, receipt_id, actual_micros, *,
                  input_tokens=None, output_tokens=None):
        """Trusted operator settlement from confirmed provider evidence; no model content is published."""
        _validate_receipt(actual_micros, receipt_id, input_tokens, output_tokens)
        def work(cur):
            scope = _one(cur, """SELECT t.task_id FROM ag_dispatch d JOIN ag_task t ON t.task_id=d.task_id
                                 WHERE t.tenant_id=%s AND d.dispatch_attempt_id=%s""", (tenant_id, dispatch_attempt_id))
            if not scope:
                raise _error("RESOURCE_NOT_FOUND")
            row = _task(cur, scope["task_id"])
            if row["state"] not in TERMINAL and row["lease_until"] > _now(cur):
                raise _error("STATE_CONFLICT")
            dispatch = _one(cur, "SELECT * FROM ag_dispatch WHERE dispatch_attempt_id=%s FOR UPDATE",
                            (dispatch_attempt_id,))
            _settle(cur, dispatch, receipt_id, actual_micros, input_tokens, output_tokens)
            if row["state"] not in TERMINAL:
                expired = row["deadline_at"] <= _now(cur)
                _terminal(cur, row, "timed_out" if expired else "failed",
                          "DEADLINE_EXCEEDED" if expired else "LEASE_LOST")
        self._tx(work)

    def fail(self, task, code):
        def work(cur):
            row = _task(cur, task["task_id"])
            _lease(cur, row, task)
            _terminal(cur, row, "failed", code)
        self._tx(work)

    def get_snapshot(self, tenant_id, subject_id, snapshot_id, *, allowed_projects=None):
        def work(cur):
            pointer = _one(cur, "SELECT task_id FROM ag_dispatch WHERE snapshot_id=%s", (snapshot_id,))
            if not pointer:
                raise _error("RESOURCE_NOT_FOUND")
            row = _owned(cur, tenant_id, subject_id, pointer["task_id"], allowed_projects)
            dispatch = _one(cur, "SELECT snapshot,retained_payload FROM ag_dispatch WHERE snapshot_id=%s",
                            (snapshot_id,))
            metadata = json.loads(dispatch["snapshot"])
            _check_refs(cur, row, metadata["source_refs"])
            payload = dispatch["retained_payload"]
            if payload is not None and sha256(payload).hexdigest() != metadata["stored_payload_digest"]:
                raise _error("SNAPSHOT_CONTENT_UNAVAILABLE")
            return {**metadata, "project_id": row["project_id"],
                    "retained_payload": json.loads(payload) if payload is not None else None}
        return self._tx(work)
