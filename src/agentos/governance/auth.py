"""Authentication for provisioned M1 service credentials."""

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import re

from agentos.contracts.errors import PlatformError


@dataclass(frozen=True)
class Identity:
    tenant_id: str
    subject_id: str
    project_ids: tuple[str, ...]
    expires_at: datetime
    monitor_project_ids: tuple[str, ...] = ()
    monitor_only: bool = False


class Authenticator:
    def __init__(self, credentials: dict[str, Identity]):
        self._credentials = credentials.copy()

    @classmethod
    def from_file(cls, path: str | Path) -> "Authenticator":
        """Load provisioned token hashes. Configuration errors prevent startup."""
        config = json.loads(Path(path).read_text(encoding="utf-8"))
        records = config["credentials"]
        observers = config.get("monitor_credentials", [])
        if not isinstance(records, list) or not isinstance(observers, list):
            raise ValueError("Credential collections must be lists")
        if not records and not observers:
            raise ValueError("At least one credential is required")
        credentials = {}
        observer_digests = {record["token_sha256"] for record in observers}
        for record in records + observers:
            digest = record["token_sha256"]
            if not re.fullmatch(r"[0-9a-f]{64}", digest) or digest in credentials:
                raise ValueError("Invalid or duplicate credential hash")
            projects = record["project_ids"]
            if not isinstance(projects, list) or not projects:
                raise ValueError("A credential requires at least one project")
            identifiers = [record["tenant_id"], record["subject_id"], *projects]
            if any(not isinstance(item, str) or not item.strip() or len(item) > 128 for item in identifiers):
                raise ValueError("A credential has an empty or invalid scope")
            for item in identifiers:
                item.encode("utf-8")
            monitoring = record.get("monitor_project_ids", [])
            if not isinstance(monitoring, list) or any(
                    not isinstance(project, str) or project not in projects for project in monitoring):
                raise ValueError("Monitoring projects must be within the credential's project scope")
            expires = record["expires_at"]
            if not re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,6})?Z", expires):
                raise ValueError("Credential expiry must be a UTC timestamp ending in Z")
            credentials[digest] = Identity(
                record["tenant_id"], record["subject_id"], tuple(projects),
                datetime.fromisoformat(expires[:-1] + "+00:00"),
                tuple(monitoring), digest in observer_digests,
            )
        return cls(credentials)

    def authenticate(self, authorization: str | None) -> Identity:
        scheme, _, token = (authorization or "").partition(" ")
        if scheme.lower() == "bearer" and token and not any(ch.isspace() for ch in token):
            digest = sha256(token.encode("utf-8")).hexdigest()
            identity = self._credentials.get(digest)
            if identity is not None and identity.expires_at > datetime.now(timezone.utc):
                return identity
        raise PlatformError("UNAUTHENTICATED", "A valid bearer credential is required")
