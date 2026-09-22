import json, sys
sys.path.insert(0, "studio/backend")
from datasets import Dataset
from transformers import AutoTokenizer
from utils.datasets import format_and_template_dataset

MODEL = "unsloth/Qwen2.5-0.5B-Instruct"
tok = AutoTokenizer.from_pretrained(MODEL)
PROMPT = "Classify the sentiment of the review as pos or neg."
LM = {"label": {"0": "neg", "1": "pos"}}
cases = {
    "label_names+system_prompt": {"text": "instruction", "label": "output", "__label_mapping": LM, "__system_prompt": PROMPT},
    "system_prompt_only": {"text": "instruction", "label": "output", "__system_prompt": PROMPT},
    "plain_int_label": {"text": "instruction", "label": "output"},
}
out = {}
for name, mapping in cases.items():
    ds = Dataset.from_dict({"text": ["Loved it", "Hated it"], "label": [1, 0]})
    r = format_and_template_dataset(ds, MODEL, tok, format_type="alpaca", custom_format_mapping=mapping)
    cols = r["dataset"].column_names or []
    texts = list(r["dataset"]["text"]) if r["success"] and "text" in cols else []
    responses = [t.split("### Response:\n", 1)[1].replace(tok.eos_token or "", "") for t in texts]
    out[name] = {
        "success": r["success"], "final_format": r["final_format"], "errors": r["errors"],
        "responses": responses,
        "system_prompt_rows": sum(PROMPT in t for t in texts), "rows": len(texts),
    }
    print(f"REPRO {name}: success={r['success']} final_format={r['final_format']} "
          f"responses={responses} system_prompt_rows={out[name]['system_prompt_rows']}/{len(texts)} errors={r['errors']}")

checks = [
    ("label names train as pos/neg", out["label_names+system_prompt"]["responses"] == ["pos", "neg"]),
    ("system prompt kept with label names", out["label_names+system_prompt"]["system_prompt_rows"] == 2),
    ("system prompt kept without label names", out["system_prompt_only"]["system_prompt_rows"] == 2),
    ("integer 0 label not blank", out["plain_int_label"]["responses"] == ["1", "0"]),
]
failed = [n for n, ok in checks if not ok]
for n, ok in checks:
    print(("PASS " if ok else "FAIL ") + n)
sys.exit(1 if failed else 0)
