"""AI assessment (CIPW-16): output model, input summary and the proposed rating rules. No LLM call."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from src.llm import LlmError, Reply
from src.ratios.bankruptcy import compute_bankruptcy
from src.ratios.engine import compute_ratios
from src.report import generate
from src.report.inputs import assessment_input_from_result, build_assessment_input
from src.report.models import AssessmentReport, ReportRating
from src.report.rating import rate
from src.result import LLMUsage, Result
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
    assert [m["key"] for m in data["bankruptcy"]] == ["in05", "altman_z_prime"]  # MODELS, in order
    assert all(m["current"]["score"] is None for m in data["bankruptcy"])
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


def _model(key: str, zone: str | None, flag: str = "ok", name: str | None = None, probability=None) -> dict:
    score = {"score": None if zone is None else 1.0, "zone": zone, "flag": flag, "reason": None,
             "probability": probability}  # fmt: skip
    return {"key": key, "name": name or key.upper(), "zones": "x", "horizon": None, "current": score}


def _case(in05, altman, equity=100.0, profit=10.0, passed=True, in05_flag="ok", extra=(), reds=0, ambers=0) -> dict:
    """An assessment input as the rules read it: the models list of the input, the traffic lights of the ratios."""
    models = [
        _model("in05", in05, in05_flag, "IN05 (Neumaierová & Neumaier)"),
        _model("altman_z_prime", altman, name="Altman Z′ (private companies)"),
        *extra,
    ]
    lights = ["red"] * reds + ["amber"] * ambers + ["green"]
    return {
        "bankruptcy": models,
        "ratios": [{"key": f"r{i}", "light": light, "current": {"value": 1.0}} for i, light in enumerate(lights)],
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


@pytest.mark.parametrize(
    ("case", "value", "rule"),
    [
        (_case("safe", "safe", reds=1), "medium", "R9"),  # the low of R5 is raised: fallback.py's rule
        (_case("safe", "safe", reds=2), "high", "R9"),
        (_case("grey", "safe", reds=2), "high", "R9"),  # medium of R6 raised to high
        (_case("safe", "safe", ambers=3), "medium", "R9"),  # the amber criterion of fallback.py
        (_case("safe", "safe", ambers=2), "low", "R5"),
        (_case("grey", "safe", reds=1), "medium", "R6"),  # R9 never lowers or relabels what is already medium
        (_case("distress", "distress", reds=3), "high", "R2"),  # already high: the earlier rule stays
        (_case(None, None, reds=2), "high", "R9"),  # no score at all: the lights still count (as in fallback.py)
        (_case(None, None, reds=1), "medium", "R9"),
        (_case(None, None, ambers=2), None, "R4"),
    ],
)
def test_red_and_amber_ratios_only_make_the_rating_worse_r9(case: dict, value: str | None, rule: str):
    decision = rate(case)
    assert decision.value == value and decision.rules == [rule]


def test_r9_can_be_switched_off_and_inputs_without_lights_have_none():
    from src.report.rating import RatingConfig

    assert rate(_case("safe", "safe", reds=2), RatingConfig(disabled_rules=["R9"])).rules == ["R5"]
    old = _case("safe", "safe")
    for ratio in old["ratios"]:
        ratio.pop("light")
    assert rate(old).rules == ["R5"]


def test_r9_does_not_override_a_partial_rating_of_high():
    from src.report.rating import RatingConfig

    case = _case(None, None, reds=1)
    case["ratios"].append({"key": "current_ratio", "light": "red", "current": {"value": 0.5}})
    decision = rate(case, RatingConfig(partial=True))
    assert decision.value == "high" and decision.rules == ["R7"]


def test_reasoning_names_the_scores_and_the_doubt():
    decision = rate(_case("distress", "distress", in05_flag="unreliable"))
    assert "IN05 1.00 (distress)" in decision.reasoning and "flagged for review" in decision.reasoning
    assert rate(_case(None, None)).reasoning.startswith("No bankruptcy score")


def test_reasoning_is_written_in_czech_and_english():
    case = _case("safe", "safe", reds=2)
    en, cs = rate(case, language="en"), rate(case, language="cs")
    assert en.value == cs.value == "high" and en.rules == cs.rules == ["R9"]
    assert en.reasoning.startswith("2 ratios have a red traffic light") and "IN05 1.00 (safe)" in en.reasoning
    assert cs.reasoning.startswith("Počet ukazatelů s červeným semaforem (slabé): 2")
    assert "IN05 1,00 (bezpečná zóna)" in cs.reasoning  # the Czech decimal sign and zone words
    assert rate(_case("safe", "safe", equity=-5.0), language="cs").reasoning.startswith("Vlastní kapitál je záporný")


# ---------------------------------------------------------------------------------------------- any number of models

PROBIT = _model("probit12", "safe", name="Probit 12M (Cofidis default model)", probability=0.02)


def test_a_third_model_counts_in_r2_r3_and_r5():
    distress = _model("probit12", "distress", name="Probit 12M (Cofidis default model)", probability=0.4)
    assert rate(_case("distress", "distress", extra=[distress])).rules == ["R2"]
    assert rate(_case("distress", "distress", extra=[PROBIT])).rules == ["R6"]  # not all in distress, no loss
    assert rate(_case("safe", "safe", extra=[distress])).rules == ["R6"]  # one model sees danger: not low
    assert rate(_case("safe", "safe", extra=[distress], profit=-1.0)).rules == ["R3"]
    assert rate(_case("safe", "safe", extra=[PROBIT])).rules == ["R5"]
    assert rate(_case("safe", "safe", extra=[_model("probit12", None, name="Probit 12M")])).rules == ["R6"]
    assert "Probit 12M" in rate(_case("safe", "safe", extra=[distress], profit=-1.0)).reasoning
    assert "Probit 12M 1.00 (safe)" in rate(_case("safe", "safe", extra=[PROBIT])).reasoning


def test_a_model_that_is_missing_never_makes_the_rating_better():
    only_in05 = _case("safe", "safe")
    only_in05["bankruptcy"] = only_in05["bankruptcy"][:1]  # Altman Z′ removed from MODELS: nothing crashes
    assert rate(only_in05).rules == ["R5"]  # the one model there is, is safe
    uncomputed = _case("safe", None)
    assert rate(uncomputed).value == "medium"  # a model that cannot be computed: not low
    assert rate(uncomputed).reasoning.startswith("No rule for high risk applies, but a bankruptcy model could")
    no_models = _case("safe", "safe")
    no_models["bankruptcy"] = []
    assert rate(no_models).rules == ["R4"]


def test_a_third_model_in_the_real_pipeline_parts(monkeypatch):
    """A model added to MODELS (a 12-month probit with a probability) is in the input, the rating, the criteria and
    the tables, by its own name."""
    from src.ratios.bankruptcy import MODELS, Model, Term
    from src.report.recommendation import criteria
    from src.report.tables import figure_tables

    probit = Model(
        key="probit12",
        name="Probit 12M (Cofidis default model)",
        source="test",
        zones="below 0 safe, above 0.5 distress",
        distress=0.5,
        safe=0.0,
        higher_is_safer=False,
        terms=(Term("X1", "equity / total_assets", -1.0, lambda i: i.get("equity") / i.get("total_assets")),),
        constant=-0.5,
        link="probit",
        horizon="12 months",
    )
    fs, validation, ratios, _ = _parts(GT_DIR / "26161516_2025.json")
    bankruptcy = compute_bankruptcy(fs, validation, models=(*MODELS, probit))
    data = build_assessment_input(fs, validation, ratios, bankruptcy)
    third = data["bankruptcy"][2]
    assert third["key"] == "probit12" and third["horizon"] == "12 months"
    assert third["current"]["probability"] is not None and third["current"]["score"] is not None
    decision = rate(data)
    assert "Probit 12M" in decision.reasoning
    assert any(line.startswith("Probit 12M") for line in criteria(data, decision, "cs"))
    tables, _ = figure_tables(data, "cs")
    bankruptcy_rows = next(rows for title, _, rows in tables if title == "Modely bankrotu")
    assert [row[0] for row in bankruptcy_rows][-1] == "Probit 12M (Cofidis default model)"


def test_a_model_removed_from_models_does_not_break_the_input():
    from src.ratios.bankruptcy import IN05

    fs, validation, ratios, _ = _parts(GT_DIR / "26161516_2025.json")
    data = build_assessment_input(fs, validation, ratios, compute_bankruptcy(fs, validation, models=(IN05,)))
    assert [m["key"] for m in data["bankruptcy"]] == ["in05"]
    assert rate(data).value in ("low", "medium", "high")


# ---------------------------------------------------------------------------------------------- prompt


def test_the_prompt_the_pipeline_records_exists_and_has_its_placeholder():
    from src.pipeline import REPORT_PROMPT

    text = (Path(__file__).resolve().parents[1] / "prompts" / REPORT_PROMPT).read_text(encoding="utf-8")
    assert REPORT_PROMPT == "assessment_v8.md" and text.count("{{LANGUAGE}}") == 2
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


class FakeAsk:
    """The model, as `generate` asks it: (system, user) -> Reply. `calls` keeps what was sent."""

    def __init__(self, answer=None, tokens=(1000, 200), cost=0.0, model="mistral-medium-3.5"):
        self.calls, self.answer = [], ANSWER if answer is None else answer
        self.tokens, self.cost, self.model = tokens, cost, model

    def __call__(self, system, user):
        self.calls.append((system, user))
        usage = LLMUsage(input_tokens=self.tokens[0], output_tokens=self.tokens[1], cost_usd=self.cost)
        return Reply(data=self.answer, usage=usage, model=self.model)


def _run(ask, answer_data=None, **kwargs):
    return generate.generate_report(answer_data or _input(), file="26161516_2025", ask=ask, **kwargs)


def test_prompt_is_filled_with_the_language_and_has_no_header():
    prompt = generate.build_prompt("cs")
    assert "Write all text in cs." in prompt and "{{" not in prompt and "Assessment prompt v" not in prompt
    assert prompt.startswith("You are an experienced credit analyst")


def test_user_message_carries_the_input_and_the_rule_rating():
    data = _input()
    message = json.loads(generate.user_message(data, rate(data)))
    assert message["company"]["ico"] == data["company"]["ico"]
    assert message["rating"]["value"] == "high" and message["rating"]["rules"] == ["R2"]


def test_the_prompt_asks_for_the_fields_of_the_report_model():
    written_by_the_model = set(AssessmentReport.model_fields) - {"recommendation", "source"}  # the code adds them
    answer = generate.build_prompt("en").split("## Answer")[1]
    assert all(f'"{name}"' in answer for name in written_by_the_model) and "JSON object" in answer
    assert not any(f'"{name}"' in answer for name in ("recommendation", "source"))


def test_the_ask_gives_a_validated_report_in_our_language():
    ask = FakeAsk()
    run = _run(ask, language="en")
    assert run.ok and run.report.language == "en" and run.report.rating.value == "high"
    assert (run.input_tokens, run.output_tokens, run.model) == (1000, 200, "mistral-medium-3.5")
    system, user = ask.calls[0]
    assert "Write all text in en." in system and json.loads(user)["rating"]["value"] == "high"


class FakeChat:
    """Mistral's chat completions, as `src.llm` asks it."""

    def __init__(self, answer=None):
        self.payloads, self.answer = [], ANSWER if answer is None else answer

    def chat(self, payload):
        self.payloads.append(payload)
        message = {"content": json.dumps(self.answer)}
        return {"usage": {"prompt_tokens": 900, "completion_tokens": 150},
                "choices": [{"finish_reason": "stop", "message": message}]}  # fmt: skip


