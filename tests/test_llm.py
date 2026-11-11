"""CIPW-58: the call to Mistral for the AI report, without network (the HTTP client is replaced)."""

import re

import httpx
import pytest
from pydantic import SecretStr

from src.extraction.vision import ApiError
from src.llm import LlmError, ask_json, client
from src.settings import get_settings


class FakeChat:
    def __init__(self, answer=None, error=None):
        self.answer, self.error, self.payloads = answer, error, []

    def chat(self, payload):
        self.payloads.append(payload)
        if self.error:
            raise self.error
        return self.answer


def _answer(content, finish="stop"):
    return {"model": "mistral-medium-3.5", "choices": [{"message": {"content": content}, "finish_reason": finish}],
            "usage": {"prompt_tokens": 1200, "completion_tokens": 300}}  # fmt: skip


@pytest.fixture
def settings():
    s = get_settings().model_copy(deep=True)
    s.offline, s.llm.api_key = False, SecretStr("test-key")
    s.llm.price_in_per_mtok, s.llm.price_out_per_mtok = 0.4, 2.0
    return s


def test_one_json_answer_with_its_tokens_and_cost(settings):
    chat = FakeChat(_answer('{"summary": "Firma je zdravá.", "rating": "low"}'))
    reply = ask_json("Jsi úvěrový analytik.", '{"ico": "26161516"}', settings, chat=chat)
    assert reply.data == {"summary": "Firma je zdravá.", "rating": "low"} and reply.model == "mistral-medium-3.5"
    assert (reply.usage.input_tokens, reply.usage.output_tokens) == (1200, 300)
    assert reply.usage.cost_usd == pytest.approx((1200 * 0.4 + 300 * 2.0) / 1e6)
    payload = chat.payloads[0]
    assert payload["model"] == settings.llm.model and payload["response_format"] == {"type": "json_object"}
    assert payload["temperature"] == 0 and [m["role"] for m in payload["messages"]] == ["system", "user"]


def test_an_answer_in_chunks(settings):
    chunks = [{"type": "thinking", "thinking": "..."}, {"type": "text", "text": '{"ok": true}'}]
    assert ask_json("s", "u", settings, chat=FakeChat(_answer(chunks))).data == {"ok": True}


@pytest.mark.parametrize(
    ("chat", "message"),
    [
        (FakeChat(_answer("Firma je zdravá.")), "not a JSON object"),
        (FakeChat(_answer("[1, 2]")), "not an object"),
        (FakeChat(_answer('{"summary": "Firma', finish="length")), "cut off"),
        (FakeChat({"error": "?"}), "not a JSON object"),
        (FakeChat(error=ApiError(401, "Unauthorized")), "HTTP 401"),
        (FakeChat(error=httpx.ConnectError("refused")), "not reachable (ConnectError)"),
    ],
)
def test_errors_are_one_kind(settings, chat, message):
    with pytest.raises(LlmError, match=re.escape(message)):
        ask_json("s", "u", settings, chat=chat)


def test_no_call_without_a_model(settings):
    settings.llm.api_key = None
    with pytest.raises(LlmError, match="no AI model connected"):
        ask_json("s", "u", settings, chat=FakeChat(_answer("{}")))
    settings.llm.api_key, settings.offline = SecretStr("test-key"), True
    assert not settings.llm_enabled


def test_a_server_of_ones_own_needs_no_key(settings):
    settings.llm.api_key, settings.llm.url = None, "http://llm.internal:8000/v1/"
    assert settings.llm_enabled  # vLLM or similar inside Cofidis' network
    http = client(settings)
    assert http.url == "http://llm.internal:8000/v1/chat/completions" and http.timeout == settings.llm.timeout
