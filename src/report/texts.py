"""Fixed texts of the report page in English and Czech (CIPW-16).

Everything the code writes itself (headings, table labels, rating and recommendation wording) lives here, so a
language is a dictionary and not a change in the logic. The AI writes the narrative in the chosen language by the
prompt; this module writes the rest. An unknown language falls back to English.
"""

from __future__ import annotations

from typing import Any

LANGUAGES = ("en", "cs")

EN: dict[str, Any] = {
    "no_company": "Unknown company",
    "period_ending": "period ending",
    "no_report": "No report:",
    "rating_line": "Risk rating (by rules {rules}): {rating}",
    "data_quality_input": "Data quality of the input:",
    "quality": {"good": "good", "limited": "limited", "poor": "poor"},
    "summary": "Summary",
    "strengths": "Strengths",
    "risks": "Risks",
    "red_flags": "Red flags",
    "none": "none",
    "rating_reasoning": "Risk rating reasoning",
    "recommendation": "Recommendation",
    "recommendation_note": "Indicative, calculated from the risk rating by fixed rules; the decision is the analyst's.",
    "criteria": "Criteria behind it:",
    "data_quality_note": "Data quality note",
    "footer": "Model {model}, prompt {prompt}, rating rules {rules}.",
    "rating": {"high": "high risk", "medium": "medium risk", "low": "low risk", None: "no rating"},
    "levels": {
        "recommended": "Recommended",
        "caution": "Recommended with caution (further review)",
        "not_recommended": "Not recommended",
        "no_recommendation": "No recommendation (insufficient data)",
    },
    "zones": {"safe": "safe", "grey": "grey zone", "distress": "distress"},
    "not_computable": "not computable",
    "criteria_rule": "Rule {rule} decided: {why}",
    "criteria_model": "{label}: {score} ({zone})",
    "criteria_equity": "Equity: {value} CZK",
    "criteria_profit": "Result for the period: {value} CZK",
    "criteria_checks_ok": "Accounting checks: all {total} passed",
    "criteria_checks_failed": "Accounting checks: {failed} of {total} failed",
    "criteria_flagged": "A score is computed from values flagged for review",
    "figures": "Figures for the reviewer (from the input, not written by the AI)",
    "figures_intro": (
        "To check the text above without searching for the numbers; the web app shows these figures directly. "
        "Amounts in CZK. * = value flagged for review."
    ),
    "key_figures": "Key figures",
    "ratios": "Ratios",
    "bankruptcy": "Bankruptcy models",
    "checks": "Accounting checks",
    "col_line": "line",
    "col_current": "current year",
    "rules_title": "How the risk rating is decided",
    "rules_intro": "The risk rating (high risk is the worst, low risk the best) is calculated from the numbers by "
    "these rules, the first one that applies decides. The AI only writes the text and does not choose it.",
    "rules_header": ["rule", "condition", "risk rating"],
    "rules": {
        "R1": ("equity is negative", "high risk"),
        "R2": ("IN05 and Altman Z′ are both in the distress zone", "high risk"),
        "R3": ("one of the two models is in the distress zone and the period ends with a loss", "high risk"),
        "R7": ("no bankruptcy score could be computed and the current ratio is below 1 "
               "(partial rating from the balance sheet alone)", "high risk"),
        "R8": ("no bankruptcy score could be computed and the current ratio is 1 or more "
               "(partial rating from the balance sheet alone)", "medium risk"),
        "R4": ("no bankruptcy score could be computed (usually no income statement in the filing)", "no rating"),
        "R4_partial": ("no bankruptcy score and no current ratio could be computed", "no rating"),
        "R5": ("both models are in the safe zone, the period is not a loss, all accounting checks passed and no "
               "score is computed from values flagged for review", "low risk"),
        "R6": ("everything else", "medium risk"),
    },
    "col_prior": "prior year",
    "col_change": "change",
    "col_ratio": "ratio",
    "col_formula": "formula",
    "col_model": "model",
    "col_zones": "zones",
    "reasons": {"missing": "not computed", "not_meaningful": "not meaningful", "unreliable": "unreliable"},
    "unreliable_suffix": " (unreliable)",
    "not_computed": "not computed",
    "billion": "billion",
    "million": "million",
    "days": "days",
    "checks_text": "{total} accounting checks, {result}",
    "checks_passed": "all passed",
    "checks_failed": "{count} failed: {names}",
    "review_title": "Review (fill in)",
    "review": [
        "Every number in the text matches the input",
        "Nothing is invented or guessed (causes, industry, plans)",
        "Strengths, risks and red flags are what an analyst would say",
        "The risk rating reasoning is understandable and fits the numbers",
        "The recommendation follows the rating and its criteria are understandable",
        "The data quality note says what an analyst must know",
    ],
    "comments": "Comments:",
    "page": "page",
    "decimal": ".",
    "key_labels": {},  # English labels come with the data
    "ratio_names": {},
    "model_names": {},
}

