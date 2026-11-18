from hashlib import sha256
import json

import pytest

from agentos.context.service import prepare_context
from agentos.contracts.errors import PlatformError
from agentos.knowledge.retrieval import retrieve


def document(source_id="refund", version=2, text="订单退款时限为七天。"):
    return {"source_id": source_id, "version": version, "location": "section/2",
            "title": "", "text": text, "project_id": "support", "tenant_id": "tenant-a"}


def prepare(**changes):
    kwargs = dict(task_id="task-1", question="退款时限？", candidates=[document()],
                  window_tokens=4096, output_tokens=128, model="pinned",
                  destination="https://model.example/v1/chat/completions",
                  attempt=2, authorization_revision=3, release_id="release-a")
    return prepare_context(**(kwargs | changes))


@pytest.mark.parametrize("mode", ["full", "redacted", "metadata_only"])
def test_ac001_retention_preserves_independent_digests(mode):
    prepared = prepare(retention_mode=mode)
    snapshot = prepared.snapshot
    assert snapshot["source_refs"][0]["version"] == 2
    assert snapshot["attempt"] == 2 and snapshot["authorization_revision"] == 3
    assert snapshot["wire_digest"] == snapshot["payload_digest"] == sha256(prepared.body).hexdigest()
    assert document()["text"] not in prepared.manifest.decode()
    if mode == "metadata_only":
        assert prepared.retained_payload is snapshot["stored_payload_digest"] is None
    else:
        assert sha256(prepared.retained_payload).hexdigest() == snapshot["stored_payload_digest"]
        if mode == "full":
            assert prepared.retained_payload == prepared.body
        else:
            assert prepared.retained_payload != prepared.body
            assert {m["content"] for m in json.loads(prepared.retained_payload)["messages"]} == {"[redacted]"}


def test_ac001_manifest_changes_cannot_mutate_prepared_request():
    prepared = prepare()
    prepared.source_refs.clear()
    prepared.snapshot["budget"].clear()
    assert prepared.source_refs[0]["location"] == "section/2"
    request = json.loads(prepared.body)
    evidence = json.loads(request["messages"][1]["content"])["evidence"]
    assert evidence[0]["label"] == prepared.source_refs[0]["label"]
    assert evidence[0]["text"] == document()["text"]
    assert prepared.body == prepare().body


def test_ac002_window_exact_boundary_counts_wrapping_output_and_margin():
    prepared = prepare()
    exact = prepared.input_bound + 128 + 64
    assert prepare(window_tokens=exact).body == prepared.body
    with pytest.raises(PlatformError) as failed:
        prepare(window_tokens=exact - 1)
    assert failed.value.code == "CONTEXT_BUDGET_EXCEEDED"


def test_ac002_preserve_required_goal_when_window_too_small():
    with pytest.raises(PlatformError) as failed:
        prepare(question="退款" * 4000)
    assert failed.value.code == "CONTEXT_BUDGET_EXCEEDED"


def test_ac002_no_matching_evidence():
    with pytest.raises(PlatformError) as failed:
        prepare(question="What is the exchange rate?")
    assert failed.value.code == "MISSING_EVIDENCE"


def test_ac002_skip_oversized_source_and_keep_later_smaller_candidate():
    oversized = document(source_id="a", text="退款时限" * 1024)
    prepared = prepare(candidates=[oversized, document()])
    assert [ref["source_id"] for ref in prepared.source_refs] == ["refund"]
    assert prepared.snapshot["selection_manifest"]["unfitted_ranked_candidates"] == 1
    assert oversized["text"] not in prepared.body.decode()


def test_ac002_duplicate_removal_preserves_conflicting_revisions():
    v1 = document(version=1, text="退款时限为十天")
    v2 = document(version=2)
    assert [s.source["version"] for s in retrieve("退款时限", [v2, v1, v1])] == [1, 2]
    with pytest.raises(PlatformError) as failed:
        retrieve("退款时限", [v1, {**v1, "text": "corrupted revision"}])
    assert failed.value.code == "CONTEXT_STALE"


def test_ac001_reject_source_missing_location():
    with pytest.raises(PlatformError) as failed:
        prepare(candidates=[{**document(), "location": ""}])
    assert failed.value.code == "CONTEXT_STALE"


def test_ac002_single_character_query_finds_matching_han_span():
    assert retrieve("税", [document(text="增值税专用发票")])[0].source["source_id"] == "refund"
