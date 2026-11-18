"""Release-pinned local capabilities for the read-only support profile."""

from dataclasses import dataclass
from hashlib import sha256
import json
import re

from agentos.gateways.tool.evidence import MAX_HITS
from agentos.gateways.tool.registry import resolve


_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}")
_COMMON = {"kind", "name", "version", "owner", "source", "license"}


def _text(value, max_bytes):
    if not isinstance(value, str) or not value.strip() or len(value.encode("utf-8")) > max_bytes:
        raise ValueError("Expected nonempty UTF-8 capability text within its byte limit")
    return value


def _id(value):
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise ValueError("Invalid capability name or version")
    return value


def _digest(record):
    return sha256(json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                             allow_nan=False).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Capability:
    kind: str
    name: str
    version: str
    owner: str
    source: str
    license: str
    digest: str
    implementation: str = ""
    implementation_digest: str = ""
    limit: int = 0
    text: str = ""
    requires: tuple["Capability", ...] = ()

    def metadata(self):
        return {"kind": self.kind, "name": self.name, "version": self.version, "digest": self.digest}

    def manifest(self):
        record = {**self.metadata(), "owner": self.owner, "source": self.source, "license": self.license}
        if self.kind == "tool":
            return {**record, "implementation": self.implementation, "limit": self.limit,
                    "implementation_digest": self.implementation_digest, "effect": "local_readonly"}
        return {**record, "requires": [dep.metadata() for dep in self.requires],
                "text_sha256": sha256(self.text.encode("utf-8")).hexdigest(),
                "trust": "profile_guidance"}


def _make(raw, tools):
    kind = raw["kind"]
    extras = {"implementation", "limit"} if kind == "tool" else {"text", "requires"}
    if raw.keys() != _COMMON | extras:
        raise ValueError("Missing or unknown capability field")
    common = dict(kind=kind, name=_id(raw["name"]), version=_id(raw["version"]),
                  owner=_text(raw["owner"], 256), source=_text(raw["source"], 512),
                  license=_text(raw["license"], 256))
    if kind == "tool":
        if type(raw["limit"]) is not int or not 1 <= raw["limit"] <= MAX_HITS:
            raise ValueError("Unsupported tool result limit")
        implementation = raw["implementation"]
        impl_digest = resolve(implementation).digest
        digest = _digest({**raw, "implementation_digest": impl_digest})
        return Capability(**common, digest=digest, implementation=implementation,
                          implementation_digest=impl_digest, limit=raw["limit"])
    if not isinstance(raw["requires"], list):
        raise ValueError("Skill requires must be a list of pinned tools")
    dependencies = []
    for ref in raw["requires"]:
        if not isinstance(ref, dict) or ref.keys() != {"name", "version"}:
            raise ValueError("Invalid skill dependency")
        tool = tools.get((_id(ref["name"]), _id(ref["version"])))
        if tool is None or tool in dependencies:
            raise ValueError("Skill requires a unique tool from the same release")
        dependencies.append(tool)
    content = _text(raw["text"], 65_536)
    digest = _digest({**raw, "requires": [dep.metadata() for dep in dependencies]})
    return Capability(**common, digest=digest, text=content, requires=tuple(dependencies))


def parse_capabilities(records) -> tuple[Capability, ...]:
    """Resolve dependencies before workers start. The M1 profile has one evidence retrieval step."""
    if not isinstance(records, list):
        raise ValueError("capabilities must be an array")
    if any(not isinstance(r, dict) or r.get("kind") not in ("tool", "skill") for r in records):
        raise ValueError("Unsupported capability kind")
    tools = {}
    for raw in records:
        if raw["kind"] == "tool":
            if tools:
                raise ValueError("The read-only support profile supports one retrieval tool")
            tool = _make(raw, tools)
            tools[(tool.name, tool.version)] = tool
    capabilities = list(tools.values()) + [_make(r, tools) for r in records if r["kind"] == "skill"]
    if len({c.name for c in capabilities}) != len(capabilities):
        raise ValueError("Duplicate capability name in release")
    return tuple(capabilities)
