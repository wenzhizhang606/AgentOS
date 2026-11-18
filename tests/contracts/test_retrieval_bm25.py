"""Evidence ranking and paired scoring contracts, AC-002 and AC-014."""

import json
from pathlib import Path

import pytest

from agentos.context.service import prepare_context
from agentos.contracts.errors import PlatformError
from agentos.control.release import Release
from agentos.gateways.tool.evidence import execute_evidence
from agentos.gateways.tool.registry import resolve
from benchmarks.scifact import measure


_ADAPTER = "bm25_search/1"


def source(source_id, text, title=""):
    return {"source_id": source_id, "title": title, "text": text, "version": 1,
            "location": "abstract", "tenant_id": "test", "project_id": "support"}


def search(question, corpus, limit=10):
    return execute_evidence(_ADAPTER, {"query": question, "limit": limit}, corpus, max_hits=10,
                            expected_digest=resolve(_ADAPTER).digest)


def test_ac002_bm25_penalizes_length_and_uses_term_frequency():
    corpus = [source("a", "refund " + "other " * 20), source("b", "refund refund policy"),
              source("c", "refund policy")]
    hits = search("refund", corpus)
    assert [hit.source["source_id"] for hit in hits] == ["b", "c", "a"]
    assert hits[0].source is corpus[1]


def test_ac002_bm25_han_title_matches_tie_breaks_and_excludes_empty_body():
    corpus = [source("b", "等待期限", "退货"), source("a", "等待期限", "退货"), source("c", "", "退货")]
    assert [hit.source["source_id"] for hit in search("退货", corpus, limit=1)] == ["a"]
    assert [hit.source["source_id"] for hit in search("退", corpus)] == ["a", "b"]
    assert search("发票", corpus) == []


def test_ac002_bm25_refuses_conflicting_evidence_versions():
    with pytest.raises(PlatformError) as err:
        search("refund", [source("same", "refund"), source("same", "credit only")])
    assert err.value.code == "CONTEXT_STALE"


def test_ac002_bm25_pins_adapter_in_runtime_snapshot():
    example = Path(__file__).parents[2] / "examples/m1/release-capabilities.json"
    config = json.loads(example.read_text(encoding="utf-8"))
    baseline = Release(**config)
    config["capabilities"][0]["implementation"] = _ADAPTER
    candidate = Release(**config)
    assert candidate.version_id != baseline.version_id
    context = prepare_context(task_id="case", question="退货", candidates=[source("policy", "退货政策")],
                              model=candidate.model, window_tokens=candidate.window_tokens,
                              output_tokens=candidate.max_output_tokens, capabilities=candidate.capabilities,
                              release_id=candidate.version_id, rag=candidate.rag,
                              observe=lambda metadata, operation: operation())
    assert context.snapshot["selection_manifest"]["policy"] == _ADAPTER
    assert context.snapshot["capability_refs"] == candidate.catalog_metadata()
    assert context.source_refs[0]["source_id"] == "policy"


def test_ac014_scifact_scoring_counts_retrieval_misses():
    assert measure([], {"relevant": 1}) == (0, 0)
    assert measure(["relevant", "other"], {"relevant": 1}) == (1, 1)
    dcg, recall = measure(["other", "relevant"], {"relevant": 1})
    assert dcg == pytest.approx(0.63092975)
    assert recall == 1
