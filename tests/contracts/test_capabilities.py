"""Local release capability contracts, AC-001, AC-013 and AC-023."""

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from agentos.context.service import prepare_context
from agentos.contracts.errors import PlatformError
from agentos.control.release import Release
from agentos.gateways.model.client import ChatModel, ModelNotSent
from agentos.gateways.tool.evidence import execute_evidence
from agentos.gateways.tool.registry import EVIDENCE_TOOLS, EvidenceAdapter, resolve


EXAMPLE = Path(__file__).resolve().parents[2] / "examples/m1/release-capabilities.json"


def example():
    return json.loads(EXAMPLE.read_text(encoding="utf-8"))


def doc(key, title, text):
    return {"source_id": key, "version": 1, "location": "p1", "title": title, "text": text,
            "tenant_id": "demo", "project_id": "support"}


def prepare(release, observe, **changes):
    args = {"task_id": "case", "question": "退货时限",
            "candidates": [doc("returns", "退货", "退货时限七天"), doc("shipping", "运费", "包邮")],
            "model": release.model, "window_tokens": release.window_tokens,
            "output_tokens": release.max_output_tokens, "capabilities": release.capabilities,
            "observe": observe}
    return prepare_context(**(args | changes))


def test_ac023_pinned_manifest_and_dependencies_exclude_skill_body():
    config = example()
    release = Release(**config)
    tool, skill = release.manifest["capabilities"]
    assert skill["requires"] == [release.catalog_metadata()[0]]
    assert len(tool["implementation_digest"]) == len(skill["text_sha256"]) == 64
    assert config["capabilities"][1]["text"] not in json.dumps(release.manifest, ensure_ascii=False)
    original_digest = tool["digest"]
    config["capabilities"][0]["limit"] -= 1
    changed = Release(**config)
    assert release.manifest["capabilities"][0]["digest"] == original_digest
    assert changed.catalog_metadata()[0]["digest"] != original_digest
    assert changed.catalog_metadata()[1]["digest"] != skill["digest"]
    assert release.version_id != changed.version_id


@pytest.mark.parametrize("field,value", [
    ("shell", "tool"), ("endpoint", "https://example.invalid"), ("implementation", "unregistered"),
    ("limit", True), ("limit", 11), ("owner", ""), ("name", "../path"),
])
def test_ac023_unsupported_permissions_and_implementations_rejected_at_load(field, value):
    config = example()
    config["capabilities"][0][field] = value
    with pytest.raises(ValueError):
        Release(**config)


def test_ac023_unsatisfied_skill_dependency_and_ambiguous_tool_are_rejected():
    config = example()
    tool, skill = config["capabilities"]
    skill["requires"][0]["version"] = "unreleased"
    with pytest.raises(ValueError):
        Release(**config)
    config["capabilities"] = [tool, deepcopy(tool)]
    with pytest.raises(ValueError):
        Release(**config)


def test_ac001_ac013_skill_and_tool_in_exact_request_and_metadata():
    release = Release.load(EXAMPLE)
    observed = []

    def observe(meta, call):
        observed.append(meta)
        return call()

    context = prepare(release, observe)
    assert [m["kind"] for m in observed] == ["skill", "tool"]
    assert all(m.keys() == {"kind", "name", "version", "digest"} for m in observed)
    assert context.snapshot["capability_refs"] == release.catalog_metadata()
    assert len(context.source_refs) == 1 and context.source_refs[0]["source_id"] == "returns"
    messages = json.loads(context.body)["messages"]
    skills = json.loads(messages[1]["content"])["skills"]
    assert skills[0]["text"] not in messages[0]["content"]
    assert skills[0]["text"] not in context.manifest.decode()
    assert skills[0]["trust"] == "profile_guidance" and skills[0]["digest"] == observed[0]["digest"]
    assert context.retained_payload is None


def test_ac013_capabilities_require_observer_and_refuse_on_observer_failure():
    release = Release.load(EXAMPLE)
    with pytest.raises(PlatformError) as missing:
        prepare(release, None)
    assert missing.value.code == "POLICY_UNAVAILABLE"

    def deny(meta, call):
        raise PlatformError("LEASE_LOST", "Cannot start span")

    with pytest.raises(PlatformError) as failed:
        prepare(release, deny)
    assert failed.value.code == "LEASE_LOST"
    baseline = prepare(replace(release, capabilities=()), None)
    assert "capability_refs" not in baseline.snapshot


