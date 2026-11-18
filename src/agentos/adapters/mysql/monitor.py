"""Content-free projections of task events, capability observations and the billing ledger."""

from datetime import timedelta
import json


CAPABILITY_ERRORS = frozenset({
    "BUDGET_EXHAUSTED", "CANCELLED", "CAPABILITY_FAILED", "CONTEXT_BUDGET_EXCEEDED",
    "CONTEXT_STALE", "DEADLINE_EXCEEDED", "FORBIDDEN", "INTERNAL_ERROR", "INVALID_ARGUMENT",
    "LEASE_LOST", "MISSING_EVIDENCE", "OUTPUT_LIMIT_EXCEEDED", "POLICY_UNAVAILABLE", "RESOURCE_NOT_FOUND",
    "STATE_CONFLICT", "TOOL_SCHEMA_CHANGED", "TRANSIENT_DEPENDENCY_FAILURE", "VERSION_CONFLICT",
})


def _stamp(dt):
    return dt.isoformat(timespec="microseconds") + "Z"


def read_dispatch(cur, task_id):
    cur.execute("""SELECT dispatch_attempt_id,snapshot_id,
                   CAST(snapshot->>'$.attempt' AS UNSIGNED) AS attempt,
                   snapshot->>'$.model' AS model,SHA2(receipt_id,256) AS receipt_ref,
                   reservation,actual_micros,input_tokens,output_tokens
                   FROM ag_dispatch WHERE task_id=%s""", (task_id,))
    return cur.fetchone()


def event_position(event):
    if "sequence_no" in event:
        return event["occurred_at"], 2, event["sequence_no"]
    return event["occurred_at"], (0 if event["event"] == "started" else 1), event["span_id"]


def _before(before, stream, time_column, id_column):
    if before is None:
        return "", ()
    at, boundary_stream, identifier = before
    if stream == boundary_stream:
        return f"AND ({time_column},{id_column})<(%s,%s)", (at, identifier)
    comparator = "<=" if stream < boundary_stream else "<"
    return f"AND {time_column}{comparator}%s", (at,)


def timeline_events(cur, task_id, dispatch, attempt=None, before=None):
    # The caller holds the task row. All writers of these observations take the same lock.
    task_filter = span_filter = ""
    task_args = span_args = (task_id,)
    if attempt is not None:
        # A receipt may arrive after execution ends. Its owner is the admitted dispatch.
        task_filter = """AND ((event_type NOT IN ('dispatch_admitted','receipt_settled') AND attempt=%s)
                         OR (event_type IN ('dispatch_admitted','receipt_settled') AND %s))"""
        task_args += (attempt, bool(dispatch and dispatch["attempt"] == attempt))
        span_filter = "AND attempt=%s"
        span_args += (attempt,)
    boundary, key = _before(before, 2, "occurred_at", "sequence_no")
    cur.execute(f"""SELECT event_id,sequence_no,attempt,event_type AS event,occurred_at
                    FROM ag_task_event WHERE task_id=%s {task_filter} {boundary}
                    ORDER BY occurred_at DESC,sequence_no DESC LIMIT 501""", (*task_args, *key))
    events = list(cur.fetchall())
    for event in events:
        event["kind"] = "task"
        if dispatch and event["event"] in ("dispatch_admitted", "receipt_settled"):
            event.update(kind="model", attempt=dispatch["attempt"], dispatch_attempt_id=dispatch["dispatch_attempt_id"],
                         snapshot_id=dispatch["snapshot_id"], model=dispatch["model"])
            if event["event"] == "dispatch_admitted":
                event["reservation_micros"] = str(dispatch["reservation"])
            else:
                event.update(actual_micros=str(dispatch["actual_micros"]), receipt_ref=dispatch["receipt_ref"],
                             input_tokens=str(dispatch["input_tokens"]) if dispatch["input_tokens"] is not None else None,
                             output_tokens=str(dispatch["output_tokens"]) if dispatch["output_tokens"] is not None else None)

    for at, completed in (("started_at", False), ("ended_at", True)):
        boundary, key = _before(before, int(completed), at, "span_id")
        cur.execute(f"""SELECT span_id,parent_span_id,attempt,kind,name,version,digest,outcome,error_code,
                        snapshot_id,{at} AS occurred_at,
                        TIMESTAMPDIFF(MICROSECOND,started_at,ended_at)/1000 AS duration_ms
                        FROM ag_capability_span WHERE task_id=%s {span_filter} AND {at} IS NOT NULL {boundary}
                        ORDER BY {at} DESC,span_id DESC LIMIT 501""", (*span_args, *key))
        for span in cur.fetchall():
            event = {key: span[key] for key in ("span_id", "parent_span_id", "attempt", "kind", "name",
                                                "version", "digest", "occurred_at")}
            event["event"] = span["outcome"] if completed else "started"
            if completed:
                event.update(error_code=span["error_code"], snapshot_id=span["snapshot_id"],
                             duration_ms=float(span["duration_ms"]))
            events.append(event)
    # Each SQL source uses this same order. At a tie, a span start precedes its finish.
    events.sort(key=event_position)
    truncated = len(events) > 500
    events = events[-500:]
    for event in events:
        event["occurred_at"] = _stamp(event["occurred_at"])
    return {"truncated": truncated, "events": events}


def timeline(cur, task):
    dispatch = read_dispatch(cur, task["task_id"])
    return {**{k: task[k] for k in ("task_id", "project_id", "agent_version_id", "state", "error_code")},
            **timeline_events(cur, task["task_id"], dispatch)}


