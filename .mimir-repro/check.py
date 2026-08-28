"""Gate the PR 9940 A/B: the served window must be what Claude Code enforces."""
import json, sys
from pathlib import Path

ASSUMED_UNKNOWN_MODEL_WINDOW = 200000


def load(path):
    return json.loads(Path(path).read_text())


def main():
    before, after = load(sys.argv[1]), load(sys.argv[2])
    served = after["server_window"]
    checks = [
        ("before: claude resolves the unknown-model default, not the served window",
         before["resolved_context_window"] == ASSUMED_UNKNOWN_MODEL_WINDOW),
        ("before: a request overruns the served window and the server rejects it",
         len(before["overflow_prompt_tokens"]) > 0),
        ("after: claude resolves the served window",
         after["resolved_context_window"] == served),
        ("after: no request overruns the served window",
         len(after["overflow_prompt_tokens"]) == 0),
    ]
    print(f"served window: {served}")
    for arm in (before, after):
        print(f"\n[{arm['arm']}] CLAUDE_CODE_MAX_CONTEXT_TOKENS={arm['env_max_context_tokens']} "
              f"CLAUDE_CODE_AUTO_COMPACT_WINDOW={arm['env_auto_compact_window']}")
        print(f"[{arm['arm']}] resolved contextWindow: {arm['resolved_context_window']}")
        print(f"[{arm['arm']}] turns completed: {arm['turns_completed']}/{arm['turns_requested']}"
              f"  first failure: turn {arm['first_failed_turn']}")
        print(f"[{arm['arm']}] largest prompt sent: {arm['max_prompt_tokens']} tokens"
              f"  server rejections: {arm['overflow_prompt_tokens']}")
    print()
    failed = [name for name, ok in checks if not ok]
    for name, ok in checks:
        print(f"{'PASS' if ok else 'FAIL'}  {name}")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
