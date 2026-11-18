"""Approved, in-process evidence adapters. Deployment JSON selects a fixed ID."""

from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache
from hashlib import sha256
from importlib.resources import files
import json

from agentos.gateways.tool.lexical import search


@lru_cache
def _digest_sources(paths: tuple[str, ...]) -> str:
    root = files("agentos")
    hashes = {p: sha256(root.joinpath(*p.split("/")).read_text(
        encoding="utf-8").encode("utf-8")).hexdigest() for p in paths}
    return sha256(json.dumps(hashes, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


@dataclass(frozen=True)
class EvidenceAdapter:
    run: Callable[[str, tuple[dict, ...], int], list[dict]]
    source_files: tuple[str, ...]

    @property
    def digest(self):
        source_digest = _digest_sources(("gateways/tool/evidence.py", *self.source_files))
        entrypoint = f"{self.run.__module__}.{self.run.__qualname__}"
        return sha256(f"{entrypoint}\n{source_digest}".encode("utf-8")).hexdigest()


EVIDENCE_TOOLS = {
    "lexical_search/1": EvidenceAdapter(search, ("gateways/tool/lexical.py", "knowledge/retrieval.py")),
}


def resolve(implementation: str) -> EvidenceAdapter:
    if not isinstance(implementation, str) or implementation not in EVIDENCE_TOOLS:
        raise ValueError("Unsupported evidence tool implementation")
    return EVIDENCE_TOOLS[implementation]
