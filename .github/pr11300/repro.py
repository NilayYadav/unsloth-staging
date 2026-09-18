"""Exercise the real recipe validator and HF seed reader without model inference."""
import copy
import json
import os
import sys
from pathlib import Path

repo = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(repo / "studio/backend"))
from models.data_recipe import RecipePayload
from routes.data_recipe.validate import validate
from core.data_recipe.service import preview_recipe

recipe = {
    "seed_config": {
        "source": {
            "seed_type": "hf",
            "path": "datasets/lhoestq/demo1/data/train.csv",
            "endpoint": None,
        },
        "sampling_strategy": "ordered",
    },
    "columns": [{"column_type": "expression", "name": "result", "expr": "verified", "dtype": "str"}],
}
payload = RecipePayload(recipe=copy.deepcopy(recipe))
response = validate(payload)
facts = {"valid": response.valid, "errors": [e.message for e in response.errors],
         "endpoint_after_validation": payload.recipe["seed_config"]["source"]["endpoint"]}
print("VALIDATION_FACTS=" + json.dumps(facts), flush=True)
assert response.valid, "HF seed with browser-default endpoint=None must validate"
assert payload.recipe["seed_config"]["source"]["endpoint"] == os.environ.get("HF_ENDPOINT", "https://huggingface.co")
rows, _, _ = preview_recipe(payload.recipe, num_records=2)
assert len(rows) == 2 and all(row["result"] == "verified" for row in rows)
print("PASS real HF seed: valid=true; generated_rows=2; result=verified", flush=True)