@pytest.fixture
def llm_settings():
    from pydantic import SecretStr

    from src.settings import get_settings

    s = get_settings().model_copy(deep=True)
    s.offline, s.llm.api_key = False, SecretStr("test-key")
    s.llm.price_in_per_mtok, s.llm.price_out_per_mtok = 0.4, 2.0
    return s


def test_by_default_the_report_goes_through_src_llm_ask_json(monkeypatch, llm_settings):
    """No provider choice: `settings.llm` (Mistral's API or an own server) through `ask_json`, with its cost."""
    chat = FakeChat()
    seen = []
    monkeypatch.setattr("src.llm.client", lambda settings: seen.append(settings.llm.url) or chat)
    run = generate.generate_report(_input(), file="26161516_2025", settings=llm_settings)
    assert run.ok and run.model == llm_settings.llm.model and seen == [llm_settings.llm.url]
    payload = chat.payloads[0]
    assert payload["response_format"] == {"type": "json_object"} and payload["temperature"] == 0
    assert payload["max_tokens"] == generate.MAX_TOKENS and "Write all text in en." in payload["messages"][0]["content"]
    assert (run.input_tokens, run.output_tokens) == (900, 150)
    assert run.cost_usd == pytest.approx((900 * 0.4 + 150 * 2.0) / 1e6)


