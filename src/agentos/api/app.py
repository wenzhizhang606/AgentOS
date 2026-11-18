"""HTTP ingress for the pinned, single-worker read-only support profile."""

from datetime import datetime
import re
from typing import Annotated, Literal, Protocol
from uuid import uuid4

from fastapi import Depends, FastAPI, Header, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field, field_validator
from starlette.exceptions import HTTPException

from agentos.contracts.errors import PlatformError
from agentos.governance.auth import Authenticator, Identity


class TaskStore(Protocol):
    def create_task(self, tenant_id, subject_id, idempotency_key, request: dict,
                    *, replay_only: bool = False) -> dict: ...
    def get_task(self, tenant_id, subject_id, task_id, *, allowed_projects) -> dict: ...
    def cancel_task(self, tenant_id, subject_id, task_id, idempotency_key,
                    expected_version, *, allowed_projects) -> dict: ...
    def get_snapshot(self, tenant_id, subject_id, snapshot_id, *, allowed_projects) -> dict: ...


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


def create_app(store: TaskStore, authenticator: Authenticator, agent_version_id: str) -> FastAPI:
    app = FastAPI(title="AgentOS M1", version="1")

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

    def authenticate(credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]) -> Identity:
        header = f"{credentials.scheme} {credentials.credentials}" if credentials else None
        return authenticator.authenticate(header)

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

    @app.post("/v1/tasks", status_code=202)
    def submit(body: CreateTask, identity: Annotated[Identity, Depends(authenticate)],
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
    def get_task(task_id: str, identity: Annotated[Identity, Depends(authenticate)], response: Response):
        task = store.get_task(identity.tenant_id, identity.subject_id, task_id,
                              allowed_projects=identity.project_ids)
        response.headers["ETag"] = f'"{task["version"]}"'
        return task

    @app.post("/v1/tasks/{task_id}/cancel", status_code=202)
    def cancel(task_id: str, identity: Annotated[Identity, Depends(authenticate)],
               operation: Annotated[str, Depends(idempotency_key)], response: Response,
               expected_version: Annotated[int, Depends(version)]):
        task = store.cancel_task(identity.tenant_id, identity.subject_id, task_id, operation,
                                 expected_version, allowed_projects=identity.project_ids)
        if task.get("state") in {"cancelled", "succeeded", "failed", "timed_out"}:
            response.status_code = 200
        return task

    @app.get("/v1/context-snapshots/{snapshot_id}")
    def get_snapshot(snapshot_id: str, identity: Annotated[Identity, Depends(authenticate)]):
        return store.get_snapshot(identity.tenant_id, identity.subject_id, snapshot_id,
                                  allowed_projects=identity.project_ids)

    return app
