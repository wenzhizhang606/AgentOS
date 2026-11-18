"""A one-shot HTTPS adapter for the text-only Chat Completions protocol."""

from dataclasses import dataclass
from hashlib import sha256
from http.client import HTTPException
import json
import math
import re
import time
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import build_opener, HTTPRedirectHandler, Request

from agentos.context.service import PreparedContext
from agentos.contracts.errors import PlatformError


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class ModelNotSent(PlatformError):
    """Rejected at the gateway before entering the HTTP transport."""


class ModelOutputError(PlatformError):
    """The provider reported billable usage but the answer cannot be delivered."""

    def __init__(self, code, message, input_tokens, output_tokens, provider_call_id):
        super().__init__(code, message)
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.provider_call_id = provider_call_id


@dataclass(frozen=True)
class ModelAnswer:
    answer: str
    citations: list[dict]
    input_tokens: int
    output_tokens: int
    provider_call_id: str


def _parse_response(data: bytes, prepared: PreparedContext) -> ModelAnswer:
    # Status codes alone do not establish a zero charge. Missing usage is pending.
    try:
        reply = json.loads(data)
        call_id = reply["id"]
        prompt = reply["usage"]["prompt_tokens"]
        completion = reply["usage"]["completion_tokens"]
        if (type(call_id) is not str or not call_id.strip() or len(call_id) > 128
                or any(type(n) is not int or not 0 <= n <= 2**63 - 1
                       for n in (prompt, completion))):
            raise ValueError
        call_id.encode("utf-8")
    except (TypeError, ValueError, KeyError, RecursionError) as exc:
        raise PlatformError("EXTERNAL_OUTCOME_UNKNOWN", "Provider usage receipt unavailable") from exc

    try:
        choices = reply["choices"]
        if not isinstance(choices, list) or len(choices) != 1:
            raise ValueError
        choice = choices[0]
        message = choice["message"]
        if (choice["finish_reason"] != "stop" or message["role"] != "assistant"
                or message.get("tool_calls") or message.get("function_call") or message.get("refusal")):
            raise ValueError
        answer = message["content"].strip()
        answer.encode("utf-8")  # Lone surrogates cannot enter a MySQL utf8mb4 result.
        if not answer:
            raise ValueError
        if answer == "INSUFFICIENT_EVIDENCE":
            raise ModelOutputError("MISSING_EVIDENCE", "Model reported insufficient evidence",
                                   prompt, completion, call_id)
        labels = list(dict.fromkeys(re.findall(r"\[(E[^\[\]\s]*)\]", answer)))
        refs = {r["label"]: r for r in prepared.source_refs}
        if not labels or any(label not in refs for label in labels):
            raise ValueError
        citations = [refs[label] for label in labels]
    except (AttributeError, IndexError, KeyError, TypeError, ValueError) as exc:
        raise ModelOutputError("INVALID_MODEL_OUTPUT", "Missing or invalid cited answer",
                               prompt, completion, call_id) from exc
    return ModelAnswer(answer, citations, prompt, completion, call_id)


class ChatModel:
    def __init__(self, endpoint: str, api_key: str, timeout_seconds: float = 30):
        url = urlsplit(endpoint)
        if (url.scheme != "https" or not url.hostname or url.username is not None
                or url.password is not None or url.query or url.fragment):
            raise ValueError("A configured HTTPS model endpoint is required")
        if not api_key or any(ord(ch) < 33 or ord(ch) > 126 for ch in api_key):
            raise ValueError("Invalid model credential")
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError("Model timeout must be positive and finite")
        self.endpoint = endpoint
        self._api_key = api_key
        self.timeout_seconds = timeout_seconds
        self._transport = build_opener(_NoRedirect())

    def generate(self, prepared: PreparedContext, *, send_before: float | None = None) -> ModelAnswer:
        snapshot = prepared.snapshot
        digest = sha256(prepared.body).hexdigest()
        if (digest != snapshot["wire_digest"] or digest != snapshot["payload_digest"]
                or self.endpoint != snapshot["destination"]):
            raise ModelNotSent("CONTEXT_STALE", "Payload or destination differs from the snapshot")
        # The admission transaction authorized the manifest's source versions.
        # Check their order and coordinates against the actual serialized items.
        try:
            payload = json.loads(prepared.body)
            user_input = json.loads(payload["messages"][1]["content"])
            evidence = user_input["evidence"]
            fields = ("source_id", "version", "location", "label")
            actual = [tuple(item[key] for key in fields) for item in evidence]
            authorized = [tuple(ref[key] for key in fields) for ref in snapshot["source_refs"]]
            skills = user_input.get("skills", [])
            pinned = [ref for ref in snapshot.get("capability_refs", []) if ref["kind"] == "skill"]
            identity = ("kind", "name", "version", "digest")
            wire_refs = [{key: skill[key] for key in identity} for skill in skills]
            text_digests = {skill["name"]: sha256(skill["text"].encode("utf-8")).hexdigest()
                            for skill in skills if skill["trust"] == "profile_guidance"}
            if wire_refs != pinned or text_digests != snapshot.get("skill_text_sha256", {}):
                raise ValueError
            if (actual != authorized or not actual or payload["model"] != snapshot["model"]
                    or payload["max_tokens"] != snapshot["budget"]["reserved_output_tokens"]):
                raise ValueError
        except (ValueError, TypeError, AttributeError, KeyError, IndexError, RecursionError) as exc:
            raise ModelNotSent("CONTEXT_STALE", "Wire context or limits differ from admitted manifest") from exc
        request = Request(self.endpoint, method="POST", data=prepared.body, headers={
            "Content-Type": "application/json", "Accept": "application/json",
            "Authorization": "Bearer " + self._api_key,
        })
        max_bytes = 4 * 1024 * 1024
        try:
            if send_before is not None and time.monotonic() >= send_before:
                raise ModelNotSent("DISPATCH_EXPIRED", "Send permit expired")
            with self._transport.open(request, timeout=self.timeout_seconds) as response:
                data = response.read(max_bytes + 1)
        except (URLError, OSError, HTTPException) as exc:
            raise PlatformError("EXTERNAL_OUTCOME_UNKNOWN", "Model dispatch has no confirmed receipt") from exc
        if len(data) > max_bytes:
            raise PlatformError("EXTERNAL_OUTCOME_UNKNOWN", "Provider response exceeds supported size")
        return _parse_response(data, prepared)
