import hashlib
import json
import os
import random
import sys
import tempfile
from types import SimpleNamespace as NS

tree, server, out = sys.argv[1:4]
sys.path.insert(0, tree)
os.environ["UNSLOTH_STUDIO_HOME"] = tempfile.mkdtemp()
os.environ["UNSLOTH_STUDIO_ALLOW_STDIO_MCP"] = "1"

from mcp import types as mt  # noqa: E402

from core.inference import mcp_client  # noqa: E402
from core.inference.mcp_client import _flatten_result  # noqa: E402
from core.inference.tool_loop_controller import strip_result_for_model  # noqa: E402

records = {}


def rec(key, value):
    records[key] = value


STDIO_TOOLS = [
    "t_dict", "t_list", "t_str", "t_int", "t_float", "t_bool", "t_none", "t_model", "t_dataclass",
    "t_big", "t_block_lookalike", "t_structured_only", "t_text_and_structured",
    "t_empty_text_structured", "t_text_resource", "t_resource_link", "t_multi_text", "t_error",
]
for tool in STDIO_TOOLS:
    try:
        flat = mcp_client.call_tool_sync(f"{sys.executable} {server}", None, tool, {}, timeout = 90)
    except Exception as exc:  # recorded, so both sides must fail the same way
        flat = f"EXCEPTION {type(exc).__name__}: {exc}"
    rec(f"stdio:{tool}", flat)
    rec(f"stdio_model:{tool}", strip_result_for_model(flat))

rng = random.Random(10940)
KEYS = ["type", "data", "blob", "resource", "mimeType", "mime_type", "uri", "content", "rows",
        "transcript", "_meta", "annotations", "x", "ünï", ""]
STRINGS = ["", "image", "audio", "resource", "text", "UklGRiQAAABXQVZF", "iVBORw0KGgo=",
           "JVBERi0xLjQK", "hello", "ünïcödé ✓", "a\nb", "\x1b[1mbold\x1b[0m", "__MCP_IMAGES__:[]",
           "Error: nope", "file:///out/x.png", "data:image/png;base64,iVBORw0KGgo="]


def value(depth):
    roll = rng.random()
    if depth <= 0 or roll < 0.35:
        return rng.choice([rng.choice(STRINGS), rng.randint(-5, 10**6), rng.random(), True, False, None])
    if roll < 0.7:
        return {rng.choice(KEYS): value(depth - 1) for _ in range(rng.randint(0, 5))}
    return [value(depth - 1) for _ in range(rng.randint(0, 5))]


def block():
    kind = rng.randrange(4)
    if kind == 0:
        return mt.TextContent(type = "text", text = rng.choice(STRINGS))
    if kind == 1:
        return mt.ResourceLink(type = "resource_link", uri = "file:///out/data.csv",
                               name = rng.choice(["data.csv", "gen.png", "r"]), mimeType = rng.choice([None, "text/csv", "image/png"]))
    if kind == 2:
        return mt.EmbeddedResource(type = "resource", resource = mt.TextResourceContents(
            uri = rng.choice(["file:///out/log.txt", "file:///out/gen.png"]),
            mimeType = rng.choice([None, "text/plain", "application/json"]), text = rng.choice(STRINGS)))
    return NS(type = "text", text = rng.choice(STRINGS))


FUZZ = 30000
for i in range(FUZZ):
    blocks = [block() for _ in range(rng.randint(0, 4))]
    structured = value(4) if rng.random() < 0.8 else None
    result = NS(content = blocks, structured_content = structured, is_error = rng.random() < 0.15)
    flat = _flatten_result(result)
    rec(f"fuzz:{i}", flat)
    rec(f"fuzz_model:{i}", strip_result_for_model(flat))

# positive controls: attachments are expected to differ, proving the comparison is not blind
WAV = "UklGRiQAAABXQVZFZm10IBAAAAABAAEAQB8AAEAfAAABAAgAZGF0YQAAAAA="
audio = mt.AudioContent(type = "audio", data = WAV, mimeType = "audio/wav")
pdf = mt.EmbeddedResource(type = "resource", resource = mt.BlobResourceContents(
    uri = "file:///out/r.pdf", mimeType = "application/pdf", blob = "JVBERi0xLjQK"))
rec("control:audio_only", _flatten_result(NS(content = [audio], structured_content = None, is_error = False)))
rec("control:pdf_only", _flatten_result(NS(content = [pdf], structured_content = None, is_error = False)))
rec("control:filesystem_mirror", _flatten_result(NS(content = [audio], structured_content = {
    "content": [{"type": "audio", "data": WAV, "mimeType": "audio/wav"}]}, is_error = False)))

with open(out, "w") as fh:
    json.dump({k: {"sha256": hashlib.sha256(v.encode("utf-8", "surrogatepass")).hexdigest(),
                   "len": len(v), "head": v[:240]} for k, v in records.items()}, fh)
print(f"{tree}: {len(records)} outputs", flush = True)
