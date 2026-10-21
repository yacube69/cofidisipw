"""Smoke test: one LLM API call.

Usage:
    python scripts/hello_llm.py
Requires ANTHROPIC_API_KEY in .env (see .env.example); the model comes from the settings (config/fides.yaml).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.settings import get_settings  # noqa: E402


def main() -> int:
    settings = get_settings()
    if settings.offline:
        print("Offline mode (FIDES_OFFLINE / config/fides.yaml): no LLM call made. OK")
        return 0
    if not settings.llm_enabled:
        print("ERROR: ANTHROPIC_API_KEY is not set. Copy .env.example to .env and fill it in.")
        return 1

    import anthropic

    model = settings.llm.model
    client = anthropic.Anthropic(api_key=settings.llm.api_key.get_secret_value())  # type: ignore[union-attr]
    try:
        response = client.messages.create(
            model=model,
            max_tokens=50,
            messages=[{"role": "user", "content": "Odpověz jedním slovem: jaké je hlavní město České republiky?"}],
        )
    except anthropic.AuthenticationError:
        print("ERROR: API key was rejected.")
        return 1
    except anthropic.APIError as exc:
        print(f"ERROR: API call failed: {exc}")
        return 1

    text = "".join(block.text for block in response.content if block.type == "text")
    print(f"Model: {response.model}")
    print(f"Answer: {text.strip()}")
    print(f"Tokens: in={response.usage.input_tokens}, out={response.usage.output_tokens}")
    print("OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
