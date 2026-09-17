import copy
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.inference.llama_cpp import _MAX_TOOL_CALLS_PER_TURN
from core.inference.safetensors_agentic import run_safetensors_tool_loop
from test_llama_cpp_tool_loop import _backend_and_payloads, _done, _record_tool_calls, _sse
from test_safetensors_tool_loop import FakeExecuteTool, _collect_events

N = 10


def _call(i):
    return '<tool_call>{"name":"web_fetch","arguments":{"url":"https://example.com/p%d"}}</tool_call>' % i


def _report(label, executed, messages):
    notices = [m for m in messages if "more tool call(s)" in (m.get("content") or "")]
    print(f"\n[{label}] requested={N} executed={len(executed)} cap={_MAX_TOOL_CALLS_PER_TURN}")
    print(f"[{label}] executed urls={[a.get('url') for a in executed]}")
    print(f"[{label}] roles sent on next model call={[m['role'] for m in messages]}")
    print(f"[{label}] skipped-call notices={len(notices)}")
    for m in notices:
        print(f"[{label}] notice role={m['role']} content={json.dumps(m['content'])}")
    return notices


def test_gguf_over_cap_calls_are_reported_to_model(monkeypatch):
    streams = [
        [_sse({"content": "".join(_call(i) for i in range(N))}), _done()],
        [_sse({"content": "done"}), _done()],
    ]
    backend, payloads = _backend_and_payloads(monkeypatch, streams)
    calls = _record_tool_calls(monkeypatch, "OK")
    list(
        backend.generate_chat_completion_with_tools(
            messages = [{"role": "user", "content": "fetch 10 pages"}],
            tools = [{"type": "function", "function": {"name": "web_fetch"}}],
            max_tool_iterations = 2,
        )
    )
    notices = _report("GGUF", [a for _n, a in calls], payloads[1]["messages"])
    assert len(calls) == _MAX_TOOL_CALLS_PER_TURN
    assert len(notices) == 1, "PROBE FAIL: skipped calls were not reported to the model"
    for i in range(_MAX_TOOL_CALLS_PER_TURN, N):
        assert f"p{i}" in notices[0]["content"]
    print("[GGUF] PROBE PASS")


def test_safetensors_over_cap_calls_are_reported_to_model():
    seen = []
    turns = iter(["".join(_call(i) for i in range(N)), "final"])

    def _gen(messages):
        seen.append(copy.deepcopy(messages))
        yield next(turns)

    exec_fn = FakeExecuteTool(["r"] * N)
    _collect_events(
        run_safetensors_tool_loop(
            single_turn = _gen,
            messages = [{"role": "user", "content": "fetch 10 pages"}],
            tools = [{"type": "function", "function": {"name": "web_fetch"}}],
            execute_tool = exec_fn,
            max_tool_iterations = 2,
        )
    )
    notices = _report("Safetensors", [a for _n, a in exec_fn.calls], seen[1])
    assert len(exec_fn.calls) == _MAX_TOOL_CALLS_PER_TURN
    assert len(notices) == 1, "PROBE FAIL: skipped calls were not reported to the model"
    for i in range(_MAX_TOOL_CALLS_PER_TURN, N):
        assert f"p{i}" in notices[0]["content"]
    print("[Safetensors] PROBE PASS")


def test_gguf_final_turn_notice_placement(monkeypatch):
    from core.inference.tool_call_parser import BUDGET_EXHAUSTED_NUDGE

    blocks = "".join(_call(i) for i in range(_MAX_TOOL_CALLS_PER_TURN + 1))
    blocks += '<tool_call>{"name":"python","arguments":{"code":"print(1)"}}</tool_call>'
    streams = [
        [_sse({"content": blocks}), _done()],
        [_sse({"content": "done"}), _done()],
    ]
    backend, payloads = _backend_and_payloads(monkeypatch, streams)
    _record_tool_calls(monkeypatch, "OK")
    list(
        backend.generate_chat_completion_with_tools(
            messages = [{"role": "user", "content": "fetch 9 pages and run code"}],
            tools = [
                {"type": "function", "function": {"name": "web_fetch"}},
                {"type": "function", "function": {"name": "python"}},
            ],
            max_tool_iterations = 1,
        )
    )
    messages = payloads[1]["messages"]
    print("\n[Final turn] roles sent on the final pass=%s" % [m["role"] for m in messages])
    print("[Final turn] tools offered=%s" % payloads[1].get("tools"))
    print("[Final turn] last message role=%s content=%s"
          % (messages[-1]["role"], json.dumps(messages[-1].get("content"))))
    notices = [m for m in messages if "more tool call(s)" in (m.get("content") or "")]
    assert len(notices) == 1
    assert notices[0]["role"] == "user", "notice folded into a tool result it is not about"
    assert "Call them again" not in notices[0]["content"], "asks for a retry with no tools left"
    assert BUDGET_EXHAUSTED_NUDGE in notices[0]["content"], "budget nudge split into a second user turn"
    assert not any(
        a.get("role") == "user" and b.get("role") == "user" for a, b in zip(messages, messages[1:])
    ), "two user turns in a row"
    print("[Final turn] PROBE PASS")
