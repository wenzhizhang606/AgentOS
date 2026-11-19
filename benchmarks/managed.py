"""Run bounded benchmark tasks through the deployed API and worker."""

import argparse
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from http.client import HTTPConnection
import json
import os
from pathlib import Path
import re
from secrets import token_urlsafe
import subprocess
import time


MAX_TASKS = 32
MAX_TOTAL_RESERVED_MICROS = 500_000
ALLOWED_MODEL = "gpt-6.1-sol"
ALLOWED_ENDPOINT = "https://llapi.org/v1/chat/completions"
TERMINAL = {"succeeded", "failed", "timed_out", "cancelled"}


def utc_after(minutes):
    return (datetime.now(timezone.utc) + timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")


def write_json(path, value):
    temporary = path.with_suffix(".part")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def private_dir(name):
    if not re.fullmatch(r"bench-[a-z0-9-]{1,40}", name):
        raise ValueError("Benchmark run name must start with bench- and contain only lowercase letters, digits and hyphens")
    return Path("local/benchmarks") / name


def projects(name, count=MAX_TASKS):
    if not 1 <= count <= 642:
        raise ValueError("Expected 1 to 642 benchmark projects")
    return [f"{name}-{slot:02d}" for slot in range(1, count + 1)]


def provision(name, count=MAX_TASKS, *, credential_hours=4):
    if type(credential_hours) is not int or credential_hours < 1:
        raise ValueError("Credential validity must be a positive number of hours")
    expires_at = utc_after(credential_hours * 60)
    directory = private_dir(name)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    token_path = directory / "token"
    if not token_path.exists():
        token_path.write_text(token_urlsafe(32), encoding="ascii")
    token = token_path.read_text(encoding="ascii").strip()
    manifest = directory / "credentials.json"
    write_json(manifest, {"credentials": [{
        "token_sha256": sha256(token.encode("ascii")).hexdigest(),
        "tenant_id": name,
        "subject_id": "runner",
        "project_ids": projects(name, count),
        "monitor_project_ids": projects(name, count),
        "expires_at": expires_at,
    }]})
    manifest.chmod(0o444)


def compose_python(service, code, data=None):
    return subprocess.check_output(
        ["docker", "compose", "exec", "-T", service, "python", "-c", code],
        input=data,
        encoding="utf-8",
        timeout=45,
    )


def read_release():
    return json.loads(compose_python("worker", """
import json, os
from agentos.control.release import Release
from agentos.context.service import _request
r = Release.load(os.environ["AGENTOS_RELEASE_FILE"], environ=os.environ)
skills = [{**c.metadata(), "source": c.source, "trust": "profile_guidance", "text": c.text}
          for c in r.capabilities if c.kind == "skill"]
empty = {"label": "E1", "source_id": "", "version": 1, "location": "", "title": "", "text": ""}
frame = _request(r.model, r.max_output_tokens, "", [empty], skills)
print(json.dumps({"id": r.version_id, "model": r.model, "endpoint": r.endpoint,
                  "reservation_micros": r.reservation_micros, "max_output_tokens": r.max_output_tokens,
                  "window_tokens": r.window_tokens, "safety_margin_tokens": r.safety_margin_tokens,
                  "empty_swe_wire_bytes": len(frame)}))
"""))


def put_sources(tenant, project, sources):
    compose_python("api", """
import json, sys
from agentos.adapters.mysql.store import MySQLStore
data = json.load(sys.stdin)
store = MySQLStore.from_env()
store.set_access(data["tenant"], "runner", data["project"], True)
for source in data["sources"]:
    store.put_source(data["tenant"], data["project"], source["source_id"], source["title"],
                     source["text"], source["location"])
""", json.dumps({"tenant": tenant, "project": project, "sources": sources}))


class ManagedRunner:
    def __init__(self, name, port, *, case_labels=None, total_reserved_micros=MAX_TOTAL_RESERVED_MICROS):
        self.name = name
        self.directory = private_dir(name)
        self.assigned_projects = None
        if case_labels is not None:
            labels = tuple(case_labels)
            if len(set(labels)) != len(labels):
                raise ValueError("Duplicate benchmark case")
            self.assigned_projects = dict(zip(labels, projects(name, len(labels)), strict=True))
        self.token = (self.directory / "token").read_text(encoding="ascii").strip()
        self.port = port
        self.release = read_release()
        if (self.release["model"], self.release["endpoint"]) != (ALLOWED_MODEL, ALLOWED_ENDPOINT):
            raise ValueError("Unexpected live benchmark model or destination")
        reservation = self.release["reservation_micros"]
        count = MAX_TASKS if self.assigned_projects is None else len(self.assigned_projects)
        if reservation <= 0 or count * reservation > total_reserved_micros:
            raise ValueError("The configured release would exceed the benchmark reservation envelope")
        self.api("GET", "/readyz", expected={200})

    def api(self, method, path, *, expected, payload=None, key=None):
        headers = {"Authorization": f"Bearer {self.token}"}
        if payload is not None:
            headers.update({"Content-Type": "application/json", "Idempotency-Key": key})
        connection = HTTPConnection("127.0.0.1", self.port, timeout=15)
        try:
            connection.request(method, path, body=payload, headers=headers)
            response = connection.getresponse()
            result = json.loads(response.read())
            if response.status not in expected:
                raise RuntimeError(f"AgentOS benchmark API failed with HTTP {response.status}")
            return result
        finally:
            connection.close()

    def run_task(self, case_label, question, sources):
        if self.assigned_projects is not None and case_label not in self.assigned_projects:
            raise ValueError("Case is absent from the frozen benchmark manifest")
        case = self.directory / sha256(case_label.encode("utf-8")).hexdigest()[:24]
        plan_file = case / "plan.json"
        source_digest = sha256(json.dumps(sources, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
        if plan_file.exists():
            plan = json.loads(plan_file.read_text(encoding="utf-8"))
            if (plan["label"] != case_label or plan["source_digest"] != source_digest
                    or plan["request"]["goal"] != question or plan["request"]["agent_version_id"] != self.release["id"]
                    or (self.assigned_projects is not None
                        and self.assigned_projects[case_label] != plan["request"]["project_id"])):
                raise ValueError("Use a new benchmark run for changed inputs or release")
        else:
            if self.assigned_projects is not None:
                project = self.assigned_projects[case_label]
            else:
                saved = list(self.directory.glob("*/plan.json"))
                slots = projects(self.name)
                if len(saved) >= len(slots):
                    raise ValueError("Live benchmark task cap reached")
                project = slots[len(saved)]
            case.mkdir(exist_ok=True, mode=0o700)
            plan = {"label": case_label, "source_digest": source_digest, "request": {
                "project_id": project,
                "goal": question,
                "agent_version_id": self.release["id"],
                "completion_criteria_ref": "criteria_support_readonly_v1",
                "deadline_at": utc_after(8),
                "budget": {"limit_micros": self.release["reservation_micros"], "currency": "CNY", "max_concurrency": 1},
            }}
            write_json(plan_file, plan)

        receipt_file = case / "receipt.json"
        if receipt_file.exists():
            cached = json.loads(receipt_file.read_text(encoding="utf-8"))
            cached.setdefault("run_id", cached["trace"].get("run_id"))
            return cached
        request = plan["request"]
        id_file = case / "task-id"
        since = time.monotonic()
        if id_file.exists():
            task_id = id_file.read_text(encoding="ascii").strip()
        else:
            put_sources(self.name, request["project_id"], sources)
            self.api("GET", f"/v1/projects/{request['project_id']}/agents", expected={200})
            payload = json.dumps(request, ensure_ascii=False, sort_keys=True).encode("utf-8")
            created = self.api("POST", "/v1/tasks", expected={200, 202}, payload=payload,
                               key=f"{self.name}-{case.name}")
            task_id = created["task_id"]
            temp_id = id_file.with_suffix(".part")
            temp_id.write_text(task_id, encoding="ascii")
            temp_id.replace(id_file)
        path = f"/v1/tasks/{task_id}"
        deadline = time.monotonic() + 9 * 60
        while True:
            task = self.api("GET", path, expected={200})
            if task["state"] in TERMINAL:
                break
            if time.monotonic() >= deadline:
                raise TimeoutError(f"Task {task_id} is still active; resume the same benchmark run")
            time.sleep(1)
        trace = {}
        if task["attempt"]:
            trace = self.api("GET", f"{path}/runs/{task['attempt']}/trace", expected={200})
        snapshot = None
        if task.get("snapshot_id"):
            context = self.api("GET", f"/v1/context-snapshots/{task['snapshot_id']}", expected={200})
            snapshot = {name: context[name] for name in ("budget", "model", "source_refs", "wire_digest")}
        task.update(run_id=trace.get("run_id"), trace=trace, snapshot=snapshot,
                    accounting=trace.get("accounting"), elapsed_seconds=time.monotonic() - since)
        write_json(receipt_file, task)
        print(f"{case_label} {task['state']} {task.get('error_code') or '-'}", flush=True)
        return task


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_name")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("provision", help="write a short-lived credential for a dedicated evaluation API")
    live = sub.add_parser("live", help="run paid model probes and store one receipt per task")
    live.add_argument("--port", type=int, default=18001)
    live.add_argument("--humaneval", type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    if args.command == "provision":
        provision(args.run_name)
        return

    from benchmarks.coding import run_coding
    from benchmarks.collaboration import run_collaboration

    runner = ManagedRunner(args.run_name, args.port)
    with (runner.directory / "run.lock").open("a", encoding="ascii") as lock:
        import fcntl

        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        coding = run_coding(runner.run_task, args.humaneval)
        write_json(runner.directory / "coding.json", coding)
        collaboration = run_collaboration(runner.run_task)
        write_json(runner.directory / "collaboration.json", collaboration)
        receipts = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(runner.directory.glob("*/receipt.json"))]
        accounting = [r["accounting"] for r in receipts if r["accounting"]]
        snapshots = [r for r in receipts if r["snapshot"] and r.get("accounting")
                     and r["accounting"]["input_tokens"] is not None]
        observed_input_deltas = [
            int(r["accounting"]["input_tokens"]) - r["snapshot"]["budget"]["input_token_upper_estimate"]
            for r in snapshots
        ]
        summary = {
            "release": runner.release,
            "finished_at": utc_after(0),
            "submitted": len(receipts),
            "dispatches": sum(a["state"] in {"final", "pending"} for a in accounting),
            "settled_micros": sum(int(a["settled_micros"]) for a in accounting),
            "pending_reserved_micros": sum(int(a["reserved_micros"]) for a in accounting),
            "observed_input_deltas": observed_input_deltas,
            "max_total_reserved_micros": MAX_TOTAL_RESERVED_MICROS,
        }
        write_json(runner.directory / "summary.json", summary)
        print(json.dumps(summary))


if __name__ == "__main__":
    main()
