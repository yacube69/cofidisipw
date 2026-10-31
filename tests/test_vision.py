"""CIPW-8: vision baseline. No test calls the API: the client is a test double."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import anthropic
import pytest

from src.evaluation import compare
from src.extraction import vision
from src.line_items import load_dictionary
from src.schema import GroundTruth
from tests.synthetic import make_digital_pdf

ROOT = Path(__file__).resolve().parents[1]
GT_PATH = ROOT / "data" / "ground_truth" / "26161516_2025.json"


def _gt() -> GroundTruth:
    return GroundTruth.model_validate_json(GT_PATH.read_text(encoding="utf-8"))


def _answer(gt: GroundTruth) -> dict:
    """What a perfect model would send back for this ground truth."""
    return {
        "unit_multiplier": gt.unit_multiplier,
        "balance_sheet": {k: v.model_dump(exclude_none=True) for k, v in gt.balance_sheet.items()},
        "income_statement": {k: v.model_dump(exclude_none=True) for k, v in gt.income_statement.items()},
        "notes": "test",
    }


class FakeMessages:
    def __init__(self, answers: list):
        self.answers = answers
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


def _response(tool_input: dict, stop_reason: str = "tool_use"):
    block = SimpleNamespace(type="tool_use", name=vision.TOOL_NAME, input=tool_input)
    return SimpleNamespace(
        content=[block], stop_reason=stop_reason, usage=SimpleNamespace(input_tokens=1200, output_tokens=300)
    )


def _client(*answers):
    return SimpleNamespace(messages=FakeMessages(list(answers)))


@pytest.fixture
def pdf(tmp_path: Path) -> Path:
    path = tmp_path / "doc.pdf"
    return make_digital_pdf(path)


def test_prompt_is_filled_from_the_line_dictionary():
    prompt = vision.build_prompt()
    assert "{{" not in prompt
    d = load_dictionary()
    for key in d.keys("balance_sheet") + d.keys("income_statement"):
        assert f"`{key}`" in prompt


def test_schema_has_the_dictionary_keys_and_period_columns():
    schema = vision.output_schema()
    d = load_dictionary()
    bs = schema["properties"]["balance_sheet"]["properties"]
    assert set(bs) == set(d.keys("balance_sheet"))
    assert set(schema["properties"]["income_statement"]["properties"]) == set(d.keys("income_statement"))
    assets = next(line for line in d.for_statement("balance_sheet") if line.side == "assets")
    liabilities = next(line for line in d.for_statement("balance_sheet") if line.side == "liabilities")
    assert set(bs[assets.key]["properties"]) == {"gross", "adjustment", "current", "prior"}
    assert set(bs[liabilities.key]["properties"]) == {"current", "prior"}
    json.dumps(schema)  # serializable


def test_render_pages_returns_png_and_rejects_missing_pages(pdf: Path):
    ((page, png),) = vision.render_pages(pdf, [1], dpi=72)
    assert page == 1 and png.startswith(b"\x89PNG")
    with pytest.raises(ValueError):
        vision.render_pages(pdf, [3])


def test_perfect_answer_scores_like_the_ground_truth(pdf: Path):
    gt = _gt()
    client = _client(_response(_answer(gt)))
    result = vision.extract_with_vision(pdf, [1, 2], client, model="test-model")
    assert result.ok and result.input_tokens == 1200 and result.output_tokens == 300
    fields = compare(gt, result.statements)
    assert fields and all(f.correct for f in fields)
    call = client.messages.calls[0]
    assert call["temperature"] == 0 and call["tool_choice"] == {"type": "tool", "name": vision.TOOL_NAME}
    images = [b for b in call["messages"][0]["content"] if b["type"] == "image"]
    assert len(images) == 2


def test_wrong_and_missing_values_are_counted():
    gt = _gt()
    answer = _answer(gt)
    first = next(iter(answer["balance_sheet"]))
    answer["balance_sheet"][first] = {"current": 1.0}  # wrong value, other periods missing
    fs = vision.to_statements(answer, gt.file, [1])
    outcomes = {f.outcome for f in compare(gt, fs)}
    assert "wrong" in outcomes and "missing" in outcomes


def test_null_line_counts_as_not_printed():
    gt = _gt()
    answer = _answer(gt)
    answer["balance_sheet"]["provisions"] = None  # Gemini sometimes writes null instead of leaving the line out
    answer["income_statement"]["other_operating_income"] = None
    fs = vision.to_statements(answer, gt.file, [1])
    assert "provisions" not in fs.balance_sheet and "other_operating_income" not in fs.income_statement


def test_unknown_key_is_a_failed_file_not_a_crash(pdf: Path):
    answer = {"unit_multiplier": 1, "balance_sheet": {"not_a_line": {"current": 1}}, "income_statement": {}}
    result = vision.extract_with_vision(pdf, [1], _client(_response(answer)), model="m")
    assert not result.ok and "not_a_line" in (result.error or "")


def test_cut_off_answer_is_an_error(pdf: Path):
    result = vision.extract_with_vision(pdf, [1], _client(_response({}, stop_reason="max_tokens")), model="m")
    assert "max_tokens" in (result.error or "")


def test_model_without_temperature_is_retried_and_flagged(pdf: Path):
    rejected = anthropic.BadRequestError.__new__(anthropic.BadRequestError)  # no HTTP library needed
    Exception.__init__(rejected, "temperature is not supported")
    client = _client(rejected, _response(_answer(_gt())))
    result = vision.extract_with_vision(pdf, [1], client, model="m")
    assert result.ok and result.temperature_used is False and result.warnings
    assert "temperature" not in client.messages.calls[1]


def test_cost_needs_the_prices_from_the_environment(monkeypatch):
    monkeypatch.delenv("LLM_PRICE_IN_PER_MTOK", raising=False)
    monkeypatch.delenv("LLM_PRICE_OUT_PER_MTOK", raising=False)
    assert vision.cost_usd(1000, 1000) is None
    monkeypatch.setenv("LLM_PRICE_IN_PER_MTOK", "3")
    monkeypatch.setenv("LLM_PRICE_OUT_PER_MTOK", "15")
    assert vision.cost_usd(1_000_000, 1_000_000) == pytest.approx(18.0)


# ---------------------------------------------------------------------------------------------------------------
# Mistral (the model Cofidis uses): plain HTTP client, so the test doubles are a fake client and a fake httpx.post
# ---------------------------------------------------------------------------------------------------------------


class FakeMistral:
    def __init__(self, *answers):
        self.answers = list(answers)
        self.payloads: list[dict] = []

    def chat(self, payload):
        self.payloads.append(payload)
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


def _mistral_response(tool_input, finish_reason="tool_calls", as_string=True):
    arguments = json.dumps(tool_input) if as_string else tool_input
    message = {"role": "assistant", "tool_calls": [{"function": {"name": vision.TOOL_NAME, "arguments": arguments}}]}
    return {
        "choices": [{"message": message, "finish_reason": finish_reason}],
        "usage": {"prompt_tokens": 1500, "completion_tokens": 400},
    }


def test_mistral_request_shape_and_perfect_answer(pdf: Path):
    gt = _gt()
    client = FakeMistral(_mistral_response(_answer(gt)))
    result = vision.extract_with_vision(pdf, [1, 2], client, provider="mistral")
    assert result.ok and result.provider == "mistral" and result.model == "mistral-medium-3.5"
    assert result.input_tokens == 1500 and result.output_tokens == 400
    assert all(f.correct for f in compare(gt, result.statements))
    payload = client.payloads[0]
    assert payload["temperature"] == 0 and payload["tool_choice"] == "any"
    images = [b for b in payload["messages"][1]["content"] if b["type"] == "image_url"]
    assert len(images) == 2 and images[0]["image_url"].startswith("data:image/png;base64,")
    schema = payload["tools"][0]["function"]["parameters"]
    first = next(iter(schema["properties"]["balance_sheet"]["properties"].values()))
    assert all(p["type"] == "number" for p in first["properties"].values())  # no ["number", "null"] unions


def test_mistral_arguments_may_already_be_an_object(pdf: Path):
    client = FakeMistral(_mistral_response(_answer(_gt()), as_string=False))
    assert vision.extract_with_vision(pdf, [1], client, provider="mistral").ok


def test_mistral_cut_off_and_missing_tool_call_are_errors(pdf: Path):
    cut = FakeMistral(_mistral_response({}, finish_reason="length"))
    assert "cut off" in (vision.extract_with_vision(pdf, [1], cut, provider="mistral").error or "")
    no_call = FakeMistral({"choices": [{"message": {"content": "hi"}, "finish_reason": "stop"}], "usage": {}})
    assert "did not call" in (vision.extract_with_vision(pdf, [1], no_call, provider="mistral").error or "")


def test_mistral_temperature_rejected_is_retried_and_flagged(pdf: Path):
    rejected = vision.MistralError(400, "temperature is not supported")
    client = FakeMistral(rejected, _mistral_response(_answer(_gt())))
    result = vision.extract_with_vision(pdf, [1], client, provider="mistral")
    assert result.ok and result.temperature_used is False and result.warnings
    assert "temperature" not in client.payloads[1]


def test_unknown_provider_is_refused(pdf: Path):
    with pytest.raises(ValueError):
        vision.extract_with_vision(pdf, [1], object(), provider="nope")


class FakeHttpResponse:
    def __init__(self, status_code, body=None, headers=None):
        self.status_code, self._body, self.headers, self.text = status_code, body or {}, headers or {}, "error text"

    def json(self):
        return self._body


def test_mistral_client_waits_and_retries_on_rate_limit(monkeypatch):
    import httpx

    responses = [FakeHttpResponse(429, headers={"retry-after": "3"}), FakeHttpResponse(200, {"ok": True})]
    monkeypatch.setattr(httpx, "post", lambda *a, **k: responses.pop(0))
    client = vision.MistralClient(api_key="test-key")
    waits: list[float] = []
    client.sleep = waits.append
    assert client.chat({"model": "m"}) == {"ok": True}
    assert waits == [3.0]


def test_daily_quota_is_not_retried(monkeypatch):
    import httpx

    calls = []

    def post(*a, **k):
        calls.append(1)
        response = FakeHttpResponse(429)
        response.text = "Quota exceeded ... Generate content requests per day limit exceeded on Free Tier"
        return response

    monkeypatch.setattr(httpx, "post", post)
    client = vision.MistralClient(api_key="test-key")
    client.sleep = lambda s: pytest.fail("must not wait")
    with pytest.raises(vision.ApiError):
        client.chat({})
    assert len(calls) == 1
    assert vision.is_daily_quota("limit exceeded per day") and not vision.is_daily_quota("too many requests")


def test_mistral_client_raises_on_other_errors_and_needs_a_key(monkeypatch):
    import httpx

    monkeypatch.setattr(httpx, "post", lambda *a, **k: FakeHttpResponse(401))
    with pytest.raises(vision.MistralError) as exc:
        vision.MistralClient(api_key="test-key").chat({})
    assert exc.value.status_code == 401
    monkeypatch.delenv("MISTRAL_API_KEY", raising=False)
    with pytest.raises(ValueError):
        vision.MistralClient()


# ---------------------------------------------------------------------------------------------------------------
# Gemini (free tier): plain HTTP client, JSON answer instead of a tool call
# ---------------------------------------------------------------------------------------------------------------


class FakeGemini:
    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls: list[tuple[str, dict]] = []

    def generate(self, model, body):
        self.calls.append((model, body))
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


def _gemini_response(answer, finish="STOP", thoughts=0):
    return {
        "candidates": [{"content": {"parts": [{"text": json.dumps(answer)}]}, "finishReason": finish}],
        "usageMetadata": {"promptTokenCount": 2000, "candidatesTokenCount": 500, "thoughtsTokenCount": thoughts},
    }


def test_gemini_request_shape_and_perfect_answer(pdf: Path):
    gt = _gt()
    client = FakeGemini(_gemini_response(_answer(gt), thoughts=300))
    result = vision.extract_with_vision(pdf, [1, 2], client, provider="gemini")
    assert result.ok and result.provider == "gemini" and result.model == "gemini-3.6-flash"
    assert result.input_tokens == 2000 and result.output_tokens == 800  # thinking counts as output
    assert all(f.correct for f in compare(gt, result.statements))
    model, body = client.calls[0]
    assert model == "gemini-3.6-flash"
    config = body["generationConfig"]
    assert config["temperature"] == 0 and config["responseMimeType"] == "application/json"
    assert config["maxOutputTokens"] == vision.MAX_TOKENS_BY_PROVIDER["gemini"]
    images = [p for p in body["contents"][0]["parts"] if "inlineData" in p]
    assert len(images) == 2 and images[0]["inlineData"]["mimeType"] == "image/png"
    assert "responseJsonSchema" not in config  # the full schema is rejected by Gemini (HTTP 400)
    assert any("exactly this shape" in p.get("text", "") for p in body["contents"][0]["parts"])


def test_gemini_cut_off_empty_and_blocked_answers_are_errors(pdf: Path):
    cut = FakeGemini(_gemini_response({}, finish="MAX_TOKENS"))
    assert "cut off" in (vision.extract_with_vision(pdf, [1], cut, provider="gemini").error or "")
    empty = FakeGemini({"candidates": [{"content": {"parts": []}, "finishReason": "STOP"}], "usageMetadata": {}})
    assert "no text" in (vision.extract_with_vision(pdf, [1], empty, provider="gemini").error or "")
    blocked = FakeGemini({"promptFeedback": {"blockReason": "OTHER"}})
    assert "no answer" in (vision.extract_with_vision(pdf, [1], blocked, provider="gemini").error or "")


def test_gemini_temperature_rejected_is_retried_and_flagged(pdf: Path):
    client = FakeGemini(vision.ApiError(400, "temperature is not supported"), _gemini_response(_answer(_gt())))
    result = vision.extract_with_vision(pdf, [1], client, provider="gemini")
    assert result.ok and result.temperature_used is False
    assert "temperature" not in client.calls[1][1]["generationConfig"]


def test_gemini_client_uses_key_header_and_retries_when_busy(monkeypatch):
    import httpx

    seen = {}

    def fake_post(url, json, headers, timeout):
        seen["url"], seen["headers"] = url, headers
        return responses.pop(0)

    responses = [FakeHttpResponse(503), FakeHttpResponse(200, {"ok": True})]
    monkeypatch.setattr(httpx, "post", fake_post)
    client = vision.GeminiClient(api_key="test-key")
    waits: list[float] = []
    client.sleep = waits.append
    assert client.generate("gemini-x", {"a": 1}) == {"ok": True}
    assert seen["url"].endswith("/models/gemini-x:generateContent") and seen["headers"] == {
        "x-goog-api-key": "test-key"
    }
    assert waits == [2.0]


def test_make_client_needs_the_key(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(ValueError):
        vision.make_client("gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    assert isinstance(vision.make_client("gemini"), vision.GeminiClient)