def test_offline_mode_calls_no_model_and_says_so(monkeypatch, llm_settings):
    chat = FakeChat()
    monkeypatch.setattr("src.llm.client", lambda settings: chat)
    llm_settings.offline = True
    run = generate.generate_report(_input(), file="x", settings=llm_settings)
    assert not run.ok and "LlmError" in run.error and "no AI model connected" in run.error and chat.payloads == []
    english = _english()
    translated = generate.translate_report(english, _input(), language="cs", settings=llm_settings)
    assert not translated.ok and "no AI model connected" in translated.error and chat.payloads == []


def test_an_own_server_needs_no_key(monkeypatch, llm_settings):
    chat = FakeChat()
    monkeypatch.setattr("src.llm.client", lambda settings: chat)
    llm_settings.llm.api_key, llm_settings.llm.url = None, "http://fides-llm.cofidis.local/v1"
    assert generate.generate_report(_input(), file="x", settings=llm_settings).ok and len(chat.payloads) == 1


def test_a_call_above_the_cost_cap_is_an_error_not_a_report(llm_settings):
    llm_settings.llm.max_cost_usd = 0.50
    assert _run(FakeAsk(cost=0.40), settings=llm_settings).ok
    run = _run(FakeAsk(cost=0.60), settings=llm_settings)
    assert not run.ok and "above llm.max_cost_usd" in run.error and run.cost_usd == 0.60
    english = _english()
    expensive = FakeAsk(CZECH, cost=0.60)
    run = generate.translate_report(english, _input(), language="cs", ask=expensive, settings=llm_settings)
    assert not run.ok and "above llm.max_cost_usd" in run.error


