"""HTTP ingress for the pinned, single-worker read-only support profile."""

from datetime import datetime
import re
from typing import Annotated, Literal, Protocol
from uuid import uuid4

from fastapi import Depends, FastAPI, Header, Path, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field, field_validator
from starlette.exceptions import HTTPException

from agentos.contracts.errors import PlatformError
from agentos.governance.auth import Authenticator, Identity
from agentos.observability.page import console_html


PageCursor = Annotated[str | None, Query(min_length=1, max_length=1024)]


class TaskStore(Protocol):
    def ready(self, version_id: str) -> bool: ...
    def create_task(self, tenant_id, subject_id, idempotency_key, request: dict,
                    *, replay_only: bool = False) -> dict: ...
    def get_task(self, tenant_id, subject_id, task_id, *, allowed_projects) -> dict: ...
    def cancel_task(self, tenant_id, subject_id, task_id, idempotency_key,
                    expected_version, *, allowed_projects) -> dict: ...
    def get_snapshot(self, tenant_id, subject_id, snapshot_id, *, allowed_projects) -> dict: ...
    def require_project_access(self, tenant_id, subject_id, project_id, *, allowed_projects) -> None: ...
    def get_monitoring(self, tenant_id, subject_id, project_id, *, allowed_projects,
                       include_all_subjects, hours, limit) -> dict: ...
    def get_timeline(self, tenant_id, subject_id, task_id, *, allowed_projects,
                     monitor_project_ids) -> dict: ...
    def list_observed_agents(self, tenant_id, subject_id, project_id, *, allowed_projects,
                             include_all_subjects, hours, limit, cursor) -> dict: ...
    def get_agent_runs(self, tenant_id, subject_id, project_id, agent_version_id, *, allowed_projects,
                       include_all_subjects, hours, limit, cursor) -> dict: ...
    def get_run_trace(self, tenant_id, subject_id, task_id, attempt, *, allowed_projects,
                      monitor_project_ids, before) -> dict: ...


class Budget(BaseModel):
    model_config = ConfigDict(extra="forbid")
    currency: Literal["CNY"]
    limit_micros: int = Field(ge=0, le=2**63 - 1, strict=True)
    max_concurrency: int = Field(default=1, ge=1, le=1, strict=True)


class CreateTask(BaseModel):
    model_config = ConfigDict(extra="forbid")
    agent_version_id: str = Field(min_length=1, max_length=128)
    project_id: str = Field(min_length=1, max_length=128)
    goal: str = Field(min_length=1, max_length=16000)
    completion_criteria_ref: Literal["criteria_support_readonly_v1"]
    input_refs: list[str] = Field(default_factory=list, max_length=0)
    deadline_at: datetime
    budget: Budget

    @field_validator("goal", "project_id", "agent_version_id")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Expected a nonblank value")
        value.encode("utf-8")
        return value

    @field_validator("deadline_at", mode="before")
    @classmethod
    def utc_timestamp(cls, value):
        if not isinstance(value, str) or not re.fullmatch(
            r"[1-9][0-9]{3}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?Z",
            value,
        ):
            raise ValueError("Expected a UTC timestamp ending in Z")
        return value


ERROR_STATUS = {
    "INVALID_ARGUMENT": 400, "UNAUTHENTICATED": 401, "FORBIDDEN": 403,
    "RESOURCE_NOT_FOUND": 404, "VERSION_CONFLICT": 409, "STATE_CONFLICT": 409,
    "IDEMPOTENCY_CONFLICT": 409, "CONTEXT_STALE": 409, "DEADLINE_EXCEEDED": 409,
    "CANCELLED": 409, "SNAPSHOT_CONTENT_UNAVAILABLE": 410,
    "TRANSIENT_DEPENDENCY_FAILURE": 503,
}


def error_response(request: Request, status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"error": {"code": code, "message": message, "request_id": request.state.request_id,
                           "retryable": code == "TRANSIENT_DEPENDENCY_FAILURE", "details": {}}},
        headers={"WWW-Authenticate": "Bearer"} if status == 401 else {},
    )


