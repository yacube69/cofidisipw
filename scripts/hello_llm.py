"""Smoke test: one LLM API call.

Usage:
    python scripts/hello_llm.py
Requires ANTHROPIC_API_KEY in .env (see .env.example).
"""

from __future__ import annotations

import os
import sys

from dotenv import load_dotenv

DEFAULT_MODEL = "claude-sonnet-5-5"


def main() -> int:
    load_dotenv()
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        print("ERROR: ANTHROPIC_API_KEY is not set. Copy .env.example to .env and fill it in.")
        return 1

    import anthropic

    model = os.getenv("LLM_MODEL") or DEFAULT_MODEL
    client = anthropic.Anthropic(api_key=api_key)
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
