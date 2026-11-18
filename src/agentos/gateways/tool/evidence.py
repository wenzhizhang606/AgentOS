"""Execute a registered local evidence adapter over a single authorized read."""

import json
from sys import float_info

from agentos.contracts.errors import PlatformError
from agentos.gateways.tool.registry import resolve
from agentos.knowledge.retrieval import RankedSource


MAX_HITS = 10
MAX_RESULT_BYTES = 16_384


def _coordinates(item, code):
    if (not isinstance(item, dict) or not isinstance(item.get("source_id"), str)
            or not item["source_id"] or type(item.get("version")) is not int or item["version"] < 1
            or not isinstance(item.get("location"), str) or not item["location"]):
        raise PlatformError(code, "Invalid evidence coordinates")
    return item["source_id"], item["version"], item["location"]


def execute_evidence(implementation: str, args: dict, candidates, *, max_hits: int,
                     expected_digest: str) -> list[RankedSource]:
    try:
        adapter = resolve(implementation)
    except ValueError as exc:
        raise PlatformError("TOOL_SCHEMA_CHANGED", "Pinned evidence adapter is unavailable") from exc
    if adapter.digest != expected_digest:
        raise PlatformError("TOOL_SCHEMA_CHANGED", "Evidence adapter differs from pinned release")
    if not isinstance(args, dict) or args.keys() != {"query", "limit"}:
        raise PlatformError("INVALID_ARGUMENT", "Expected query and limit")
    query, limit = args["query"], args["limit"]
    if (not isinstance(query, str) or not query.strip() or len(query) > 16_000
            or type(limit) is not int or type(max_hits) is not int or not 1 <= limit <= max_hits <= MAX_HITS):
        raise PlatformError("INVALID_ARGUMENT", "Invalid evidence search arguments")
    sources = {}
    rows = []
    for source in candidates:
        key = _coordinates(source, "CONTEXT_STALE")
        if not isinstance(source.get("text"), str) or not isinstance(source.get("title", ""), str):
            raise PlatformError("CONTEXT_STALE", "Invalid source content")
        if key in sources:
            if sources[key] != source:
                raise PlatformError("CONTEXT_STALE", "Conflicting copies of a source revision")
            continue
        sources[key] = source
        # Pass private, flat copies so an adapter cannot mutate the authoritative evidence objects.
        rows.append({"source_id": key[0], "version": key[1], "location": key[2],
                     "text": source["text"], "title": source.get("title", "")})
    hits = adapter.run(query, tuple(rows), limit)
    if not isinstance(hits, list) or len(hits) > limit:
        raise PlatformError("TOOL_SCHEMA_CHANGED", "Invalid evidence search result")
    ranked = []
    seen = set()
    for hit in hits:
        key = _coordinates(hit, "TOOL_SCHEMA_CHANGED")
        score = hit.get("score")
        if (hit.keys() != {"source_id", "version", "location", "score"}
                or type(score) not in (int, float) or not -float_info.max <= score <= float_info.max):
            raise PlatformError("TOOL_SCHEMA_CHANGED", "Invalid evidence hit")
        if key not in sources or key in seen:
            raise PlatformError("TOOL_SCHEMA_CHANGED", "Unknown or duplicate evidence coordinates")
        seen.add(key)
        ranked.append(RankedSource(sources[key], score))
    if len(json.dumps(hits, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) > MAX_RESULT_BYTES:
        raise PlatformError("OUTPUT_LIMIT_EXCEEDED", "Evidence result exceeds its byte limit")
    return ranked
