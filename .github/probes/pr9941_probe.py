"""PR 9941 reproduction probe: does Claude Code's effort tier reach the template,
and does it leave thinking-off alone?

Wire shapes below are REAL bodies captured from Claude Code 2.1.251 pointed at a
local capture server (the same path `unsloth start` wires up via ANTHROPIC_BASE_URL).

Regimes:
  base   -- upstream main before the PR: the tier never reaches the template.
  prefix -- PR head before the review fix: tier lands, but thinking-off is
            switched back on.
  fixed  -- PR head: tier lands and thinking-off is left alone.
"""

import argparse
import os
import sys
import types

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "studio", "backend"))

from models.inference import AnthropicMessagesRequest  # noqa: E402
from routes.inference import _anthropic_reasoning_args  # noqa: E402
from core.inference.llama_cpp import LlamaCppBackend  # noqa: E402

ADAPTIVE = {"type": "adaptive", "display": "omitted"}

SHAPES = [
    ("A: CC thinking ON,  /effort high", {"output_config": {"effort": "high"}, "thinking": ADAPTIVE}),
    ("B: CC thinking ON,  /effort low", {"output_config": {"effort": "low"}, "thinking": ADAPTIVE}),
    ("C: CC thinking OFF, effort high", {"output_config": {"effort": "high"}}),
    ("D: thinking disabled, effort high", {"output_config": {"effort": "high"},
                                           "thinking": {"type": "disabled"}}),
]

ON_HIGH = {"enable_thinking": True, "reasoning_effort": "high"}
ON_LOW = {"enable_thinking": True, "reasoning_effort": "low"}
ON_ONLY = {"enable_thinking": True}
OFF_ONLY = {"enable_thinking": False}

# Expected chat_template_kwargs for an `enable_thinking_effort` (GLM-style) template.
EXPECTED = {
    "base": {"A": ON_ONLY, "B": ON_ONLY, "C": None, "D": OFF_ONLY},
    "prefix": {"A": ON_HIGH, "B": ON_LOW, "C": ON_HIGH, "D": ON_HIGH},
    "fixed": {"A": ON_HIGH, "B": ON_LOW, "C": None, "D": OFF_ONLY},
}


def backend_stub(style):
    s = types.SimpleNamespace()
    s._supports_reasoning = True
    s._reasoning_always_on = False
    s._reasoning_style = style
    s._reasoning_effort_levels = ["low", "medium", "high", "max", "xhigh"]
    s._supports_preserve_thinking = False
    s._architecture = None
    return s


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--regime", required=True, choices=sorted(EXPECTED))
    a = ap.parse_args()

    expected = EXPECTED[a.regime]
    failures = []
    print(f"regime={a.regime}\n")
    for name, extra in SHAPES:
        key = name.split(":")[0]
        payload = AnthropicMessagesRequest(
            model="m", max_tokens=16, messages=[{"role": "user", "content": "hi"}], **extra
        )
        args = _anthropic_reasoning_args(payload)
        kwargs = LlamaCppBackend._request_reasoning_kwargs(
            backend_stub("enable_thinking_effort"),
            args["enable_thinking"], args["reasoning_effort"], args["preserve_thinking"],
        )
        want = expected[key]
        ok = kwargs == want
        print(f"{'PASS' if ok else 'FAIL'} {name}")
        print(f"      payload.reasoning_effort = {payload.reasoning_effort!r}")
        print(f"      resolved  enable_thinking={args['enable_thinking']!r} "
              f"reasoning_effort={args['reasoning_effort']!r}")
        print(f"      template kwargs = {kwargs!r}")
        if not ok:
            print(f"      EXPECTED        = {want!r}")
            failures.append(name)
        print()

    if failures:
        print(f"REPRO RESULT: FAIL ({len(failures)} shape(s) off expectation): {failures}")
        return 1
    print(f"REPRO RESULT: PASS -- regime '{a.regime}' behaves exactly as documented")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
