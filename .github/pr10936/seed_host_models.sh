#!/usr/bin/env bash
# Seeds the runner's home the way real LM Studio, Hermes and Ollama installs lay out models.
set -euo pipefail

HF=https://huggingface.co/unsloth/SmolLM2-135M-Instruct-GGUF/resolve/main
fetch() {
    mkdir -p "$(dirname "$2")"
    curl -fsSL --retry 5 -o "$2" "$HF/$1"
}

fetch SmolLM2-135M-Instruct-Q4_K_M.gguf \
    "$HOME/.lmstudio/models/lmstudio-community/SmolLM2-135M-Instruct-GGUF/SmolLM2-135M-Instruct-Q4_K_M.gguf"
fetch SmolLM2-135M-Instruct-Q2_K.gguf "$HOME/.hermes/models/SmolLM2-135M-Instruct-Q2_K.gguf"
fetch SmolLM2-135M-Instruct-Q3_K_M.gguf "$HOME/gguf-models/SmolLM2-135M-Instruct-Q3_K_M.gguf"

curl -fsSL https://ollama.com/install.sh | sh
sudo systemctl stop ollama || true
nohup ollama serve >"$RUNNER_TEMP/ollama.log" 2>&1 &
for _ in $(seq 60); do ollama list >/dev/null 2>&1 && break; sleep 2; done
ollama pull smollm:135m
ollama list
pkill -f "ollama serve" || true

find "$HOME/.lmstudio" "$HOME/.hermes" "$HOME/gguf-models" "$HOME/.ollama/models/manifests" -type f | sort
