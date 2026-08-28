"""PR 9923 repro: validate the repetition-bound limit table against a real llama-server.

Two independent assertions:
  A. Sweep each repetition keyword against llama-server's grammar engine and derive the
     first bound that fails to compile. Compare with _JSON_SCHEMA_REPETITION_LIMITS.
  B. A/B the shipped filter: the raw boundary schema must 400, the filtered copy must 200.

The filter is lifted straight out of the checked-out inference.py by AST, so the code under
test is the PR's own, without importing the 30k-line module.
"""

import ast
import json
import os
import sys
import urllib.error
import urllib.request

BASE = os.environ.get("LLAMA_SERVER", "http://127.0.0.1:8080")
SRC = os.environ.get("INFERENCE_PY", "studio/backend/routes/inference.py")

WANT = (
    "_JSON_SCHEMA_MAP_KEYWORDS",
    "_JSON_SCHEMA_SINGLE_KEYWORDS",
    "_JSON_SCHEMA_LIST_KEYWORDS",
    "_JSON_SCHEMA_REPETITION_LIMITS",
    "_is_json_number",
    "_llama_compatible_tool_schema",
    "_llama_compatible_response_format",
)


def load_filter(path):
    tree = ast.parse(open(path, encoding="utf-8").read())
    picked = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in WANT:
            picked.append(node)
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id in WANT:
                    picked.append(node)
    ns = {"frozenset": frozenset}
    exec(compile(ast.Module(body=picked, type_ignores=[]), path, "exec"), ns)
    missing = [w for w in WANT if w not in ns]
    if missing:
        raise SystemExit(f"FAIL: could not lift {missing} from {path}")
    return ns


def post(endpoint, payload, timeout=120):
    req = urllib.request.Request(
        BASE + endpoint,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode()[:400]
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:400]
    except Exception as e:  # connection died = server killed itself
        return 0, f"{type(e).__name__}: {e}"


def compiles(schema):
    """True when llama-server compiled a grammar for this schema."""
    status, body = post("/completion", {"prompt": "x", "n_predict": 1, "json_schema": schema})
    return status == 200, status, body


def schema_for(keyword, value):
    if keyword in ("maxLength", "minLength"):
        return {"type": "object", "properties": {"f": {"type": "string", keyword: value}},
                "required": ["f"]}
    return {"type": "object",
            "properties": {"f": {"type": "array", "items": {"type": "string"}, keyword: value}},
            "required": ["f"]}


def first_failing(keyword, lo, hi):
    """Lowest value in [lo, hi] whose grammar does not compile, else None."""
    for v in range(lo, hi + 1):
        ok, status, body = compiles(schema_for(keyword, v))
        print(f"    {keyword}={v:<6} -> HTTP {status} {'compiles' if ok else 'REJECTED'}")
        if not ok:
            return v, body
    return None, ""


def main():
    ns = load_filter(SRC)
    limits = ns["_JSON_SCHEMA_REPETITION_LIMITS"]
    tool_filter = ns["_llama_compatible_tool_schema"]
    rf_filter = ns["_llama_compatible_response_format"]
    failures = []

    print(f"== llama-server: {BASE}")
    print(f"== shipped limit table: {dict(sorted(limits.items()))}\n")

    print("== A. derive each keyword's first non-compiling bound")
    for keyword in sorted(limits):
        claimed = limits[keyword]
        print(f"  {keyword}: claimed highest compilable = {claimed}")
        got, body = first_failing(keyword, claimed - 1, claimed + 2)
        if got == claimed + 1:
            print(f"    OK: first failure at {got}, exactly one past the shipped limit\n")
        else:
            failures.append(
                f"{keyword}: shipped limit {claimed} implies first failure at {claimed + 1}, "
                f"observed {got}. body={body[:160]}"
            )
            print(f"    MISMATCH: expected first failure {claimed + 1}, observed {got}\n")

    print("== B. A/B the shipped filter on a boundary-laden schema")
    raw = {
        "type": "object",
        "properties": {
            "a": {"type": "string", "maxLength": limits["maxLength"] + 1},
            "b": {"type": "array", "items": {"type": "string"},
                  "maxItems": limits["maxItems"] + 1},
            "c": {"$ref": "#/$defs/B"},
            "d": {"type": "array", "items": [{"type": "string", "maxLength": 2000}]},
        },
        "$defs": {"B": {"type": "string", "maxLength": 2000}},
        "required": ["a"],
    }
    ok_raw, st_raw, body_raw = compiles(raw)
    ok_fil, st_fil, _ = compiles(tool_filter(raw))
    print(f"  raw      -> HTTP {st_raw} {'compiles' if ok_raw else 'REJECTED'}")
    print(f"  filtered -> HTTP {st_fil} {'compiles' if ok_fil else 'REJECTED'}")
    if ok_raw:
        failures.append("negative branch did not fail: raw boundary schema compiled")
    elif "grammar" not in body_raw.lower():
        failures.append(f"raw schema failed for the wrong reason: {body_raw[:200]}")
    if not ok_fil:
        failures.append("positive branch did not pass: filtered schema still rejected")

    print("\n== C. unsatisfiable pair (min > max) must not reach the server")
    bad = {"type": "object", "properties": {"f": {"type": "string", "minLength": 5,
                                                  "maxLength": 2}}, "required": ["f"]}
    cleaned = tool_filter(bad)
    kept = [k for k in ("minLength", "maxLength") if k in cleaned["properties"]["f"]]
    print(f"  filtered keeps: {kept or 'neither bound'}")
    if kept:
        failures.append(f"descending pair survived the filter: {kept}")
    ok_c, st_c, _ = compiles(cleaned)
    print(f"  filtered -> HTTP {st_c} {'compiles' if ok_c else 'REJECTED'}")
    if not ok_c:
        failures.append("filtered descending-pair schema did not compile")

    print("\n== D. response_format reaches the same engine")
    rf = {"type": "json_schema", "json_schema": {"name": "r", "strict": True,
          "schema": {"type": "object",
                     "properties": {"s": {"type": "string", "maxLength": 2000}},
                     "required": ["s"]}}}
    st_rf_raw, body_rf = post("/v1/chat/completions", {
        "messages": [{"role": "user", "content": "hi"}], "max_tokens": 1, "response_format": rf})
    st_rf_fil, _ = post("/v1/chat/completions", {
        "messages": [{"role": "user", "content": "hi"}], "max_tokens": 1,
        "response_format": rf_filter(rf)})
    print(f"  raw response_format      -> HTTP {st_rf_raw}")
    print(f"  filtered response_format -> HTTP {st_rf_fil}")
    if st_rf_raw == 200:
        failures.append("response_format negative branch did not fail")
    if st_rf_fil != 200:
        failures.append(f"filtered response_format still rejected: HTTP {st_rf_fil}")
    wrapper = rf_filter(rf)["json_schema"]
    if wrapper.get("strict") is not True or wrapper.get("name") != "r":
        failures.append("filter dropped the json_schema wrapper fields")

    print("\n" + "=" * 60)
    if failures:
        print("RESULT: FAIL")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("RESULT: PASS - limit table matches llama-server, A/B proven both directions")
    return 0


if __name__ == "__main__":
    sys.exit(main())
