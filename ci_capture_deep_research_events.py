#!/usr/bin/env python3
"""Capture the real tool events Studio's loop publishes for a deep_research call.

Not a fixture: this drives ``stream_with_studio_tools`` with the real tool catalog and a
scripted model, three ways -- the call runs, the user denies it, the call budget is spent --
and writes the exact ``tool_start`` / ``tool_end`` frames a browser would receive.

Those frames are the whole input to the frontend handoff, so ci_handoff_ab.mjs replays both
implementations against them rather than against events written by hand.
"""

from __future__ import annotations

import asyncio
import json
import sys
import threading
from pathlib import Path

BACKEND = Path(__file__).resolve().parent / "studio" / "backend"
sys.path.insert(0, str(BACKEND))

from core.inference import studio_tool_loop as loop_mod  # noqa: E402
from core.inference.studio_tool_loop import (  # noqa: E402
    ToolLoopPolicy,
    ToolLoopRun,
    stream_with_studio_tools,
)
from core.inference.tools import DEEP_RESEARCH_TOOL, execute_tool  # noqa: E402

QUESTION = "Which small dog breeds suit a flat with no garden?"
_DONE = "data: [DONE]"


def _sse(delta=None, finish=None) -> str:
    choice: dict = {"index": 0, "delta": delta or {}}
    if finish is not None:
        choice["finish_reason"] = finish
    return "data: " + json.dumps({"choices": [choice]})


class ScriptedModel:
    """Calls deep_research on the first turn, then answers."""

    def __init__(self) -> None:
        self.heals_text_tool_calls = True
        self.turns = [
            [
                _sse(
                    {
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": "call_r",
                                "function": {
                                    "name": "deep_research",
                                    "arguments": json.dumps({"question": QUESTION}),
                                },
                            }
                        ]
                    }
                ),
                _sse(finish="tool_calls"),
                _DONE,
            ],
            [_sse({"content": "Looking into it."}), _sse(finish="stop"), _DONE],
        ]

    def stream(self, *, messages, tools, tool_choice, cancel_event):
        lines = self.turns.pop(0) if self.turns else [_DONE]

        async def _gen():
            for line in lines:
                yield line

        return _gen()


def _drive(*, permission_mode: str, verdict: str | None, max_calls: int) -> list[dict]:
    def _execute(name, arguments, **kwargs):
        return execute_tool(name, arguments)

    loop_mod.execute_tool = _execute
    loop_mod.build_rag_autoinject = lambda *a, **k: None
    if verdict is not None:
        loop_mod.begin_tool_decision = lambda *a, **k: object()
        loop_mod.abort_tool_decision = lambda *a, **k: None
        loop_mod.wait_tool_decision = lambda *a, **k: verdict

    async def _collect() -> list[dict]:
        events: list[dict] = []
        async for line in stream_with_studio_tools(
            ScriptedModel(),
            run=ToolLoopRun(
                messages=[{"role": "user", "content": "breeds of dogs"}],
                session_id="s1",
                thread_id="thread-1",
                tool_choice=None,
            ),
            policy=ToolLoopPolicy(
                tools=[DEEP_RESEARCH_TOOL],
                max_calls=max_calls,
                timeout=300,
                permission_mode=permission_mode,
                confirm_calls=permission_mode == "ask",
                bypass_permissions=False,
                rag_scope=None,
            ),
            cancel_event=threading.Event(),
        ):
            if not line.startswith("data: ") or line[6:].strip() == "[DONE]":
                continue
            payload = json.loads(line[6:])
            if payload.get("type") in ("tool_start", "tool_end"):
                events.append(payload)
        return events

    return asyncio.run(_collect())


def main() -> int:
    captured = {
        "ran": _drive(permission_mode="off", verdict=None, max_calls=25),
        "denied": _drive(permission_mode="ask", verdict="deny", max_calls=25),
        "budget_spent": _drive(permission_mode="off", verdict=None, max_calls=0),
    }
    for name, events in captured.items():
        kinds = [f"{e['type']}({e.get('result', '')[:24]!r})" for e in events]
        print(f"captured {name}: {kinds}")
        if not any(e["type"] == "tool_end" for e in events):
            print(f"FAIL: {name} published no tool_end")
            return 1
    # The premise the handoff has to survive: all three close with the same event type.
    assert all(
        any(e["type"] == "tool_end" for e in events) for events in captured.values()
    )
    Path("deep-research-events.json").write_text(json.dumps(captured, indent=1))
    print("PASS: every path -- ran, denied, budget spent -- closes with the same tool_end")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
