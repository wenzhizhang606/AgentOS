from dataclasses import replace
from io import BytesIO
import json
import time
from unittest.mock import Mock
from urllib.error import URLError

import pytest

from agentos.context.service import prepare_context
from agentos.contracts.errors import PlatformError
from agentos.gateways.model.client import ChatModel, ModelNotSent, ModelOutputError, _NoRedirect


ENDPOINT = "https://model.example/v1/chat/completions"


@pytest.fixture
def prepared():
    return prepare_context(task_id="case", question="How long is the warranty?",
                           candidates=[dict(source_id="policy", version=1, location="p.1",
                                            title="Warranty", text="Warranty lasts one year.")],
                           model="pinned", window_tokens=4096, output_tokens=64, destination=ENDPOINT)


@pytest.fixture
def model():
    client = ChatModel(ENDPOINT, "test-key")
    client._transport = Mock()
    return client


def reply(answer, **overrides):
    payload = {"id": "receipt-01", "usage": {"prompt_tokens": 85, "completion_tokens": 10},
               "choices": [{"finish_reason": "stop", "message": {"role": "assistant",
                                                                  "content": answer}}]}
    return BytesIO(json.dumps(payload | overrides).encode())


def test_ac001_exact_wire_payload_and_revision_bound_citations(model, prepared):
    model._transport.open.return_value = reply("One year [E1]. Consult [E1].")
    result = model.generate(prepared)
    request = model._transport.open.call_args.args[0]
    assert request.data == prepared.body
    assert request.full_url == ENDPOINT
    assert request.headers["Authorization"] == "Bearer test-key"
    assert b"test-key" not in prepared.manifest and b"test-key" not in request.data
    assert result.citations == prepared.source_refs
    assert (result.input_tokens, result.output_tokens, result.provider_call_id) == (85, 10, "receipt-01")


@pytest.mark.parametrize("answer", ["[E2] fabricated", "Uncited.", ""])
def test_ac002_citation_rejection_preserves_billable_usage(model, prepared, answer):
    model._transport.open.return_value = reply(answer)
    with pytest.raises(ModelOutputError) as failed:
        model.generate(prepared)
    assert failed.value.code == "INVALID_MODEL_OUTPUT"
    assert failed.value.input_tokens == 85 and failed.value.output_tokens == 10


def test_ac002_model_evidence_gap_preserves_billable_usage(model, prepared):
    model._transport.open.return_value = reply("INSUFFICIENT_EVIDENCE")
    with pytest.raises(ModelOutputError) as failed:
        model.generate(prepared)
    assert failed.value.code == "MISSING_EVIDENCE"
    assert failed.value.provider_call_id == "receipt-01"


@pytest.mark.parametrize("finish, extra", [("length", {}), ("stop", {"tool_calls": [{"id": "t"}]})])
def test_ac002_incomplete_or_tool_response_is_not_a_final_answer(model, prepared, finish, extra):
    message = {"role": "assistant", "content": "One year [E1].", **extra}
    model._transport.open.return_value = reply("", choices=[{"finish_reason": finish, "message": message}])
    with pytest.raises(ModelOutputError) as failed:
        model.generate(prepared)
    assert failed.value.code == "INVALID_MODEL_OUTPUT"
    assert failed.value.output_tokens == 10


def test_ac001_pre_send_rejects_modified_semantics_or_destination(model, prepared):
    with pytest.raises(ModelNotSent) as failed:
        model.generate(replace(prepared, body=b"{}"))
    assert failed.value.code == "CONTEXT_STALE"
    model.endpoint = "https://other.example/v1/chat/completions"
    with pytest.raises(ModelNotSent):
        model.generate(prepared)
    model._transport.open.assert_not_called()


def test_ac012_expired_permit_keeps_request_unsent(model, prepared):
    with pytest.raises(ModelNotSent) as failed:
        model.generate(prepared, send_before=time.monotonic() - 1)
    assert failed.value.code == "DISPATCH_EXPIRED"
    model._transport.open.assert_not_called()


def test_ac001_manifest_revision_must_match_serialized_evidence(model, prepared):
    metadata = prepared.snapshot
    metadata["source_refs"][0]["version"] += 1
    with pytest.raises(ModelNotSent):
        model.generate(replace(prepared, manifest=json.dumps(metadata).encode()))
    model._transport.open.assert_not_called()


def test_ac009_unencodable_receipt_id_remains_pending(model, prepared):
    model._transport.open.return_value = reply("One year [E1].", id="bad\ud800")
    with pytest.raises(PlatformError) as failed:
        model.generate(prepared)
    assert failed.value.code == "EXTERNAL_OUTCOME_UNKNOWN"


def test_ac009_lost_response_cannot_trigger_model_retry(model, prepared):
    model._transport.open.side_effect = URLError("connection lost")
    with pytest.raises(PlatformError) as failed:
        model.generate(prepared)
    assert failed.value.code == "EXTERNAL_OUTCOME_UNKNOWN"
    model._transport.open.assert_called_once()


@pytest.mark.parametrize("usage", [None, {"prompt_tokens": -1, "completion_tokens": 1},
                                   {"prompt_tokens": 10, "completion_tokens": True}])
def test_ac009_missing_or_invalid_receipt_remains_pending(model, prepared, usage):
    model._transport.open.return_value = reply("one year [E1]", usage=usage)
    with pytest.raises(PlatformError) as failed:
        model.generate(prepared)
    assert failed.value.code == "EXTERNAL_OUTCOME_UNKNOWN"


def test_ac012_gateway_rejects_redirect_and_plaintext_destination():
    assert _NoRedirect().redirect_request(None, None, 307, "", {}, "https://elsewhere.example") is None
    with pytest.raises(ValueError):
        ChatModel("http://model.example/v1/chat/completions", "secret")
