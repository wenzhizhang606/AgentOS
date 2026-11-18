"""The read-only support profile and its immutable deployment configuration."""

from dataclasses import dataclass, fields
from functools import lru_cache
import hashlib
from importlib.resources import files
import json
from pathlib import Path
from urllib.parse import urlsplit

from agentos.control.capabilities import Capability, parse_capabilities


@lru_cache(maxsize=1)
def _components():
    # Include behavioral code shared by the API and worker in the installed package.
    root = files("agentos")
    paths = (
        "control/release.py", "api/app.py", "governance/auth.py", "runtime/managed_task.py",
        "worker/service.py", "knowledge/retrieval.py", "context/service.py",
        "gateways/model/client.py", "adapters/mysql/store.py", "contracts/errors.py",
        "control/capabilities.py", "gateways/tool/registry.py", "gateways/tool/evidence.py",
        "observability/capabilities.py",
        "adapters/mysql/monitor.py", "adapters/mysql/run_trace.py",
    )
    return tuple((name, hashlib.sha256(root.joinpath(*name.split("/")).read_text(
        encoding="utf-8").encode("utf-8")).hexdigest()) for name in paths)


@dataclass(frozen=True)
class Release:
    endpoint: str
    model: str
    window_tokens: int
    max_output_tokens: int
    safety_margin_tokens: int
    input_micros_per_million: int
    output_micros_per_million: int
    retention_mode: str
    currency: str = "CNY"
    capabilities: tuple[Capability, ...] = ()

    def __post_init__(self):
        if isinstance(self.capabilities, list):
            object.__setattr__(self, "capabilities", parse_capabilities(self.capabilities))
        elif not isinstance(self.capabilities, tuple) or any(
                not isinstance(cap, Capability) for cap in self.capabilities):
            raise ValueError("Expected a release capability array")
        if not isinstance(self.endpoint, str) or not self.endpoint.isascii() or any(
                ord(char) <= 32 or ord(char) == 127 for char in self.endpoint):
            raise ValueError("Configure an ASCII HTTPS URL with escaped path characters")
        target = urlsplit(self.endpoint)
        if (target.scheme != "https" or not target.hostname or target.username is not None
                or target.password is not None or target.fragment or target.query or target.port == 0):
            raise ValueError("Use the full HTTPS endpoint without credentials, query or fragment")
        if not isinstance(self.model, str) or not self.model.strip():
            raise ValueError("Configure a pinned model identifier")
        limits = (self.window_tokens, self.max_output_tokens, self.safety_margin_tokens,
                  self.input_micros_per_million, self.output_micros_per_million)
        if any(type(n) is not int or n < 0 for n in limits):
            raise ValueError("Token limits and prices must be nonnegative integers")
        if not 0 < self.max_output_tokens < self.window_tokens - self.safety_margin_tokens:
            raise ValueError("The model window must leave room for input")
        if self.retention_mode not in {"full", "redacted", "metadata_only"}:
            raise ValueError("Unsupported snapshot retention mode")
        if self.currency != "CNY":
            raise ValueError("The M1 price table uses CNY micro-units")
        if self.reservation_micros > 2**63 - 1:
            raise ValueError("Price exceeds supported integer range")

    @classmethod
    def load(cls, path: str):
        return cls(**json.loads(Path(path).read_text(encoding="utf-8")))

    @property
    def manifest(self) -> dict:
        config = {f.name: getattr(self, f.name) for f in fields(self) if f.name != "capabilities"}
        if self.capabilities:
            config["capabilities"] = [cap.manifest() for cap in self.capabilities]
        retrieval = next((cap.implementation for cap in self.capabilities if cap.kind == "tool"), "lexical/1")
        return {"profile": "readonly-support/1", "retrieval": retrieval,
                "context": "evidence/1", "gateway": "chat-completions/1",
                "components_sha256": dict(_components()), **config}

    def catalog_metadata(self) -> list[dict]:
        return [cap.metadata() for cap in self.capabilities]

    @property
    def version_id(self) -> str:
        encoded = json.dumps(self.manifest, sort_keys=True, separators=(",", ":"),
                             ensure_ascii=False, allow_nan=False).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def cost(self, input_tokens: int, output_tokens: int) -> int:
        if any(type(n) is not int or n < 0 for n in (input_tokens, output_tokens)):
            raise ValueError("Invalid provider usage")
        total = (input_tokens * self.input_micros_per_million
                 + output_tokens * self.output_micros_per_million)
        return (total + 999_999) // 1_000_000

    @property
    def reservation_micros(self) -> int:
        # Reserve against the complete input window, including provider-side framing.
        return self.cost(self.window_tokens, self.max_output_tokens)
