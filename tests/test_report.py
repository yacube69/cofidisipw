"""AI assessment (CIPW-16): output model, input summary and the proposed rating rules. No LLM call."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from src.extraction.vision import ApiError
from src.ratios.bankruptcy import compute_bankruptcy
from src.ratios.engine import compute_ratios
from src.report import generate
from src.report.inputs import assessment_input_from_result, build_assessment_input
from src.report.models import AssessmentReport, ReportRating
from src.report.rating import rate
from src.result import Result
from src.schema import GroundTruth
from src.validation.review import validate_and_review

GT_DIR = Path(__file__).resolve().parents[1] / "data" / "ground_truth"
GT_FILES = sorted(p for p in GT_DIR.glob("*.json") if not p.name.startswith("_"))


def _parts(path: Path):
    gt = GroundTruth.model_validate_json(path.read_text(encoding="utf-8"))
    fs = gt.to_statements()
    validation, _ = validate_and_review(fs)
    return fs, validation, compute_ratios(fs, validation), compute_bankruptcy(fs, validation)


def _input(name: str = "26161516_2025", benchmarks: dict | None = None) -> dict:
    return build_assessment_input(*_parts(GT_DIR / f"{name}.json"), benchmarks)


# ---------------------------------------------------------------------------------------------- output model


def test_report_model_accepts_the_mock_report_format():
    mock = json.loads((GT_DIR.parent / "fixtures" / "mock_26161516_2025.json").read_text(encoding="utf-8"))["report"]
    mock.pop("mock")
    report = AssessmentReport.model_validate(mock)
    assert report.rating.value == "high" and report.language == "en"


def test_report_model_rejects_unknown_fields_and_ratings():
    with pytest.raises(ValidationError):
        AssessmentReport(summary="s", rating=ReportRating(value="critical", reasoning="x"), data_quality_note="n")  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        AssessmentReport(summary="s", rating={"value": "low", "reasoning": "x"}, data_quality_note="n", extra=1)  # type: ignore[call-arg]


def test_a_report_without_a_rating_value_is_allowed():
    report = AssessmentReport(summary="s", rating=ReportRating(reasoning="not enough data"), data_quality_note="n")
    assert report.rating.value is None


# ---------------------------------------------------------------------------------------------- input


def test_input_is_json_ready_and_has_the_expected_sections():
    data = _input()
    json.dumps(data)  # numbers, strings, lists, None only
    assert set(data) == {
        "version",
        "company",
        "key_figures",
        "ratios",
        "bankruptcy",
        "benchmarks",
        "validation",
        "data_quality",
    }
    assert data["company"]["name"] == "Mondi Štětí a.s." and data["company"]["amounts_in"] == "CZK"
    assert [r["key"] for r in data["ratios"]][:3] == ["current_ratio", "quick_ratio", "cash_ratio"]


def test_key_figures_in_czk_with_year_over_year_change():
    figures = _input()["key_figures"]
    assert figures["total_assets"]["current"] == 29_457_798_000
    assert figures["total_assets"]["prior"] == 28_427_620_000
    assert figures["total_assets"]["change_pct"] == pytest.approx(3.6, abs=0.05)
    assert figures["revenue"]["current"] == 15_510_024_000  # I. + II.


def test_net_turnover_is_passed_on_when_there_is_no_revenue_in_lines_1_and_2():
    figures = _input("28297580_2025")["key_figures"]  # TRAIS GOLD: lines I. and II. are empty
    assert "revenue" not in figures
    assert figures["net_turnover"]["current"] == 23_553_000
    assert figures["net_turnover"]["change_pct"] == pytest.approx(-23.9, abs=0.05)


def test_ratios_keep_their_flags_and_reasons():
    ratios = {r["key"]: r for r in _input()["ratios"]}
    assert ratios["current_ratio"]["current"] == {"value": 0.2227, "flag": "ok", "reason": None}
    assert ratios["roe"]["change"] == pytest.approx(-11.5372, abs=1e-3)


def test_stub_benchmarks_are_never_passed_on():
    mock = {"mock": True, "ratios": {"current_ratio": {"light": "red"}}}
    data = _input(benchmarks=mock)
    assert data["benchmarks"] == {"available": False}
    assert any("no industry benchmarks" in n for n in data["data_quality"]["notes"])


def test_real_benchmarks_are_passed_on():
    real = {"nace": "17.12", "source": "x", "ratios": {"current_ratio": {"light": "red"}}}
    data = _input(benchmarks=real)
    assert data["benchmarks"]["available"] is True and data["benchmarks"]["ratios"]["current_ratio"]["light"] == "red"
    assert not any("no industry benchmarks" in n for n in data["data_quality"]["notes"])


def test_flagged_values_make_the_data_quality_limited():
    fs, validation, ratios, bankruptcy = _parts(GT_DIR / "26161516_2025.json")
    assert build_assessment_input(fs, validation, ratios, bankruptcy)["data_quality"]["overall"] == "good"
    fs.value("total_assets", "current").needs_review = True  # type: ignore[union-attr]
    data = build_assessment_input(fs, validation, compute_ratios(fs, validation), bankruptcy)
    assert data["data_quality"]["overall"] == "limited"
    assert data["key_figures"]["total_assets"]["needs_review"] is True
    assert any("flagged for review" in n for n in data["data_quality"]["notes"])


def test_a_company_without_income_statement_has_poor_data_and_no_scores():
    data = _input("08497222_2025")
    assert data["bankruptcy"]["in05"]["current"]["score"] is None
    assert data["data_quality"]["overall"] == "poor"


@pytest.mark.parametrize("path", GT_FILES, ids=lambda p: p.stem)
def test_every_ground_truth_company_gives_an_input_and_a_decision(path: Path):
    data = build_assessment_input(*_parts(path))
    json.dumps(data)
    decision = rate(data)
    assert decision.value in ("low", "medium", "high", None) and decision.rules and decision.reasoning


def test_input_from_result_needs_the_deterministic_stages():
    with pytest.raises(ValueError, match="missing"):
        assessment_input_from_result(Result(file="x.pdf"))


def test_input_from_result_drops_stub_benchmarks():
    fs, validation, ratios, bankruptcy = _parts(GT_DIR / "26161516_2025.json")
    result = Result(
        file="x.pdf",
        statements=fs,
        validation=validation,
        ratios=ratios,
        bankruptcy=bankruptcy,
        benchmarks={"mock": True, "ratios": {}},
        stages=[{"stage": "benchmarks", "status": "stub"}],  # type: ignore[list-item]
    )
    assert assessment_input_from_result(result)["benchmarks"] == {"available": False}


# ---------------------------------------------------------------------------------------------- rating rules


def _score(zone: str | None, flag: str = "ok") -> dict:
    return {"score": None if zone is None else 1.0, "zone": zone, "flag": flag, "reason": None}


def _case(in05, altman, equity=100.0, profit=10.0, passed=True, in05_flag="ok") -> dict:
    return {
        "bankruptcy": {
            "in05": {"current": _score(in05, in05_flag)},
            "altman_z_prime": {"current": _score(altman)},
        },
        "key_figures": {"equity": {"current": equity}, "profit_for_period": {"current": profit}},
        "validation": {"passed": passed},
    }


@pytest.mark.parametrize(
    ("case", "value", "rule"),
    [
        (_case("safe", "safe", equity=-5.0), "high", "R1"),
        (_case("distress", "distress"), "high", "R2"),
        (_case("distress", "grey", profit=-1.0), "high", "R3"),
        (_case("grey", "distress", profit=-1.0), "high", "R3"),
        (_case(None, None), None, "R4"),
        (_case("safe", "safe"), "low", "R5"),
        (_case("safe", "safe", profit=0.0), "low", "R5"),
        (_case("distress", "grey"), "medium", "R6"),
        (_case("safe", "grey"), "medium", "R6"),
        (_case("safe", "safe", profit=-1.0), "medium", "R6"),
        (_case("safe", "safe", profit=None), "medium", "R6"),
        (_case("safe", "safe", passed=False), "medium", "R6"),
        (_case("safe", "safe", in05_flag="unreliable"), "medium", "R6"),
        (_case("safe", None), "medium", "R6"),
    ],
)
def test_rating_rules(case: dict, value: str | None, rule: str):
    decision = rate(case)
    assert decision.value == value and decision.rules == [rule]


def test_reasoning_names_the_scores_and_the_doubt():
    decision = rate(_case("distress", "distress", in05_flag="unreliable"))
    assert "IN05 1.0 (distress)" in decision.reasoning and "flagged for review" in decision.reasoning
    assert rate(_case(None, None)).reasoning.startswith("No bankruptcy score")


# ---------------------------------------------------------------------------------------------- prompt


def test_the_prompt_the_pipeline_records_exists_and_has_its_placeholder():
    from src.pipeline import REPORT_PROMPT

    text = (Path(__file__).resolve().parents[1] / "prompts" / REPORT_PROMPT).read_text(encoding="utf-8")
    assert REPORT_PROMPT == "assessment_v6.md" and text.count("{{LANGUAGE}}") == 2
    for section in ("key_figures", "ratios", "bankruptcy", "benchmarks", "validation", "data_quality", "rating"):
        assert f"`{section}`" in text


# ---------------------------------------------------------------------------------------------- LLM call, fake clients

ANSWER = {
    "language": "xx",
    "summary": "Mondi Štětí a.s. has total assets of 29.5 billion CZK.",
    "strengths": ["Revenue 15.5 billion CZK."],
    "risks": [],
    "red_flags": ["IN05 0.55 and Altman Z′ 0.61, both in distress."],
    "rating": {"value": "high", "reasoning": "Rule R2."},
    "data_quality_note": "No problems found.",
}


class FakeAnthropic:
    def __init__(self, answer=None, stop="tool_use"):
        self.calls, self.answer, self.stop = [], ANSWER if answer is None else answer, stop
        self.messages = self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        block = SimpleNamespace(type="tool_use", name=generate.TOOL_NAME, input=self.answer)
        return SimpleNamespace(
            usage=SimpleNamespace(input_tokens=1000, output_tokens=200), stop_reason=self.stop, content=[block]
        )


class FakeMistral:
    def __init__(self, answer=None):
        self.calls, self.answer = [], ANSWER if answer is None else answer

    def chat(self, payload):
        self.calls.append(payload)
        call = {"function": {"name": generate.TOOL_NAME, "arguments": json.dumps(self.answer)}}
        return {
            "usage": {"prompt_tokens": 900, "completion_tokens": 150},
            "choices": [{"finish_reason": "tool_calls", "message": {"tool_calls": [call]}}],
        }


class FakeGemini:
    def __init__(self, text=None, reject_temperature=False):
        self.calls, self.text, self.reject = [], json.dumps(ANSWER) if text is None else text, reject_temperature

    def generate(self, model, body):
        self.calls.append((model, body))
        if self.reject and "temperature" in body["generationConfig"]:
            raise ApiError(400, "temperature is not supported")
        return {
            "usageMetadata": {"promptTokenCount": 800, "candidatesTokenCount": 100, "thoughtsTokenCount": 50},
            "candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": self.text}]}}],
        }


def _run(client, provider, answer_data=None, **kwargs):
    return generate.generate_report(
        answer_data or _input(), file="26161516_2025", provider=provider, client=client, **kwargs
    )


def test_prompt_is_filled_with_the_language_and_has_no_header():
    prompt = generate.build_prompt("cs")
    assert "Write all text in cs." in prompt and "{{" not in prompt and "Assessment prompt v" not in prompt
    assert prompt.startswith("You are an experienced credit analyst")


def test_user_message_carries_the_input_and_the_rule_rating():
    data = _input()
    message = json.loads(generate.user_message(data, rate(data)))
    assert message["company"]["ico"] == data["company"]["ico"]
    assert message["rating"]["value"] == "high" and message["rating"]["rules"] == ["R2"]


def test_the_tool_schema_has_the_fields_of_the_report_model():
    schema = generate.output_schema()
    written_by_the_model = set(AssessmentReport.model_fields) - {"recommendation"}  # the code adds it
    assert set(schema["properties"]) == written_by_the_model
    assert set(schema["required"]) == written_by_the_model


def test_anthropic_call_gives_a_validated_report_in_our_language():
    client = FakeAnthropic()
    run = _run(client, "anthropic", language="en")
    assert run.ok and run.report.language == "en" and run.report.rating.value == "high"
    assert (run.input_tokens, run.output_tokens) == (1000, 200)
    call = client.calls[0]
    assert call["temperature"] == 0 and call["tool_choice"]["name"] == generate.TOOL_NAME
    assert "Write all text in en." in call["system"]


def test_mistral_call():
    client = FakeMistral()
    run = _run(client, "mistral")
    assert run.ok and run.model == "mistral-medium-3.5"
    payload = client.calls[0]
    assert payload["temperature"] == 0 and payload["messages"][0]["role"] == "system"


def test_gemini_call_and_temperature_fallback():
    client = FakeGemini(reject_temperature=True)
    run = _run(client, "gemini")
    assert run.ok and run.temperature_used is False and any("temperature" in w for w in run.warnings)
    assert run.output_tokens == 150  # thinking tokens count as output
    assert len(client.calls) == 2


def test_the_rating_is_the_rules_not_the_models():
    answer = {**ANSWER, "rating": {"value": "low", "reasoning": "x"}}
    run = _run(FakeAnthropic(answer), "anthropic")
    assert run.ok and run.report.rating.value == "high"
    assert any("the rules give 'high'" in w for w in run.warnings)


def test_no_rating_stays_none_even_when_the_model_writes_none():
    data = _input("08497222_2025")
    for value in (None, "none", "null"):
        answer = {**ANSWER, "rating": {"value": value, "reasoning": "Not possible."}}
        run = _run(FakeAnthropic(answer), "anthropic", answer_data=data)
        assert run.ok and run.report.rating.value is None and not run.warnings


def test_bad_answers_become_errors_not_exceptions():
    assert "ValidationError" in (_run(FakeAnthropic({"summary": "only"}), "anthropic").error or "")
    assert "JSONDecodeError" in (_run(FakeGemini("not json"), "gemini").error or "")
    assert "cut off" in (_run(FakeAnthropic(stop="max_tokens"), "anthropic").error or "")
    assert _run(FakeAnthropic(), "anthropic").report is not None


def test_unknown_provider_is_refused():
    with pytest.raises(ValueError, match="unknown provider"):
        generate.generate_report(_input(), provider="nope")


def test_markdown_for_the_reviewers():
    data = _input()
    run = _run(FakeAnthropic(), "anthropic", answer_data=data)
    text = generate.report_markdown(run, data)
    assert text.startswith("# Mondi Štětí a.s.") and "Risk rating (by rules R2): high risk" in text
    assert "## Red flags" in text and "## Review (fill in)" in text and "- none" in text  # risks is empty
    failed = generate.ReportRun(
        file="x", provider="gemini", model="m", language="en", prompt_version="v", rating=rate(data), error="boom"
    )
    assert "**No report:** boom" in generate.report_markdown(failed, data)


def test_sentences_from_the_model_are_tidied():
    answer = {**ANSWER, "data_quality_note": " overall data quality is good.", "risks": ["  cash fell."]}
    run = _run(FakeAnthropic(answer), "anthropic")
    assert run.report.data_quality_note == "Overall data quality is good."
    assert run.report.risks == ["Cash fell."]


def test_v3_prompt_has_the_rules_of_the_first_reports():
    v3 = Path(generate.PROMPT_PATH).with_name("assessment_v3.md")
    text = generate.build_prompt("en", v3)
    for phrase in ("more than 20 %", "never a strength", "15.49 million CZK", "bringing equity to", "no others"):
        assert phrase in text


def test_v5_prompt_takes_the_threshold_from_the_settings_and_keeps_the_v3_rules():
    text = generate.build_prompt("en", worsening_pct=15)
    assert "more than 15 %" in text and "{{" not in text and "20 %" not in text
    for phrase in ("never a strength", "15.49 million CZK", "stands at", "below 0.01", "net turnover", "no rating"):
        assert phrase in text


def test_rating_reasons_name_what_decided():
    low = rate(_case("safe", "safe"))
    assert "all accounting checks passed" in low.reasoning
    medium = rate(_case("safe", "grey"))
    assert "not both in the safe zone" in medium.reasoning
    assert "loss" in rate(_case("safe", "safe", profit=-1.0)).reasoning
    assert "check failed" in rate(_case("safe", "safe", passed=False)).reasoning


def test_report_clients_retry_only_twice(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    client = FakeGemini()
    client.max_retries = 6
    run = _run(client, "gemini")
    assert run.ok and client.max_retries == generate.MAX_RETRIES


def test_a_busy_answer_is_not_retried_because_it_may_count_against_the_quota(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    client = FakeGemini()
    client.max_retries, client.retry_statuses = 6, (429, 503)
    run = _run(client, "gemini")
    assert run.ok and client.retry_statuses == (429,)


def test_review_pack_collects_the_saved_reports(tmp_path):
    data = _input()
    for stem, with_report in (("a_2025", True), ("b_2025", False)):
        report = _run(FakeAnthropic(), "anthropic", answer_data=data)
        (tmp_path / f"{stem}.json").write_text(
            json.dumps(
                {
                    "input": data,
                    "rating": report.rating.model_dump(),
                    "report": report.report.model_dump() if with_report else None,
                    "prompt_version": "v-" + stem,
                }
            ),
            encoding="utf-8",
        )
        if with_report:
            (tmp_path / f"{stem}.md").write_text(generate.report_markdown(report, data), encoding="utf-8")
    pack = generate.review_pack(tmp_path)
    assert pack.startswith("# AI assessment reports for review")
    assert "| a_2025 | Mondi Štětí a.s. | high risk (R2) | Not recommended | good | report | v-a_2025 |" in pack
    assert "| b_2025 | Mondi Štětí a.s. | high risk (R2) | – | good | ERROR | v-b_2025 |" in pack
    assert "reports come from" not in pack  # one version with a report
    assert pack.count("## Review (fill in)") == 1


def test_review_pack_warns_about_mixed_prompt_versions(tmp_path):
    data = _input()
    for stem in ("a", "b"):
        run = _run(FakeAnthropic(), "anthropic", answer_data=data)
        (tmp_path / f"{stem}.json").write_text(
            json.dumps(
                {
                    "input": data,
                    "rating": run.rating.model_dump(),
                    "report": run.report.model_dump(),
                    "prompt_version": "v-" + stem,
                }
            ),
            encoding="utf-8",
        )
    assert "Warning: the reports come from 2 different prompt versions (v-a, v-b)" in generate.review_pack(tmp_path)


# ------------------------------------------------------------------------------- settings the team can change


def test_default_rating_config_changes_nothing():
    from src.report.rating import RatingConfig

    data = _case("distress", "distress")
    assert rate(data, RatingConfig()) == rate(data)
    assert rate(data).rules_version == "proposal-1"


def test_a_rule_can_be_switched_off():
    from src.report.rating import RatingConfig

    decision = rate(_case("safe", "safe", equity=-5.0), RatingConfig(disabled_rules=["R1"]))
    assert (decision.value, decision.rules) == ("low", ["R5"])
    assert decision.rules_version == "proposal-1+custom"


def test_r4_and_r6_cannot_be_switched_off():
    from pydantic import ValidationError

    from src.report.rating import RatingConfig

    with pytest.raises(ValidationError):
        RatingConfig(disabled_rules=["R4"])


def _no_score(current_ratio: float | None) -> dict:
    data = _case(None, None)
    data["ratios"] = [{"key": "current_ratio", "current": {"value": current_ratio}}]
    return data


@pytest.mark.parametrize(("ratio", "value", "rule"), [(0.8, "high", "R7"), (1.4, "medium", "R8"), (None, None, "R4")])
def test_partial_rating_from_the_balance_sheet(ratio, value, rule):
    from src.report.rating import RatingConfig

    decision = rate(_no_score(ratio), RatingConfig(partial=True))
    assert (decision.value, decision.rules) == (value, [rule])


def test_without_partial_a_company_without_score_has_no_rating():
    assert rate(_no_score(0.8)).value is None


def test_the_worsening_threshold_fills_the_prompt_placeholder(tmp_path):
    prompt = tmp_path / "p.md"
    prompt.write_text("header\n---\nWorse than {{WORSENING_PCT}} % in {{LANGUAGE}}.", encoding="utf-8")
    assert generate.build_prompt("cs", prompt, 15) == "Worse than 15 % in cs.\n"


def test_report_settings_default_to_what_we_use_now():
    from src.settings import Settings

    report = Settings().report
    assert (report.language, report.provider, report.worsening_pct) == ("cs", "gemini", 20)
    assert report.rating.partial and not report.rating.disabled_rules


def test_review_pack_explains_how_the_rating_is_decided(tmp_path):
    pack = generate.review_pack(tmp_path)
    assert "How the risk rating is decided" in pack
    assert all(f"| R{n} |" in pack for n in range(1, 7))


# ------------------------------------------------------------------------------------------ figures tables


def test_amounts_are_written_like_in_the_prompt():
    from src.report.tables import amount

    assert [amount(v) for v in (29_460_000_000, 176_520_000, -12_580_000, 799_000, 407_000, 0, None)] == [
        "29.46 billion",
        "176.52 million",
        "-12.58 million",
        "799 000",
        "407 000",
        "0",
        "–",
    ]


def test_figures_tables_come_from_the_input_with_the_reasons_of_missing_ratios():
    from src.report.tables import figures_markdown

    text = "\n".join(figures_markdown(_input("28297580_2025")))  # TRAIS GOLD
    assert "| 23.55 million | 30.97 million | -23.9 % |" in text and "Net turnover" in text
    assert "| Current ratio | current_assets / liabilities_short_term | 2.75 | 2.33 | +0.41 |" in text
    assert "| Payables days |" in text and "not meaningful" in text and "not computed" in text
    assert "### Bankruptcy models" in text and "### Accounting checks" in text


def test_the_report_page_ends_with_the_figures_before_the_review_checklist(tmp_path):
    data = _input("28297580_2025")
    run = generate.generate_report(data, file="x", client=FakeGemini(), provider="gemini")
    page = generate.report_markdown(run, data)
    order = [page.index(h) for h in ("## Data quality note", "## Figures for the reviewer", "## Review (fill in)")]
    assert order == sorted(order)


def test_tables_round_half_up_like_the_text():
    from src.report.tables import amount

    # 118 945 000 is 118.95 million for a reader and for the LLM; the float format would give 118.94
    assert [amount(v) for v in (118_945_000, 15_485_000, 14_125_000_000, 155_535_000)] == [
        "118.95 million",
        "15.49 million",
        "14.13 billion",
        "155.54 million",
    ]


def test_v5_prompt_has_the_rules_of_the_second_review():
    text = generate.build_prompt("en")
    for phrase in (
        "unchanged at 0.17",
        "is a red flag and only a red flag",
        "do not mention net turnover at all",
        "`validation.checks_total`",
        "No judging words",
        "a decline of more than 20 %",  # quoted as a thing not to write (20 is the default threshold)
    ):
        assert phrase in text
    assert "both bankruptcy models in the distress" not in text


def test_the_result_printed_in_the_balance_sheet_is_a_key_figure_only_without_income_statement():
    efko = _input("26915758_2024")["key_figures"]  # no income statement in the filing
    assert "profit_for_period" not in efko
    assert efko["profit_current_period"]["current"] == 1_185_000
    assert "profit_current_period" not in _input("48171131_2025")["key_figures"]  # has the income statement


# ---------------------------------------------------------------------------------------------- recommendation, Czech


def test_the_recommendation_follows_the_rating_with_its_criteria():
    from src.report.recommendation import RecommendationConfig, recommend

    data = _input()
    decision = rate(data)
    rec = recommend(data, decision)
    expected = {"low": "recommended", "medium": "caution", "high": "not_recommended", None: "no_recommendation"}
    assert rec.level == expected[decision.value]
    assert rec.criteria[0].startswith(f"Rule {decision.rules[0]} decided")
    assert any(line.startswith("IN05") for line in rec.criteria) and any("Altman" in line for line in rec.criteria)
    assert any(line.startswith("Accounting checks") for line in rec.criteria)
    # the table is a setting: a team that wants high risk to read "caution" changes the file, not the code
    changed = recommend(data, decision, RecommendationConfig(high="caution", medium="caution"))
    assert changed.level == "caution" if decision.value == "high" else True


def test_every_rating_gets_a_recommendation_level():
    from src.report.recommendation import RecommendationConfig

    config = RecommendationConfig()
    assert (config.low, config.medium, config.high, config.none) == (
        "recommended",
        "caution",
        "not_recommended",
        "no_recommendation",
    )


def test_the_recommendation_is_added_by_the_code_and_survives_the_report_page():
    data = _input()
    run = _run(FakeAnthropic(), "anthropic", answer_data=data)
    assert run.report.recommendation is not None
    page = generate.report_markdown(run, data)
    assert "## Recommendation" in page and run.report.recommendation.label in page
    assert all(f"- {c}" in page for c in run.report.recommendation.criteria)


def test_the_two_languages_have_the_same_fixed_texts():
    from src.report.texts import CS, EN

    assert set(CS) == set(EN)
    assert set(CS["levels"]) == set(EN["levels"]) and set(CS["rating"]) == set(EN["rating"])
    assert len(CS["review"]) == len(EN["review"])
    from src.report.recommendation import RULE_WHY

    assert set(RULE_WHY["cs"]) == set(RULE_WHY["en"])


def test_the_czech_page_uses_czech_headings_decimal_comma_and_czech_units():
    from src.report.tables import amount

    data = _input()
    run = _run(FakeAnthropic(), "anthropic", answer_data=data, language="cs")
    page = generate.report_markdown(run, data)
    for text in ("## Shrnutí", "## Doporučení", "Rating rizika", "Klíčové údaje", "Běžná likvidita", "Modely bankrotu"):
        assert text in page
    assert "## Summary" not in page and "Current ratio" not in page
    assert amount(29_460_000_000, "cs") == "29,46 mld." and amount(176_520_000, "cs") == "176,52 mil."
    assert amount(524_000, "cs") == "524 000"
    assert "Doporučeno" in page or "Nedoporučeno" in page or "Bez doporučení" in page


def test_an_unknown_language_gets_english_fixed_texts():
    from src.report.texts import texts

    assert (
        texts("fr")["summary"] == "Summary"
        and texts("cs")["summary"] == "Shrnutí"
        and texts("CS-cz")["summary"] == "Shrnutí"
    )


def test_prompt_v6_keeps_the_placeholders_and_asks_for_czech_numbers():
    text = generate.build_prompt("cs", generate.PROMPT_PATH)
    assert "mld. Kč" in text and "Write all text in cs" in text
    assert "do not mention a recommendation" in text


# ---------------------------------------------------------------------------------------------- translation (B)

CZECH = {
    "language": "xx",
    "summary": "Mondi Štětí a.s. má celková aktiva 29,5 mld. Kč.",
    "strengths": ["Tržby 15,5 mld. Kč."],
    "risks": [],
    "red_flags": ["IN05 0,55 a Altman Z′ 0,61, oba v zóně ohrožení."],
    "rating": {"value": "low", "reasoning": "Pravidlo R2."},
    "data_quality_note": "Nebyly zjištěny žádné problémy.",
}


def _english():
    return _run(FakeAnthropic(), "anthropic")


def test_numbers_are_compared_as_values_not_as_written():
    assert generate.numbers_in(["1391.2 % and 238.17 million"]) == generate.numbers_in(["1 391,2 % a 238,17 mil."])
    assert generate.numbers_in(["74.8 days"]) != generate.numbers_in(["74,9 dne"])


def test_a_translation_keeps_the_analysis_and_the_code_sets_rating_and_recommendation():
    english = _english()
    client = FakeAnthropic(CZECH)
    run = generate.translate_report(english, _input(), language="cs", client=client)
    assert run.ok and run.language == "cs" and run.report.language == "cs"
    assert run.report.rating.value == english.report.rating.value == "high"  # the model's "low" is not kept
    assert run.report.recommendation.label == "Nedoporučeno"
    assert run.warnings == []  # every number of the English text is in the Czech one
    assert "+" in run.prompt_version and english.prompt_version in run.prompt_version
    assert json.loads(client.calls[0]["messages"][0]["content"])["summary"] == english.report.summary


def test_a_translation_with_a_changed_number_is_a_warning():
    changed = {**CZECH, "summary": "Mondi Štětí a.s. má celková aktiva 29,6 mld. Kč."}
    run = generate.translate_report(_english(), _input(), language="cs", client=FakeAnthropic(changed))
    assert run.ok and any("numbers differ" in w and "29.5" in w and "29.6" in w for w in run.warnings)


def test_a_translation_with_other_number_of_items_is_an_error():
    changed = {**CZECH, "risks": ["Nové riziko."]}
    run = generate.translate_report(_english(), _input(), language="cs", client=FakeAnthropic(changed))
    assert not run.ok and "number of items in risks" in run.error


def test_nothing_to_translate_is_an_error_not_a_crash():
    english = _english()
    english.report = None
    run = generate.translate_report(english, _input(), language="cs", client=FakeAnthropic(CZECH))
    assert not run.ok and "no English report" in run.error


def test_the_translation_prompt_has_the_language_placeholder_and_no_header():
    text = generate.build_prompt("cs", generate.TRANSLATION_PROMPT_PATH)
    assert "into cs" in text and "{{" not in text and "Translation prompt v" not in text
    assert "Do not add, remove, merge" in text


def test_czech_days_use_the_form_of_a_decimal_number():
    from src.report.texts import texts

    assert texts("cs")["days"] == "dne"


# ---------------------------------------------------------------------------------------------- Word export


def _docx_text(path):
    from docx import Document

    document = Document(path)
    return document, [p.text for p in document.paragraphs]


def test_the_word_file_has_the_text_and_the_tables_of_the_report_page(tmp_path):
    from src.report import export_docx

    data = _input()
    run = _run(FakeAnthropic(), "anthropic", answer_data=data)
    document, paragraphs = _docx_text(export_docx.build_docx([(run, data)], tmp_path / "r.docx"))
    page = generate.report_markdown(run, data)
    assert paragraphs[0].startswith("Mondi Štětí a.s. (IČO 26161516)")
    for text in (
        run.report.summary,
        run.report.rating.reasoning,
        run.report.data_quality_note,
        run.report.recommendation.label,
        *run.report.recommendation.criteria,
    ):
        assert text in paragraphs and text in page
    assert "Risk rating (by rules R2): high risk" in paragraphs
    assert len(document.tables) == 4 and document.tables[0].rows[0].cells[0].text == "line"
    assert "Review (fill in)" not in " ".join(paragraphs)  # the checklist is for the reviewers' page


def test_the_word_file_follows_the_language_and_puts_companies_on_separate_pages(tmp_path):
    from src.report import export_docx

    data = _input()
    run = _run(FakeAnthropic(), "anthropic", answer_data=data, language="cs")
    document, paragraphs = _docx_text(export_docx.build_docx([(run, data), (run, data)], tmp_path / "r.docx"))
    assert "Rating rizika (podle pravidel R2): vysoké riziko" in paragraphs and "Doporučení" in paragraphs
    assert document.tables[0].rows[0].cells[0].text == "položka" and len(document.tables) == 8
    assert sum(1 for p in document.paragraphs if 'w:br w:type="page"' in p._p.xml) == 1


def test_the_word_export_needs_something_to_write(tmp_path):
    import pytest as _pytest

    from src.report import export_docx

    with _pytest.raises(ValueError):
        export_docx.build_docx([], tmp_path / "r.docx")
