#!/usr/bin/env python3
"""Live smoke test for a local LM Studio OpenAI-compatible server."""

from __future__ import annotations

import argparse
import sys

from clients.lmstudio import ChatMessage, LMStudioClient, LMStudioError, SUPPORTED_MODELS
from orchestrator.config import get_settings


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke test LM Studio chat completions")
    parser.add_argument(
        "--model",
        default="qwen3-coder-next",
        choices=sorted(SUPPORTED_MODELS),
        help="Local model id to invoke",
    )
    parser.add_argument(
        "--prompt",
        default="Reply with exactly: lmstudio-ok",
        help="User prompt to send",
    )
    args = parser.parse_args()

    settings = get_settings()
    print(f"LM Studio base URL: {settings.lmstudio_base_url}")
    print(f"Model: {args.model}")

    try:
        with LMStudioClient(settings.lmstudio_base_url, timeout_sec=60.0) as client:
            content = client.chat_completion(
                model=args.model,
                messages=[ChatMessage(role="user", content=args.prompt)],
                temperature=0.0,
            )
    except LMStudioError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1

    print("Response:")
    print(content.strip())
    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
