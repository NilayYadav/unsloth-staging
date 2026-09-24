import json
import os
import sys

from anthropic import Anthropic

BASE = os.environ["BASE_URL"]
client = Anthropic(
    base_url = BASE,
    api_key = "unused",
    default_headers = {"Authorization": f"Bearer {os.environ['TOKEN']}"},
)

SCHEMA = {
    "type": "object",
    "properties": {"name": {"type": "string"}, "year": {"type": "integer"}},
    "required": ["name", "year"],
    "additionalProperties": False,
}
PROMPT = "Name one famous scientist and the year they were born."
FMT = {"type": "json_schema", "schema": SCHEMA}


def check(label, text):
    print(f"[{label}] reply: {text!r}")
    try:
        obj = json.loads(text)
    except Exception as e:
        print(f"[{label}] FAIL: reply is not JSON ({type(e).__name__})")
        return False
    ok = (
        isinstance(obj, dict)
        and set(obj) == {"name", "year"}
        and isinstance(obj["name"], str)
        and isinstance(obj["year"], int)
    )
    print(f"[{label}] {'PASS' if ok else 'FAIL'}: parsed={obj!r}")
    return ok


def create(body_key, fmt, stream):
    kwargs = dict(
        model = "default",
        max_tokens = 128,
        messages = [{"role": "user", "content": PROMPT}],
        temperature = 0.0,
        extra_body = {"seed": 3407, "enable_thinking": False, body_key: fmt},
    )
    if stream:
        parts = []
        with client.messages.stream(**kwargs) as s:
            for t in s.text_stream:
                parts.append(t)
            final = s.get_final_message()
        return "".join(parts), final.stop_reason
    msg = client.messages.create(**kwargs)
    return "".join(b.text for b in msg.content if b.type == "text"), msg.stop_reason


results = {}
for body_key, fmt in (("output_config", {"format": FMT}), ("output_format", FMT)):
    for stream in (False, True):
        label = f"{body_key}{' stream' if stream else ''}"
        text, stop = create(body_key, fmt, stream)
        print(f"[{label}] stop_reason={stop}")
        results[label] = check(label, text)

# The SDK's own structured-output helper, when this SDK version ships it.
if hasattr(client.messages, "parse"):
    from pydantic import BaseModel

    class Scientist(BaseModel):
        name: str
        year: int

    try:
        parsed = client.messages.parse(
            model = "default",
            max_tokens = 128,
            messages = [{"role": "user", "content": PROMPT}],
            temperature = 0.0,
            output_format = Scientist,
            extra_body = {"seed": 3407, "enable_thinking": False},
        )
        print(f"[messages.parse] parsed_output={parsed.parsed_output!r}")
        results["messages.parse"] = parsed.parsed_output is not None
    except Exception as e:
        print(f"[messages.parse] FAIL: {type(e).__name__}: {str(e)[:300]}")
        results["messages.parse"] = False
else:
    print("[messages.parse] not available in this SDK version")

# Control: no format requested, reply stays free text on both sides.
msg = client.messages.create(
    model = "default",
    max_tokens = 64,
    messages = [{"role": "user", "content": PROMPT}],
    temperature = 0.0,
    extra_body = {"seed": 3407, "enable_thinking": False},
)
print(f"[control no-format] reply: {''.join(b.text for b in msg.content if b.type == 'text')!r}")

# Client tool + format: tools must stay callable, request must still succeed.
msg = client.messages.create(
    model = "default",
    max_tokens = 64,
    messages = [{"role": "user", "content": "What's the weather in Paris? Use the tool."}],
    tools = [
        {
            "name": "get_weather",
            "description": "Get weather for a city",
            "input_schema": {
                "type": "object",
                "properties": {"city": {"type": "string"}},
                "required": ["city"],
            },
        }
    ],
    temperature = 0.0,
    extra_body = {"seed": 3407, "enable_thinking": False, "output_config": {"format": FMT}},
)
print(f"[tools+format] status=200 stop_reason={msg.stop_reason} blocks={[b.type for b in msg.content]}")

print("SUMMARY", json.dumps(results))
sys.exit(0 if all(results.values()) else 1)