def test_the_rating_is_the_rules_not_the_models():
    answer = {**ANSWER, "rating": {"value": "low", "reasoning": "x"}}
    run = _run(FakeAsk(answer))
    assert run.ok and run.report.rating.value == "high"
    assert any("the rules give 'high'" in w for w in run.warnings)


def test_no_rating_stays_none_even_when_the_model_writes_none():
    data = _input("26701529_2024")  # no scores and no red or amber ratios (Power Reality has 3 amber: R9 rates it)
    for value in (None, "none", "null"):
        answer = {**ANSWER, "rating": {"value": value, "reasoning": "Not possible."}}
        run = _run(FakeAsk(answer), answer_data=data)
        assert run.ok and run.report.rating.value is None and not run.warnings


def test_bad_answers_become_errors_not_exceptions():
    assert "ValidationError" in (_run(FakeAsk({"summary": "only"})).error or "")
    assert _run(FakeAsk()).report is not None

    def refused(system, user):
        raise LlmError("the answer of the model is not a JSON object: Firma je zdravá.")

    assert "LlmError" in (_run(refused).error or "")


def test_markdown_for_the_reviewers():
    data = _input()
    run = _run(FakeAsk(), answer_data=data)
    text = generate.report_markdown(run, data)
    assert text.startswith("# Mondi Štětí a.s.") and "Risk rating (by rules R2): high risk" in text
    assert "## Red flags" in text and "## Review (fill in)" in text and "- none" in text  # risks is empty
    failed = generate.ReportRun(
        file="x", model="m", language="en", prompt_version="v", rating=rate(data), error="boom"
    )
    assert "**No report:** boom" in generate.report_markdown(failed, data)


