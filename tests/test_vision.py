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
