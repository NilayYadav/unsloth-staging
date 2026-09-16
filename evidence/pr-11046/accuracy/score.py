"""Score recorded Notion MCP calls: before (full listing) vs after (compact listing)."""
import argparse
import glob
import json
import os
import re

from jsonschema import FormatChecker
from jsonschema.validators import validator_for

HERE = os.path.dirname(os.path.abspath(__file__))
MISSING = object()


def load_catalog(path):
    return {t["name"]: t["inputSchema"] for t in json.load(open(path))}


def resolve(args, path):
    node = args
    if not path:
        return node
    for part in path.split("."):
        if isinstance(node, dict) and part in node:
            node = node[part]
        elif isinstance(node, list) and part.isdigit() and int(part) < len(node):
            node = node[int(part)]
        else:
            return MISSING
    return node


def _norm_id(value):
    return re.sub(r"[^0-9a-f]", "", str(value).lower())


def check(args, spec):
    value = resolve(args, spec["path"])
    op, want = spec["op"], spec.get("value")
    if op == "absent":
        return value is MISSING or value is None
    if value is MISSING:
        return False
    if op == "exists":
        return value is not None
    if op == "equals":
        return type(value) is type(want) and value == want
    if op == "num_equals":
        return isinstance(value, (int, float)) and not isinstance(value, bool) and value == want
    if op == "lower_contains":
        return isinstance(value, str) and want.lower() in value.lower()
    if op == "contains_id":
        items = value if isinstance(value, list) else [value]
        return any(isinstance(v, str) and _norm_id(want) in _norm_id(v) for v in items)
    if op == "json_contains":
        return want.lower() in json.dumps(value, ensure_ascii = False).lower()
    if op == "in":
        return value in want
    raise ValueError(f"unknown op {op}")


def schema_errors(catalog, name, args):
    schema = catalog.get(name)
    if schema is None:
        return ["unknown tool"]
    cls = validator_for(schema)
    return [e.message[:160] for e in cls(schema, format_checker = FormatChecker()).iter_errors(args)]


def call_score(catalog, task, call):
    tool_ok = call["name"] == task["expected_tool"]
    errors = schema_errors(catalog, call["name"], call.get("arguments") or {})
    checks = [check(call.get("arguments") or {}, spec) for spec in task["checks"]]
    return {
        "tool_correct": tool_ok,
        "schema_valid": not errors,
        "schema_errors": errors[:3],
        "checks_passed": tool_ok and all(checks),
        "failed_checks": [s["path"] + " " + s["op"] for s, ok in zip(task["checks"], checks) if not ok],
        "strict": tool_ok and not errors and all(checks),
    }


def score_task(catalog, task, record):
    calls = [c for c in record.get("calls") or [] if c.get("name") != "mcp_tool_schema"]
    out = {
        "id": task["id"],
        "control": bool(task.get("control")),
        "n_calls": len(calls),
        "no_call": not calls,
        "schema_tool_calls": record.get("schema_tool_calls", 0),
        "tool_errors": len(record.get("tool_errors") or []),
        "error": record.get("error"),
        "wall_s": record.get("wall_s"),
    }
    first = call_score(catalog, task, calls[0]) if calls else None
    for key in ("tool_correct", "schema_valid", "checks_passed", "strict"):
        out[key] = bool(first and first[key])
    out["first_call"] = {"name": calls[0]["name"], "arguments": calls[0].get("arguments")} if calls else None
    out["first_detail"] = {k: first[k] for k in ("schema_errors", "failed_checks")} if first else None
    out["any_correct"] = any(call_score(catalog, task, c)["strict"] for c in calls)
    return out


def pct(rows, key):
    return f"{100 * sum(bool(r[key]) for r in rows) / len(rows):.0f}%" if rows else "-"


def summarize(runs, tasks):
    lines = [
        "| model | side | tasks | tool | schema-valid | args | strict (first call) | any call correct | no call | used mcp_tool_schema | tool errors | strict compacted | strict controls | prompt tokens |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for run in runs:
        rows = run["scored"]
        comp = [r for r in rows if not r["control"]]
        ctl = [r for r in rows if r["control"]]
        tok = (run.get("count_tokens") or {}).get("input_tokens", "-")
        lines.append(
            f"| {run['model'].split('/')[-1]} | {run['side']} | {len(rows)} | {pct(rows, 'tool_correct')} | {pct(rows, 'schema_valid')} | "
            f"{pct(rows, 'checks_passed')} | {pct(rows, 'strict')} | {pct(rows, 'any_correct')} | {pct(rows, 'no_call')} | "
            f"{pct([dict(r, used=r['schema_tool_calls'] > 0) for r in rows], 'used')} | {sum(r['tool_errors'] for r in rows)} | "
            f"{pct(comp, 'strict')} | {pct(ctl, 'strict')} | {tok} |"
        )
    diffs = ["", "| model | task | before strict / any | after strict / any | before first call | after first call |", "|---|---|---|---|---|---|"]
    by = {(r["model"], r["side"]): {s["id"]: s for s in r["scored"]} for r in runs}
    for model in dict.fromkeys(r["model"] for r in runs):
        b, a = by.get((model, "before")), by.get((model, "after"))
        if not (a and b):
            continue
        for t in tasks:
            x, y = b.get(t["id"]), a.get(t["id"])
            if not (x and y) or (x["strict"], x["any_correct"]) == (y["strict"], y["any_correct"]):
                continue
            fmt = lambda s: "no call" if not s["first_call"] else f"`{s['first_call']['name']}` " + ("; ".join(s["first_detail"]["failed_checks"] + s["first_detail"]["schema_errors"][:1]) or "ok")
            diffs.append(f"| {model.split('/')[-1]} | {t['id']} | {x['strict']} / {x['any_correct']} | {y['strict']} / {y['any_correct']} | {fmt(x)} | {fmt(y)} |")
    return "\n".join(lines + (diffs if len(diffs) > 3 else ["", "No per-task disagreements between sides."]))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", default = os.path.join(HERE, "out"))
    ap.add_argument("--catalog", default = os.path.join(HERE, "notion_tools.json"))
    ap.add_argument("--tasks", default = os.path.join(HERE, "tasks.json"))
    args = ap.parse_args()
    catalog = load_catalog(args.catalog)
    tasks = json.load(open(args.tasks))
    runs = []
    for path in sorted(glob.glob(os.path.join(args.runs, "run_*.json"))):
        run = json.load(open(path))
        recs = {r["id"]: r for r in run["tasks"]}
        run["scored"] = [score_task(catalog, t, recs[t["id"]]) for t in tasks if t["id"] in recs]
        runs.append(run)
    runs.sort(key = lambda r: (r["model"], r["side"] != "before"))
    table = summarize(runs, tasks)
    json.dump([{k: v for k, v in r.items() if k != "tasks"} for r in runs], open(os.path.join(args.runs, "scored.json"), "w"), indent = 1)
    open(os.path.join(args.runs, "summary.md"), "w").write(table + "\n")
    print(table)


if __name__ == "__main__":
    main()
