"""Smoke test: one call to the language model of the AI report (CIPW-58, docs/llm.md).

Usage:
    python scripts/hello_llm.py
Needs MISTRAL_API_KEY in .env (see .env.example) for Mistral's API, or `llm.url` of a server of one's own in
config/fides.yaml; the model comes from the settings (`llm.model`, or MISTRAL_MODEL in .env).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.llm import LlmError, ask_json  # noqa: E402
from src.settings import get_settings  # noqa: E402


def main() -> int:
    settings = get_settings()
    if settings.offline:
        print("Offline mode (FIDES_OFFLINE / config/fides.yaml): no LLM call made. OK")
        return 0
    if not settings.llm_enabled:
        print("ERROR: MISTRAL_API_KEY is not set. Copy .env.example to .env and fill it in.")
        return 1
    print(f"Asking {settings.llm.model} at {settings.llm.url} ...")
    try:
        reply = ask_json(
            "Odpovídej pouze jedním objektem JSON.",
            'Jaké je hlavní město České republiky? Odpověz ve tvaru {"mesto": "..."}.',
            settings,
            max_tokens=50,
        )
    except LlmError as exc:
        print(f"ERROR: {exc}")
        return 1
    print(f"Model: {reply.model}")
    print(f"Answer: {reply.data}")
    print(f"Tokens: in={reply.usage.input_tokens}, out={reply.usage.output_tokens}, cost {reply.usage.cost_usd} USD")
    print("OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