def test_ac002_ac013_skill_budget_failure_occurs_inside_observed_load():
    config = example()
    config["capabilities"][1]["text"] = "large" * 2000
    seen = []

    def observe(meta, call):
        seen.append(meta["kind"])
        return call()

    with pytest.raises(PlatformError) as failed:
        prepare(Release(**config), observe, window_tokens=2048)
    assert failed.value.code == "CONTEXT_BUDGET_EXCEEDED" and seen == ["skill"]


def test_ac023_tool_result_is_bounded_and_contains_only_source_coordinates_and_score():
    candidates = [doc("r", "退货", "退货时限七天")]
    limits = {"max_hits": 1, "expected_digest": resolve("lexical_search/1").digest}
    hits = execute_evidence("lexical_search/1", {"query": "退货", "limit": 1}, candidates, **limits)
    assert hits[0].source == candidates[0]
    for args in ({"query": "退货", "limit": 2}, {"query": "退货", "limit": 1, "tenant_id": "other"}):
        with pytest.raises(PlatformError) as invalid:
            execute_evidence("lexical_search/1", args, candidates, **limits)
        assert invalid.value.code == "INVALID_ARGUMENT"
    candidates[0]["location"] = "x" * 20_000
    with pytest.raises(PlatformError) as too_large:
        execute_evidence("lexical_search/1", {"query": "退货", "limit": 1}, candidates, **limits)
    assert too_large.value.code == "OUTPUT_LIMIT_EXCEEDED"


def test_ac023_control_and_runtime_use_the_same_registered_adapter(monkeypatch):
    def pick_shipping(query, candidates, limit):
        candidates[1]["text"] = "forged by adapter"
        return [{"source_id": "shipping", "location": "p1", "version": 1, "score": 1}]

    monkeypatch.setitem(EVIDENCE_TOOLS, "fixture_search/1", EvidenceAdapter(pick_shipping, ()))
    config = example()
    config["capabilities"][0]["implementation"] = "fixture_search/1"
    release = Release(**config)
    assert release.manifest["retrieval"] == "fixture_search/1"
    context = prepare(release, lambda _, call: call())
    evidence = json.loads(json.loads(context.body)["messages"][1]["content"])["evidence"]
    assert [(row["source_id"], row["text"]) for row in evidence] == [("shipping", "包邮")]
    old_digest = release.capabilities[0].implementation_digest
    monkeypatch.setitem(EVIDENCE_TOOLS, "fixture_search/1", resolve("lexical_search/1"))
    assert release.manifest["capabilities"][0]["implementation_digest"] == old_digest
    with pytest.raises(PlatformError) as replaced:
        prepare(release, lambda _, call: call())
    assert replaced.value.code == "TOOL_SCHEMA_CHANGED"


@pytest.mark.parametrize("changes", [
    {"text": "new body"}, {"source_id": "other"}, {"version": 2}, {"version": True},
    {"location": "elsewhere"}, {"score": float("nan")},
])
def test_ac023_gateway_enforces_output_contract_for_every_adapter(monkeypatch, changes):
    def incorrect(query, candidates, limit):
        hit = {"source_id": "r", "location": "p1", "version": 1, "score": 1}
        return [hit | changes]

    monkeypatch.setitem(EVIDENCE_TOOLS, "invalid/1", EvidenceAdapter(incorrect, ()))
    with pytest.raises(PlatformError) as failed:
        execute_evidence("invalid/1", {"query": "退货", "limit": 1},
                         [doc("r", "退货", "七天")], max_hits=1, expected_digest=resolve("invalid/1").digest)
    assert failed.value.code == "TOOL_SCHEMA_CHANGED"


def test_ac001_gateway_checks_skill_digest_before_entering_transport():
    release = Release.load(EXAMPLE)
    context = prepare(release, lambda _, call: call(), destination=release.endpoint)
    manifest = context.snapshot
    manifest["skill_text_sha256"].clear()
    broken = replace(context, manifest=json.dumps(manifest).encode())
    model = ChatModel(release.endpoint, "fixture")
    model._transport = Mock()
    with pytest.raises(ModelNotSent) as failed:
        model.generate(broken)
    assert failed.value.code == "CONTEXT_STALE"
    model._transport.open.assert_not_called()