def create_app(store: TaskStore, authenticator: Authenticator, agent_version_id: str,
               *, capability_catalog=()) -> FastAPI:
    app = FastAPI(title="AgentOS M1", version="1")
    # Freeze the public projection at the same boundary as the active release ID.
    resources = tuple(
        {field: entry[field] for field in ("kind", "name", "version", "digest")}
        for entry in capability_catalog if entry["kind"] in ("skill", "tool")
    )
    resource_counts = {kind: sum(entry["kind"] == kind for entry in resources) for kind in ("skill", "tool")}

    @app.middleware("http")
    async def request_metadata(request: Request, call_next):
        request.state.request_id = uuid4().hex
        response = await call_next(request)
        response.headers["X-Request-Id"] = request.state.request_id
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(PlatformError)
    async def platform_error(request: Request, exc: PlatformError):
        return error_response(request, ERROR_STATUS.get(exc.code, 500), exc.code, exc.message)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, exc: RequestValidationError):
        # Validation objects may include credentials, user text or model input.
        return error_response(request, 400, "INVALID_ARGUMENT", "Request does not match the API schema")

    @app.exception_handler(HTTPException)
    async def route_error(request: Request, exc: HTTPException):
        code = "RESOURCE_NOT_FOUND" if exc.status_code == 404 else "INVALID_ARGUMENT"
        return error_response(request, exc.status_code, code, str(exc.detail))

    @app.exception_handler(Exception)
    async def internal_error(request: Request, exc: Exception):
        return error_response(request, 500, "INTERNAL_ERROR", "Unexpected internal error")

    bearer = HTTPBearer(auto_error=False)

    def verified_identity(credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]) -> Identity:
        header = f"{credentials.scheme} {credentials.credentials}" if credentials else None
        return authenticator.authenticate(header)

    def task_identity(identity: Annotated[Identity, Depends(verified_identity)]) -> Identity:
        if identity.monitor_only:
            raise PlatformError("FORBIDDEN", "This credential grants access to monitoring metadata only")
        return identity

    def project_identity(project_id: str, identity: Annotated[Identity, Depends(verified_identity)]) -> Identity:
        if project_id not in identity.project_ids:
            raise PlatformError("FORBIDDEN", "Project outside credential scope")
        return identity

    def idempotency_key(value: Annotated[str, Header(alias="Idempotency-Key")]) -> str:
        if not re.fullmatch(r"[!-~]{1,128}", value):
            raise PlatformError("INVALID_ARGUMENT", "Expected a printable idempotency key of up to 128 characters")
        return value

    def version(value: Annotated[str, Header(alias="If-Match")]) -> int:
        if not re.fullmatch(r'(?:[1-9][0-9]{0,18}|"[1-9][0-9]{0,18}")', value):
            raise PlatformError("INVALID_ARGUMENT", "If-Match requires a positive integer version")
        result = int(value.strip('"'))
        if result > 2**63 - 1:
            raise PlatformError("INVALID_ARGUMENT", "If-Match is out of range")
        return result

    @app.get("/healthz", include_in_schema=False)
    def liveness():
        return {"status": "alive"}

    @app.get("/readyz", include_in_schema=False)
    def readiness():
        if not store.ready(agent_version_id):
            return JSONResponse(status_code=503, content={"status": "not_ready"})
        return {"status": "ready"}

    @app.api_route("/console", methods=["GET", "HEAD"], response_class=HTMLResponse, include_in_schema=False)
    @app.api_route("/resources", methods=["GET", "HEAD"], response_class=HTMLResponse, include_in_schema=False)
    @app.api_route("/monitor", methods=["GET", "HEAD"], response_class=HTMLResponse, include_in_schema=False)
    def dashboard():
        return HTMLResponse(console_html(), headers={
            "Content-Security-Policy": "default-src 'none'; script-src 'unsafe-inline'; "
                                       "style-src 'unsafe-inline'; connect-src 'self'; "
                                       "frame-ancestors 'none'; base-uri 'none'; form-action 'none'",
            "Referrer-Policy": "no-referrer",
        })

    @app.post("/v1/tasks", status_code=202)
    def submit(body: CreateTask, identity: Annotated[Identity, Depends(task_identity)],
               operation: Annotated[str, Depends(idempotency_key)]):
        if body.project_id not in identity.project_ids:
            raise PlatformError("FORBIDDEN", "Project outside credential scope")
        if body.deadline_at > identity.expires_at:
            raise PlatformError("INVALID_ARGUMENT", "Task deadline exceeds credential validity")
        command = {
            "project_id": body.project_id, "agent_version_id": body.agent_version_id,
            "goal": body.goal, "limit_micros": body.budget.limit_micros,
            "deadline_at": body.deadline_at.isoformat().replace("+00:00", "Z"),
        }
        return store.create_task(identity.tenant_id, identity.subject_id, operation, command,
                                 replay_only=body.agent_version_id != agent_version_id)

    @app.get("/v1/tasks/{task_id}")
    def get_task(task_id: str, identity: Annotated[Identity, Depends(task_identity)], response: Response):
        task = store.get_task(identity.tenant_id, identity.subject_id, task_id,
                              allowed_projects=identity.project_ids)
        response.headers["ETag"] = f'"{task["version"]}"'
        return task

    @app.post("/v1/tasks/{task_id}/cancel", status_code=202)
    def cancel(task_id: str, identity: Annotated[Identity, Depends(task_identity)],
               operation: Annotated[str, Depends(idempotency_key)], response: Response,
               expected_version: Annotated[int, Depends(version)]):
        task = store.cancel_task(identity.tenant_id, identity.subject_id, task_id, operation,
                                 expected_version, allowed_projects=identity.project_ids)
        if task.get("state") in {"cancelled", "succeeded", "failed", "timed_out"}:
            response.status_code = 200
        return task

    @app.get("/v1/context-snapshots/{snapshot_id}")
    def get_snapshot(snapshot_id: str, identity: Annotated[Identity, Depends(task_identity)]):
        return store.get_snapshot(identity.tenant_id, identity.subject_id, snapshot_id,
                                  allowed_projects=identity.project_ids)

    @app.get("/v1/projects/{project_id}/monitoring")
    def project_monitoring(project_id: str, identity: Annotated[Identity, Depends(project_identity)],
                           hours: Annotated[int, Query(ge=1, le=168)] = 24,
                           limit: Annotated[int, Query(ge=1, le=200)] = 50):
        overview = store.get_monitoring(identity.tenant_id, identity.subject_id, project_id,
                                        allowed_projects=identity.project_ids,
                                        include_all_subjects=project_id in identity.monitor_project_ids,
                                        hours=hours, limit=limit)
        return {**overview, "active_release": {"version_id": agent_version_id,
                                              "capabilities": resources}}

    @app.get("/v1/projects/{project_id}/resources")
    def project_resources(project_id: str, identity: Annotated[Identity, Depends(project_identity)]):
        store.require_project_access(identity.tenant_id, identity.subject_id, project_id,
                                     allowed_projects=identity.project_ids)
        return {"project_id": project_id, "agent_version_id": agent_version_id,
                "counts": resource_counts, "resources": resources}

    def run_browser_options(project_id: str, identity: Annotated[Identity, Depends(project_identity)],
                            hours: Annotated[int, Query(ge=1, le=168)] = 24,
                            limit: Annotated[int, Query(ge=1, le=200)] = 50,
                            cursor: PageCursor = None):
        return {"allowed_projects": identity.project_ids,
                "include_all_subjects": project_id in identity.monitor_project_ids,
                "hours": hours, "limit": limit, "cursor": cursor}

    @app.get("/v1/projects/{project_id}/agents")
    def observed_agents(project_id: str, identity: Annotated[Identity, Depends(project_identity)],
                        options: Annotated[dict, Depends(run_browser_options)]):
        return store.list_observed_agents(identity.tenant_id, identity.subject_id, project_id, **options)

    @app.get("/v1/projects/{project_id}/agents/{agent_version_id}/runs")
    def agent_runs(project_id: str, agent_version_id: Annotated[str, Path(max_length=128)],
                   identity: Annotated[Identity, Depends(project_identity)],
                   options: Annotated[dict, Depends(run_browser_options)]):
        return store.get_agent_runs(identity.tenant_id, identity.subject_id, project_id, agent_version_id, **options)

    @app.get("/v1/tasks/{task_id}/timeline")
    def task_timeline(task_id: str, identity: Annotated[Identity, Depends(verified_identity)]):
        return store.get_timeline(identity.tenant_id, identity.subject_id, task_id,
                                  allowed_projects=identity.project_ids,
                                  monitor_project_ids=identity.monitor_project_ids)

    @app.get("/v1/tasks/{task_id}/runs/{attempt}/trace")
    def run_trace(task_id: str, attempt: Annotated[int, Path(ge=1, le=2**64 - 1)],
                  identity: Annotated[Identity, Depends(verified_identity)], before: PageCursor = None):
        return store.get_run_trace(identity.tenant_id, identity.subject_id, task_id, attempt,
                                   allowed_projects=identity.project_ids,
                                   monitor_project_ids=identity.monitor_project_ids, before=before)

    return app