CS: dict[str, Any] = {
    "no_company": "Neznámá společnost",
    "period_ending": "období končící",
    "no_report": "Bez zprávy:",
    "rating_line": "Rating rizika (podle pravidel {rules}): {rating}",
    "data_quality_input": "Kvalita vstupních dat:",
    "quality": {"good": "dobrá", "limited": "omezená", "poor": "slabá"},
    "summary": "Shrnutí",
    "strengths": "Silné stránky",
    "risks": "Rizika",
    "red_flags": "Varovné signály",
    "none": "žádné",
    "rating_reasoning": "Odůvodnění ratingu rizika",
    "recommendation": "Doporučení",
    "recommendation_note": (
        "Orientační, vypočtené z ratingu rizika podle pevných pravidel; rozhodnutí je na analytikovi."
    ),
    "criteria": "Kritéria:",
    "data_quality_note": "Poznámka ke kvalitě dat",
    "footer": "Model {model}, prompt {prompt}, pravidla ratingu {rules}.",
    "rating": {"high": "vysoké riziko", "medium": "střední riziko", "low": "nízké riziko", None: "bez ratingu"},
    "levels": {
        "recommended": "Doporučeno",
        "caution": "Doporučeno s opatrností (další posouzení)",
        "not_recommended": "Nedoporučeno",
        "no_recommendation": "Bez doporučení (nedostatek dat)",
    },
    "zones": {"safe": "bezpečná zóna", "grey": "šedá zóna", "distress": "zóna ohrožení"},
    "not_computable": "nelze vypočítat",
    "criteria_rule": "Rozhodlo pravidlo {rule}: {why}",
    "criteria_model": "{label}: {score} ({zone})",
    "criteria_equity": "Vlastní kapitál: {value} Kč",
    "criteria_profit": "Výsledek za období: {value} Kč",
    "criteria_checks_ok": "Účetní kontroly: prošly všechny ({total})",
    "criteria_checks_failed": "Účetní kontroly: {failed} z {total} neprošlo",
    "criteria_flagged": "Skóre je vypočteno z hodnot označených ke kontrole",
    "figures": "Čísla pro posuzovatele (ze vstupu, nepsala je umělá inteligence)",
    "figures_intro": (
        "K ověření textu výše bez hledání čísel; webová aplikace zobrazuje tyto údaje přímo. "
        "Částky v Kč. * = hodnota označená ke kontrole."
    ),
    "key_figures": "Klíčové údaje",
    "ratios": "Ukazatele",
    "bankruptcy": "Modely bankrotu",
    "checks": "Účetní kontroly",
    "col_line": "položka",
    "col_current": "aktuální rok",
    "rules_title": "Jak se rozhoduje o hodnocení rizika",
    "rules_intro": "Hodnocení rizika (vysoké riziko je nejhorší, nízké riziko nejlepší) se počítá z čísel podle "
    "těchto pravidel; rozhoduje první pravidlo, které platí. Umělá inteligence pouze píše text a hodnocení nevybírá.",
    "rules_header": ["pravidlo", "podmínka", "hodnocení rizika"],
    "rules": {
        "R1": ("vlastní kapitál je záporný", "vysoké riziko"),
        "R2": ("IN05 i Altman Z′ jsou v zóně ohrožení", "vysoké riziko"),
        "R3": ("jeden ze dvou modelů je v zóně ohrožení a období končí ztrátou", "vysoké riziko"),
        "R7": ("nelze vypočítat skóre bankrotu a běžná likvidita je pod 1 "
               "(částečné hodnocení pouze z rozvahy)", "vysoké riziko"),
        "R8": ("nelze vypočítat skóre bankrotu a běžná likvidita je 1 nebo více "
               "(částečné hodnocení pouze z rozvahy)", "střední riziko"),
        "R4": ("nelze vypočítat skóre bankrotu (obvykle chybí výkaz zisků a ztrát)", "bez hodnocení"),
        "R4_partial": ("nelze vypočítat skóre bankrotu ani běžnou likviditu", "bez hodnocení"),
        "R5": ("oba modely jsou v bezpečné zóně, období nekončí ztrátou, všechny účetní kontroly prošly a žádné "
               "skóre není vypočteno z hodnot označených ke kontrole", "nízké riziko"),
        "R6": ("vše ostatní", "střední riziko"),
    },
    "col_prior": "předchozí rok",
    "col_change": "změna",
    "col_ratio": "ukazatel",
    "col_formula": "vzorec",
    "col_model": "model",
    "col_zones": "zóny",
    "reasons": {"missing": "nevypočteno", "not_meaningful": "nemá smysl", "unreliable": "nespolehlivé"},
    "unreliable_suffix": " (nespolehlivé)",
    "not_computed": "nevypočteno",
    "billion": "mld.",
    "million": "mil.",
    "days": "dne",
    "checks_text": "Účetní kontroly: {total}, {result}",
    "checks_passed": "všechny prošly",
    "checks_failed": "{count} neprošlo: {names}",
    "review_title": "Posouzení (vyplňte)",
    "review": [
        "Každé číslo v textu odpovídá vstupu",
        "Nic není vymyšleno ani odhadnuto (příčiny, odvětví, plány)",
        "Silné stránky, rizika a varovné signály odpovídají tomu, co by řekl analytik",
        "Odůvodnění ratingu je srozumitelné a odpovídá číslům",
        "Doporučení vychází z ratingu a jeho kritéria jsou srozumitelná",
        "Poznámka ke kvalitě dat říká, co musí analytik vědět",
    ],
    "comments": "Poznámky:",
    "page": "strana",
    "decimal": ",",
    "key_labels": {
        "total_assets": "Aktiva celkem",
        "fixed_assets": "Dlouhodobý majetek",
        "current_assets": "Oběžná aktiva",
        "inventories": "Zásoby",
        "receivables_short_term": "Krátkodobé pohledávky",
        "cash": "Peněžní prostředky",
        "equity": "Vlastní kapitál",
        "external_sources": "Cizí zdroje (závazky a rezervy)",
        "liabilities_long_term": "Dlouhodobé závazky",
        "liabilities_short_term": "Krátkodobé závazky",
        "bank_loans_long_term": "Dlouhodobé bankovní úvěry",
        "bank_loans_short_term": "Krátkodobé bankovní úvěry",
        "revenue": "Tržby (výrobků, služeb a zboží)",
        "net_turnover": "Čistý obrat (uvedený na konci výkazu zisku a ztráty)",
        "operating_result": "Provozní výsledek hospodaření",
        "financial_result": "Finanční výsledek hospodaření",
        "profit_before_tax": "Výsledek hospodaření před zdaněním",
        "profit_for_period": "Výsledek hospodaření za účetní období",
        "profit_current_period": "Výsledek za období (řádek A.V. rozvahy; ve výkazu není výkaz zisku a ztráty)",
    },
    "ratio_names": {
        "current_ratio": "Běžná likvidita",
        "quick_ratio": "Pohotová likvidita",
        "cash_ratio": "Okamžitá likvidita",
        "roa": "Rentabilita aktiv (ROA)",
        "roe": "Rentabilita vlastního kapitálu (ROE)",
        "ros": "Rentabilita tržeb (ROS)",
        "ebitda_margin": "Marže EBITDA",
        "debt_ratio": "Celková zadluženost",
        "debt_to_equity": "Zadluženost vůči vlastnímu kapitálu",
        "interest_coverage": "Úrokové krytí",
        "receivables_days": "Doba obratu pohledávek",
        "payables_days": "Doba obratu závazků",
    },
    "model_names": {
        "in05": (
            "IN05 (Neumaierová & Neumaier)",
            "nad 1,6 firma tvoří hodnotu (bezpečná), 0,9–1,6 šedá zóna, pod 0,9 hodnotu ničí (ohrožení)",
        ),
        "altman_z_prime": (
            "Altman Z′ (soukromé firmy)",
            "nad 2,9 bezpečná, 1,23–2,9 šedá zóna, pod 1,23 ohrožení",
        ),
    },
}

_TEXTS = {"en": EN, "cs": CS}


def texts(language: str) -> dict[str, Any]:
    """The dictionary of a language ('en' for any language without one)."""
    return _TEXTS.get(language.lower()[:2], EN)


def has_texts(language: str) -> bool:
    return language.lower()[:2] in _TEXTS
