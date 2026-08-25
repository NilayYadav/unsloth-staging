#!/usr/bin/env python3
"""Does arming Deep Research actually offer the model the tool on an external provider?

Replays the exact request body Studio's composer sends for a hosted connection whose models
run Studio tools, with Deep Research armed and the Images pill lit, through the real routing
decision (``studio_tool_loop``) and the real catalog builder (``_select_request_tools``).

Run with ``--negative`` after the fix has been stripped: the tool must be absent, which is
arming Deep Research doing nothing at all.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent / "studio" / "backend"
sys.path.insert(0, str(BACKEND))

from core.inference.providers import provider_model_runs_local_tools  # noqa: E402
from models.inference import ChatCompletionRequest  # noqa: E402
from routes.inference import (  # noqa: E402
    _explicit_studio_tool_loop_requested,
    _select_request_tools,
    _selects_only_provider_hosted_tools,
)

PROVIDERS = ("openai", "gemini", "openrouter", "kimi")


def _composer_body(provider_type: str) -> ChatCompletionRequest:
    """What the composer sends: research armed, only a hosted pill lit beside it."""
    return ChatCompletionRequest(
        model = "external",
        messages = [{"role": "user", "content": "what changed in the EU AI Act?"}],
        provider_id = "saved-1",
        provider_type = provider_type,
        external_model = "gpt-5.4",
        stream = True,
        enable_tools = True,
        enabled_tools = ["image_generation"],
        run_tools_locally = True,
        deep_research_armed = True,
    )


def _offered(payload: ChatCompletionRequest, provider_type: str) -> list[str]:
    takes_loop = (
        provider_model_runs_local_tools(provider_type, payload.external_model)
        and payload.stream is True
        and _explicit_studio_tool_loop_requested(payload)
        and not _selects_only_provider_hosted_tools(payload, provider_type)
    )
    if not takes_loop:
        return []
    tools = asyncio.run(_select_request_tools(payload, tools_on = True, mcp_allowed = False))
    return [tool["function"]["name"] for tool in tools]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--negative", action = "store_true")
    args = parser.parse_args()

    failures = 0
    for provider_type in PROVIDERS:
        offered = _offered(_composer_body(provider_type), provider_type)
        has_tool = "deep_research" in offered
        want = not args.negative
        ok = has_tool is want
        failures += 0 if ok else 1
        print(
            f"{'PASS' if ok else 'FAIL'}  {provider_type}: deep_research offered={has_tool} "
            f"(want {want}); catalog={offered}"
        )

    if args.negative:
        print(
            "\nREPRO"
            if failures == 0
            else "\nREPRO FAILED TO REPRODUCE",
            "the turn proxies through and the model is never shown deep_research,",
            "so arming Deep Research does nothing at all.",
        )
    else:
        print("\nFIXED:" if failures == 0 else "\nSTILL BROKEN:", "the armed turn takes Studio's loop and the tool reaches the model.")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