def test_sentences_from_the_model_are_tidied():
    answer = {**ANSWER, "data_quality_note": " overall data quality is good.", "risks": ["  cash fell."]}
    run = _run(FakeAsk(answer))
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
    assert "not all in the safe zone" in medium.reasoning
    assert "loss" in rate(_case("safe", "safe", profit=-1.0)).reasoning
    assert "check failed" in rate(_case("safe", "safe", passed=False)).reasoning


class FakeGeminiClient:
    def __init__(self, reject_temperature=False, text=None):
        self.calls, self.reject = [], reject_temperature
        self.text = json.dumps(ANSWER) if text is None else text
        self.max_retries, self.retry_statuses = 6, (429, 503)

    def generate(self, model, body):
        from src.extraction.vision import ApiError

        self.calls.append((model, body))
        if self.reject and "temperature" in body["generationConfig"]:
            raise ApiError(400, "temperature is not supported")
        return {
            "usageMetadata": {"promptTokenCount": 800, "candidatesTokenCount": 100, "thoughtsTokenCount": 50},
            "candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": self.text}]}}],
        }


def _script():
    import importlib.util

    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location("generate_reports", root / "scripts" / "generate_reports.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_gemini_is_a_flag_of_the_script_only(monkeypatch):
    """`scripts/generate_reports.py --gemini` tunes the prompt with a free key; the settings and `generate` have no
    provider, so the pipeline and the app can never send statements to Google."""
    from src.settings import ReportSettings

    script, client = _script(), FakeGeminiClient(reject_temperature=True)
    monkeypatch.setattr(script, "make_client", lambda provider: client)
    reply = script.gemini_ask("gemini-test")("system", "user")
    assert reply.data == ANSWER and reply.model == "gemini-test" and reply.usage.cost_usd == 0.0
    assert (reply.usage.input_tokens, reply.usage.output_tokens) == (800, 150)  # thinking tokens count as output
    assert len(client.calls) == 2  # the model rejected temperature=0: asked again without it
    assert client.max_retries == script.MAX_RETRIES and client.retry_statuses == (429,)  # a 503 is not retried
    run = _run(script.gemini_ask("gemini-test"))
    assert run.ok and run.model == "gemini-test" and run.cost_usd == 0.0
    assert "provider" not in ReportSettings.model_fields and "model" not in ReportSettings.model_fields
    assert "gemini" not in Path(generate.__file__).read_text(encoding="utf-8").lower().replace("--gemini", "")


def test_the_gemini_flag_reports_a_bad_answer_as_an_error(monkeypatch):
    script = _script()
    monkeypatch.setattr(script, "make_client", lambda provider: FakeGeminiClient(text="not json"))
    assert "JSONDecodeError" in (_run(script.gemini_ask("m")).error or "")


def test_review_pack_collects_the_saved_reports(tmp_path):
    data = _input()
    for stem, with_report in (("a_2025", True), ("b_2025", False)):
        report = _run(FakeAsk(), answer_data=data)
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
        run = _run(FakeAsk(), answer_data=data)
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
    assert rate(data).rules_version == "proposal-2"


def test_a_rule_can_be_switched_off():
    from src.report.rating import RatingConfig

    decision = rate(_case("safe", "safe", equity=-5.0), RatingConfig(disabled_rules=["R1"]))
    assert (decision.value, decision.rules) == ("low", ["R5"])
    assert decision.rules_version == "proposal-2+custom"


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
    assert (report.language, report.worsening_pct) == ("cs", 20)
    assert not hasattr(report, "provider") and not hasattr(report, "model")  # the model is `llm` (CIPW-58)
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
    run = generate.generate_report(data, file="x", ask=FakeAsk())
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
    run = _run(FakeAsk(), answer_data=data)
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
    run = _run(FakeAsk(), answer_data=data, language="cs")
    page = generate.report_markdown(run, data)
    for text in (
        "## Shrnutí",
        "## Doporučení",
        "Hodnocení rizika",
        "Klíčové údaje",
        "Běžná likvidita",
        "Modely bankrotu",
    ):
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


def test_prompt_v7_keeps_the_placeholders_and_asks_for_czech_numbers():
    text = generate.build_prompt("cs", generate.PROMPT_PATH)
    assert "mld. Kč" in text and "Write all text in cs" in text
    assert "do not mention a recommendation" in text
    for rule in ("not reported", "peak liquidity", "across zero", "month as a word"):
        assert rule in text


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
    return _run(FakeAsk())


def test_numbers_are_compared_as_values_not_as_written():
    assert generate.numbers_in(["1391.2 % and 238.17 million"]) == generate.numbers_in(["1 391,2 % a 238,17 mil."])
    assert generate.numbers_in(["74.8 days"]) != generate.numbers_in(["74,9 dne"])


def test_a_translation_keeps_the_analysis_and_the_code_sets_rating_and_recommendation():
    english = _english()
    ask = FakeAsk(CZECH)
    run = generate.translate_report(english, _input(), language="cs", ask=ask)
    assert run.ok and run.language == "cs" and run.report.language == "cs"
    assert run.report.rating.value == english.report.rating.value == "high"  # the model's "low" is not kept
    assert run.report.recommendation.label == "Nedoporučeno"
    assert run.warnings == []  # every number of the English text is in the Czech one
    assert "+" in run.prompt_version and english.prompt_version in run.prompt_version
    assert json.loads(ask.calls[0][1])["summary"] == english.report.summary  # the user message


def test_a_translation_with_a_changed_number_is_a_warning():
    changed = {**CZECH, "summary": "Mondi Štětí a.s. má celková aktiva 29,6 mld. Kč."}
    run = generate.translate_report(_english(), _input(), language="cs", ask=FakeAsk(changed))
    assert run.ok and any("numbers differ" in w and "29.5" in w and "29.6" in w for w in run.warnings)


def test_a_translation_with_other_number_of_items_is_an_error():
    changed = {**CZECH, "risks": ["Nové riziko."]}
    run = generate.translate_report(_english(), _input(), language="cs", ask=FakeAsk(changed))
    assert not run.ok and "number of items in risks" in run.error


def test_nothing_to_translate_is_an_error_not_a_crash():
    english = _english()
    english.report = None
    run = generate.translate_report(english, _input(), language="cs", ask=FakeAsk(CZECH))
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
    run = _run(FakeAsk(), answer_data=data)
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
    run = _run(FakeAsk(), answer_data=data, language="cs")
    document, paragraphs = _docx_text(export_docx.build_docx([(run, data), (run, data)], tmp_path / "r.docx"))
    assert "Hodnocení rizika (podle pravidel R2): vysoké riziko" in paragraphs and "Doporučení" in paragraphs
    assert document.tables[0].rows[0].cells[0].text == "položka" and len(document.tables) == 8
    assert sum(1 for p in document.paragraphs if 'w:br w:type="page"' in p._p.xml) == 1


def test_the_word_export_needs_something_to_write(tmp_path):
    import pytest as _pytest

    from src.report import export_docx

    with _pytest.raises(ValueError):
        export_docx.build_docx([], tmp_path / "r.docx")


def test_the_czech_title_writes_the_date_the_czech_way():
    from src.report.texts import period_date

    assert period_date("cs", "2025-12-31") == "31. 12. 2025"
    assert period_date("cs", "2024-01-05") == "5. 1. 2024"
    assert period_date("en", "2025-12-31") == "2025-12-31"
    assert period_date("cs", "unknown") == "unknown"


def test_the_translation_prompt_keeps_the_czech_terms_of_the_review():
    text = generate.TRANSLATION_PROMPT_PATH.read_text(encoding="utf-8")
    assert generate.TRANSLATION_PROMPT_PATH.name == "translation_v4.md"
    for term in ("neuvedeno", "zóna ohrožení", "modely bankrotu", "částečné hodnocení"):
        assert term in text


# ---------------------------------------------------------------------------------------------- review of 13 Nov


def _with_failed_checks(data: dict) -> dict:
    data = json.loads(json.dumps(data))
    data["validation"] = {
        **data["validation"],
        "passed": False,
        "failed": [
            {"name": "balance_sum", "period": "current", "message": "assets differ from liabilities"},
            {"name": "balance_sum", "period": "prior", "message": "assets differ from liabilities"},
            {"name": "profit_link", "period": "current", "message": "profit differs"},
        ],
        "failed_count": 3,
    }
    return data


def test_failed_accounting_checks_do_not_break_the_page_or_the_word_file(tmp_path):
    from src.report import export_docx
    from src.report.tables import figures_markdown

    data = _with_failed_checks(_input())
    for language in ("en", "cs"):
        page = "\n".join(figures_markdown(data, language))
        assert "balance_sum, profit_link" in page  # each name once
    run = _run(FakeAsk(), answer_data=data)
    assert "balance_sum" in generate.report_markdown(run, data)
    assert export_docx.build_docx([(run, data)], tmp_path / "r.docx").exists()


def test_the_rules_reasoning_replaces_the_models_when_the_rating_is_replaced():
    answer = {**ANSWER, "rating": {"value": "low", "reasoning": "The model argues for a low risk."}}
    run = _run(FakeAsk(answer), answer_data=_input())
    assert run.rating.value == "high" and run.report.rating.value == "high"
    assert run.report.rating.reasoning == run.rating.reasoning
    assert any("the rules give 'high'" in w for w in run.warnings)


def test_the_number_check_sees_the_minus_sign_and_the_narrow_space():
    from collections import Counter

    assert generate.numbers_in(["a loss of -5.2 million"]) != generate.numbers_in(["ztráta 5,2 mil."])
    assert generate.numbers_in(["-5.2 %"]) == generate.numbers_in(["\u22125,2 %"])
    assert generate.numbers_in(["1 391.2 %"]) == generate.numbers_in(["1\u202f391,2 %"])
    assert generate.numbers_in(["period 2024-12-31"]) == Counter({"2024": 1, "12": 1, "31": 1})


def test_the_report_says_the_llm_wrote_it_and_the_model_is_not_asked_for_it():
    assert AssessmentReport.model_fields["source"].default == "llm"
    assert "source" not in generate.build_prompt("en").split("## Answer")[1]


def test_the_rules_table_has_r7_and_r8_with_the_partial_rating():
    from src.report.rating import rules_table

    assert "R7" not in rules_table() and "R8" not in rules_table()
    assert "R7" in rules_table(True) and "R8" in rules_table(True)


def test_settings_have_one_report_section():
    from src.settings import Settings

    assert list(Settings.model_fields).count("report") == 1
