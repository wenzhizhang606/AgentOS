"""AC-002/009/010. Model and RAG configuration is shared by ingress and runtime."""

from dataclasses import replace
import json
from pathlib import Path

import pytest

from agentos.context.service import prepare_context
from agentos.contracts.errors import PlatformError
from agentos.control.release import Release
from agentos.knowledge.rag import RagSettings


EXAMPLE = Path(__file__).resolve().parents[2] / "examples/m1/release-capabilities.json"


def test_ac010_release_env_precedence_fingerprint_and_secrets(tmp_path):
    config = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    config["rag"] = {"top_k": 4, "min_score": 0.125}
    path = tmp_path / "release.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    old = Release.load(path)
    env = {
        "AGENTOS_MODEL_NAME": "pinned-model", "AGENTOS_MODEL_API_URL": "https://provider.example/v1/chat/completions",
        "AGENTOS_MODEL_WINDOW_TOKENS": "8192", "AGENTOS_MODEL_MAX_OUTPUT_TOKENS": "300",
        "AGENTOS_MODEL_SAFETY_MARGIN_TOKENS": "123", "AGENTOS_MODEL_INPUT_MICROS_PER_MILLION": "900000",
        "AGENTOS_MODEL_OUTPUT_MICROS_PER_MILLION": "1100000", "AGENTOS_MODEL_API_KEY": "never-in-manifest",
        "AGENTOS_RAG_TOP_K": "2", "AGENTOS_RAG_MIN_SCORE": "", "AGENTOS_RAG_TITLE_WEIGHT": "1.5",
        "AGENTOS_RAG_BODY_WEIGHT": "3",
    }
    release = Release.load(path, environ=env)
    assert (release.model, release.endpoint) == (env["AGENTOS_MODEL_NAME"], env["AGENTOS_MODEL_API_URL"])
    assert (release.window_tokens, release.max_output_tokens, release.safety_margin_tokens) == (8192, 300, 123)
    assert release.cost(1_000_000, 1_000_000) == 2_000_000
    assert release.manifest["rag"] == dict(top_k=2, min_score=0.125, title_weight=1.5, body_weight=3.0)
    assert release.version_id != old.version_id
    assert release.catalog_metadata() == old.catalog_metadata()
    assert "never-in-manifest" not in json.dumps(release.manifest)
    assert Release.load(path, environ={**env, "AGENTOS_MODEL_API_KEY": "rotated"}).version_id == release.version_id
    assert Release.load(path, environ={"AGENTOS_MODEL_NAME": "", "AGENTOS_RAG_TOP_K": ""}) == old


@pytest.mark.parametrize(("variable", "invalid"), [
    ("AGENTOS_RAG_TOP_K", "0"), ("AGENTOS_RAG_TOP_K", "11"), ("AGENTOS_RAG_TOP_K", "1.5"),
    ("AGENTOS_RAG_MIN_SCORE", "-1"), ("AGENTOS_RAG_MIN_SCORE", "nan"),
    ("AGENTOS_RAG_TITLE_WEIGHT", "inf"), ("AGENTOS_RAG_BODY_WEIGHT", "-3"),
    ("AGENTOS_MODEL_MAX_OUTPUT_TOKENS", "999999"), ("AGENTOS_MODEL_WINDOW_TOKENS", "many"),
    ("AGENTOS_MODEL_API_URL", "http://provider.example/api"),
])
def test_ac002_ac009_invalid_configuration_prevents_release(variable, invalid):
    with pytest.raises(ValueError):
        Release.load(EXAMPLE, environ={variable: invalid})


def test_ac002_at_least_one_lexical_field_contributes():
    with pytest.raises(ValueError):
        RagSettings(title_weight=0, body_weight=0)


@pytest.mark.parametrize("with_tool", [False, True])
def test_ac002_field_weights_change_actual_evidence_and_top_k(with_tool):
    release = Release.load(EXAMPLE)
    # One query term occurs in both candidates. With the built-in 2:1 ranking
    # the title wins; reversing the field priorities selects the other source.
    candidates = [
        {"source_id": "title", "version": 1, "location": "p", "title": "refund", "text": "contact us"},
        {"source_id": "body", "version": 1, "location": "p", "title": "policy", "text": "refund allowed"},
    ]
    def prepare(rag):
        return prepare_context(
            task_id="t", question="refund", candidates=candidates, model=release.model,
            window_tokens=release.window_tokens, output_tokens=release.max_output_tokens, rag=rag,
            capabilities=release.capabilities if with_tool else (), observe=lambda meta, action: action())

    default = prepare(RagSettings(top_k=1))
    assert [ref["source_id"] for ref in default.source_refs] == ["title"]
    assert default.snapshot["selection_manifest"]["candidate_limit"] == 1
    rerank = RagSettings(top_k=1, title_weight=1, body_weight=4, min_score=2)
    body = prepare(rerank)
    assert [ref["source_id"] for ref in body.source_refs] == ["body"]
    assert body.snapshot["selection_manifest"]["candidate_limit"] == (5 if with_tool else 10)
    assert body.snapshot["selection_manifest"]["rag"]["body_weight"] == 4
    with pytest.raises(PlatformError) as missing:
        prepare(replace(rerank, min_score=2.1))
    assert missing.value.code == "MISSING_EVIDENCE"
