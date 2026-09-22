import ast
import glob
import inspect
import json
import sys

import vllm
from vllm import SamplingParams


def load_helper():
    src = open("unsloth/models/rl_replacements.py").read()
    node = next((n for n in ast.parse(src).body if isinstance(n, ast.FunctionDef) and n.name == "grpo_update_SamplingParams"), None)
    origin = "unsloth/models/rl_replacements.py (local helper)"
    if node is None:
        path = glob.glob(sys.argv[1] + "/unsloth_zoo/rl_replacements.py")[0]
        node = next(n for n in ast.parse(open(path).read()).body if isinstance(n, ast.FunctionDef) and n.name == "grpo_update_SamplingParams")
        origin = "unsloth_zoo/rl_replacements.py (RL_REPLACEMENTS, used by main)"
    ns = {"inspect": inspect}
    exec(compile(ast.Module(body = [node], type_ignores = []), "helper", "exec"), ns)
    return ns["grpo_update_SamplingParams"], origin


helper, origin = load_helper()
# generation_kwargs exactly as trl 0.24 GRPOTrainer builds them for colocate vLLM
generation_kwargs = {"n": 1, "repetition_penalty": 1.0, "temperature": 1.0, "top_p": 1.0, "top_k": -1,
                     "min_p": 0.0, "max_tokens": 1024, "truncate_prompt_tokens": None, "guided_decoding": None, "logprobs": 0}
# vllm_sampling_params from the Unsloth GRPO notebooks
notebook = SamplingParams(min_p = 0.1, top_p = 1.0, top_k = -1, seed = 3407, stop = ["<|im_end|>"], include_stop_str_in_output = True)
final = SamplingParams(**helper(SamplingParams, generation_kwargs, notebook))
facts = {f: getattr(final, f) for f in ("min_p", "include_stop_str_in_output", "stop", "seed", "n", "temperature", "max_tokens", "logprobs")}
print("vllm", vllm.__version__, "| helper:", origin)
print("FACTS " + json.dumps(facts))
ok = True
for name, got, want in (("min_p", final.min_p, 0.1), ("include_stop_str_in_output", final.include_stop_str_in_output, True),
                        ("stop", final.stop, ["<|im_end|>"]), ("seed", final.seed, None), ("n", final.n, 1),
                        ("temperature", final.temperature, 1.0), ("max_tokens", final.max_tokens, 1024)):
    status = "PASS" if got == want else "FAIL"
    ok &= got == want
    print(f"{status} {name}: got {got!r}, want {want!r}")
sys.exit(0 if ok else 1)