def project_report(cur, tenant, project, subject, end, hours, limit):
    """One SQL snapshot per report. Aggregate the one-to-one dispatch and the many spans separately."""
    start = end - timedelta(hours=hours)
    subject_check = "" if subject is None else "AND t.subject_id = %s"
    params = (tenant, project, start, end) + (() if subject is None else (subject,)) + (limit,)
    cur.execute(f"""
        WITH cohort AS (
            SELECT t.task_id,t.state,t.error_code,t.agent_version_id,t.settled_micros,t.reserved_micros,
                   t.monitor_created_at AS created_at
            FROM ag_task t
            WHERE t.tenant_id=%s AND t.project_id=%s AND t.monitor_created_at BETWEEN %s AND %s {subject_check}
        ), ledger AS (
            SELECT c.*,d.dispatch_attempt_id,d.receipt_id,d.input_tokens,d.output_tokens
            FROM cohort c LEFT JOIN ag_dispatch d ON d.task_id=c.task_id
        ), spans AS (
            SELECT s.kind,s.name,s.version,s.digest,s.outcome,s.task_id,s.span_id,
                   TIMESTAMPDIFF(MICROSECOND,s.started_at,s.ended_at)/1000 AS duration_ms
            FROM cohort c JOIN ag_capability_span s ON s.task_id=c.task_id
        ), duration_ranks AS (
            SELECT kind,name,version,digest,duration_ms,
                   ROW_NUMBER() OVER (PARTITION BY kind,name,version,digest ORDER BY duration_ms,span_id) AS position_no,
                   COUNT(*) OVER (PARTITION BY kind,name,version,digest) AS bucket_size
            FROM spans WHERE duration_ms IS NOT NULL
        ), percentiles AS (
            SELECT kind,name,version,digest,duration_ms AS p95_duration_ms FROM duration_ranks
            WHERE position_no=CEILING(0.95 * bucket_size)
        ), capabilities AS (
            SELECT kind,name,version,digest,COUNT(*) AS calls,
                   COUNT(IF(outcome='succeeded',1,NULL)) AS succeeded,
                   COUNT(IF(outcome='failed',1,NULL)) AS failed,
                   COUNT(IF(outcome='started',1,NULL)) AS unfinished,
                   ROUND(AVG(duration_ms),3) AS avg_duration_ms,MAX(duration_ms) AS max_duration_ms
            FROM spans GROUP BY kind,name,version,digest
            ORDER BY calls DESC,kind,name,version,digest LIMIT 201
        ), recent AS (
            SELECT * FROM ledger ORDER BY created_at DESC,task_id DESC LIMIT %s
        )
        SELECT 'summary' AS section,JSON_OBJECT(
            'tasks',COUNT(*),'queued',COUNT(IF(state='queued',1,NULL)),
            'running',COUNT(IF(state='running',1,NULL)),'succeeded',COUNT(IF(state='succeeded',1,NULL)),
            'failed',COUNT(IF(state='failed',1,NULL)),'cancelled',COUNT(IF(state='cancelled',1,NULL)),
            'timed_out',COUNT(IF(state='timed_out',1,NULL)),'dispatches',COUNT(dispatch_attempt_id),
            'pending_receipts',COUNT(dispatch_attempt_id)-COUNT(receipt_id),
            'usage_receipts',COUNT(input_tokens),'tasks_with_spans',(SELECT COUNT(DISTINCT task_id) FROM spans),
            'settled_micros',CAST(COALESCE(SUM(settled_micros),0) AS CHAR),
            'reserved_micros',CAST(COALESCE(SUM(reserved_micros),0) AS CHAR),
            'input_tokens',CAST(COALESCE(SUM(input_tokens),0) AS CHAR),
            'output_tokens',CAST(COALESCE(SUM(output_tokens),0) AS CHAR)
        ) AS payload FROM ledger
        UNION ALL SELECT 'task',JSON_OBJECT(
            'task_id',task_id,'state',state,'error_code',error_code,'agent_version_id',agent_version_id,
            'created_at',created_at,'accounting_state',IF(dispatch_attempt_id IS NOT NULL
                                       AND receipt_id IS NULL,'pending','final')
        ) FROM recent
        UNION ALL SELECT 'capability',JSON_OBJECT(
            'kind',c.kind,'name',c.name,'version',c.version,'digest',c.digest,'calls',c.calls,'succeeded',c.succeeded,
            'failed',c.failed,'unfinished',c.unfinished,'avg_duration_ms',c.avg_duration_ms,
            'p95_duration_ms',p.p95_duration_ms,'max_duration_ms',c.max_duration_ms
        ) FROM capabilities c LEFT JOIN percentiles p USING (kind,name,version,digest)
        """, params)
    report = {"project_id": project, "scope": "project" if subject is None else "subject",
              "window_start": _stamp(start), "window_end": _stamp(end), "recent_tasks": [], "capabilities": []}
    for row in cur.fetchall():
        item = json.loads(row["payload"])
        if row["section"] == "summary":
            report["summary"] = item
        elif row["section"] == "task":
            item["created_at"] = item["created_at"].replace(" ", "T") + "Z"
            report["recent_tasks"].append(item)
        else:
            report["capabilities"].append(item)
    report["recent_tasks"].sort(key=lambda item: (item["created_at"], item["task_id"]), reverse=True)
    report["capabilities"].sort(key=lambda item: (-item["calls"], item["kind"], item["name"],
                                                  item["version"], item["digest"]))
    report["capabilities_truncated"] = len(report["capabilities"]) > 200
    report["capabilities"] = report["capabilities"][:200]
    return report
