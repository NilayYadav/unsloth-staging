"""PR 11232 repro probe: Save Steps 0 must not write checkpoints.

Runs a real (tiny, CPU, offline) training run using the TrainingArguments that
Studio's production code builds for Save Steps = 0, then counts checkpoint dirs.
Assertion: zero checkpoint-* directories.
  - on main (no fix): HF defaults to save_strategy="steps"/save_steps=500 -> checkpoints appear -> FAIL
  - with PR 11232:    save_strategy="no"                                  -> none         -> PASS
"""
import importlib
import os
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
os.environ.setdefault("WANDB_DISABLED", "true")

REPO = Path(os.environ.get("PROBE_REPO", Path(__file__).resolve().parent)).resolve()
BACKEND = REPO / "studio" / "backend"
sys.path.insert(0, str(BACKEND))

STUBBED = []


def stub_if_missing(name, attrs):
    if name in sys.modules:
        return
    try:
        importlib.import_module(name)
        return
    except Exception:
        pass
    STUBBED.append(name)
    mod = types.ModuleType(name)
    mod.__spec__ = None
    for attr in attrs:
        setattr(mod, attr, MagicMock())
    sys.modules[name] = mod
    parent, _, child = name.rpartition(".")
    if parent and parent in sys.modules:
        setattr(sys.modules[parent], child, mod)


stub_if_missing("unsloth", ("FastLanguageModel", "FastVisionModel", "is_bfloat16_supported"))
stub_if_missing("unsloth.chat_templates", ("get_chat_template",))
stub_if_missing("trl", ("SFTTrainer", "SFTConfig"))

from core.training import trainer as tmod  # noqa: E402

for _n in reversed(STUBBED):
    sys.modules.pop(_n, None)

import torch  # noqa: E402
from torch.utils.data import Dataset  # noqa: E402
from transformers import GPT2Config, GPT2LMHeadModel, Trainer, TrainingArguments  # noqa: E402

MAX_STEPS = int(os.environ.get("PROBE_MAX_STEPS", "600"))
SEQ = 16


class Tiny(Dataset):
    def __len__(self):
        return 4096

    def __getitem__(self, i):
        ids = torch.randint(0, 96, (SEQ,))
        return {"input_ids": ids, "labels": ids.clone()}


def main():
    out = Path(os.environ.get("PROBE_OUT", "probe_out")).resolve()
    out.mkdir(parents=True, exist_ok=True)

    tmod.should_use_mlx_training_backend = lambda *a, **k: False
    t = tmod.UnslothTrainer()
    t.model_name = "unsloth/csm-1b"

    config = t._build_audio_training_args(
        {"save_steps": 0, "max_steps": MAX_STEPS, "optim": "adamw_torch",
         "warmup_steps": 0, "batch_size": 1, "gradient_accumulation_steps": 1},
        str(out),
    )
    print("PROBE config save_strategy =", repr(config.get("save_strategy")))
    print("PROBE config save_steps    =", repr(config.get("save_steps")))

    config.update(bf16=False, fp16=False, use_cpu=True, report_to=[],
                  logging_steps=200, disable_tqdm=True)
    args = TrainingArguments(**config)
    print("PROBE effective args.save_strategy =", str(args.save_strategy))
    print("PROBE effective args.save_steps    =", args.save_steps)

    model = GPT2LMHeadModel(GPT2Config(vocab_size=96, n_positions=SEQ, n_embd=32,
                                       n_layer=2, n_head=2))
    Trainer(model=model, args=args, train_dataset=Tiny()).train()

    ckpts = sorted(p.name for p in out.glob("checkpoint-*") if p.is_dir())
    print(f"PROBE checkpoint dirs written ({len(ckpts)}): {ckpts}")
    if ckpts:
        print("FAIL: Save Steps 0 still wrote checkpoints -> repro confirmed")
        return 1
    print("PASS: Save Steps 0 wrote no checkpoints")
    return 0


if __name__ == "__main__":
    sys.exit(main())
