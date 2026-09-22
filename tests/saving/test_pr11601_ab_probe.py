import hashlib
import json
import os

import torch
from safetensors.torch import load_file

import unsloth.save as save_mod

TENSOR = "model.layers.0.mlp.down_proj.weight"


def _snapshot(folder):
    out = {}
    for name in sorted(os.listdir(folder)):
        path = os.path.join(folder, name)
        with open(path, "rb") as f:
            out[name] = (hashlib.sha256(f.read()).hexdigest()[:12], os.stat(path).st_mtime_ns)
    return out


def test_pr11601_full_finetune_gguf_reads_trained_weights(monkeypatch, tmp_path):
    from transformers import AutoTokenizer, LlamaConfig, LlamaForCausalLM

    tokenizer = AutoTokenizer.from_pretrained("HuggingFaceTB/SmolLM2-135M")
    config = LlamaConfig(
        vocab_size = len(tokenizer),
        hidden_size = 64,
        intermediate_size = 128,
        num_hidden_layers = 2,
        num_attention_heads = 4,
        num_key_value_heads = 4,
        max_position_embeddings = 128,
        torch_dtype = "float16",
    )
    torch.manual_seed(0)
    source = tmp_path / "local_checkpoint"
    LlamaForCausalLM(config).to(torch.float16).save_pretrained(source)
    tokenizer.save_pretrained(source)
    before = _snapshot(source)
    original = load_file(str(source / "model.safetensors"))[TENSOR].float()

    model = LlamaForCausalLM.from_pretrained(str(source), torch_dtype = torch.float32)
    model._unsloth_full_finetuning = True
    model.train()
    optimizer = torch.optim.AdamW(model.parameters(), lr = 1e-2)
    batch = tokenizer(["Unsloth full finetune GGUF probe."] * 4, return_tensors = "pt")
    training_mode = "sft_forward_backward"
    try:
        for _ in range(5):
            loss = model(**batch, labels = batch["input_ids"]).loss
            loss.backward()
            optimizer.step()
            optimizer.zero_grad()
    except Exception as e:
        training_mode = f"manual_update ({type(e).__name__})"
        with torch.no_grad():
            for p in model.parameters():
                p.add_(torch.randn_like(p) * 0.05)
    model.to(torch.float16)
    trained = model.state_dict()[TENSOR].float().cpu()

    seen = {}

    def _capture_save_to_gguf(**kwargs):
        seen["model_directory"] = kwargs["model_directory"]
        converter_input = load_file(os.path.join(kwargs["model_directory"], "model.safetensors"))
        seen["converter_tensor"] = converter_input[TENSOR].float()
        out = tmp_path / "export" / "model_gguf" / "model.F16.gguf"
        out.parent.mkdir(parents = True, exist_ok = True)
        out.write_bytes(b"GGUF")
        return [str(out)], True, False

    monkeypatch.setattr(save_mod, "save_to_gguf", _capture_save_to_gguf)
    requested = tmp_path / "export" / "model"
    save_mod.unsloth_save_pretrained_gguf(
        model, str(requested), tokenizer = tokenizer, quantization_method = "f16"
    )

    after = _snapshot(source)
    converter = seen["converter_tensor"]
    report = {
        "training_mode": training_mode,
        "requested_output": str(requested),
        "converter_read_from": seen["model_directory"],
        "converter_reads_source_checkpoint": os.path.samefile(seen["model_directory"], source),
        "max_abs_diff_converter_vs_trained": float((converter - trained).abs().max()),
        "max_abs_diff_converter_vs_original": float((converter - original).abs().max()),
        "max_abs_diff_trained_vs_original": float((trained - original).abs().max()),
        "source_files_rewritten": sorted(k for k in before if before[k] != after.get(k)),
        "source_files_added": sorted(set(after) - set(before)),
    }
    print("PR11601_AB " + json.dumps(report, sort_keys = True))

    assert report["max_abs_diff_trained_vs_original"] > 1e-3
    assert report["converter_reads_source_checkpoint"] is False, report
    assert report["max_abs_diff_converter_vs_trained"] == 0.0, report
    assert report["source_files_rewritten"] == [] and report["source_files_added"] == [], report
