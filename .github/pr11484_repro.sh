#!/usr/bin/env bash
# PR 11484 real-container probe: run.sh -> real docker -> unsloth/unsloth:latest
set -uo pipefail
LABEL="${1:?label}"
OUT="evidence/$LABEL"; mkdir -p "$OUT"
export UNSLOTH_GPUS=none UNSLOTH_WORKDIR="$PWD" UNSLOTH_STUDIO_VOLUME= \
       UNSLOTH_LMSTUDIO_DIR=none UNSLOTH_OLLAMA_DIR=none UNSLOTH_HERMES_DIR=none
wait_http() { local url=$1 t=$2 code=000; for _ in $(seq "$t"); do
    code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 2 "$url" || true)
    [[ "$code" != 000 ]] && break; sleep 1; done; echo "$code"; }

# C1: JUPYTER_PORT=9000 + SKIP_NOTEBOOK_REFRESH=1 + SKIP_GPU_CHECK=1
JUPYTER_PORT=9000 UNSLOTH_SKIP_NOTEBOOK_REFRESH=1 UNSLOTH_SKIP_GPU_CHECK=1 \
UNSLOTH_PORTS="-p 9000:9000 -p 8888:8888 --name c1" \
    bash docker/run.sh > "$OUT/c1.log" 2>&1 &
for _ in $(seq 60); do docker inspect c1 >/dev/null 2>&1 && break; sleep 1; done
: > "$OUT/c1_git_procs.txt"
( for _ in $(seq 90); do docker top c1 -eo args 2>/dev/null | grep -E '(^| )git (ls-remote|clone|fetch)' >> "$OUT/c1_git_procs.txt"; sleep 1; done ) &
POLL=$!
c9000=$(wait_http http://127.0.0.1:9000/lab 240)
c8888=$(curl -s -o /dev/null -w '%{http_code}' --max-time 3 http://127.0.0.1:8888/lab || true)
wait $POLL
docker exec c1 printenv > "$OUT/c1_env.txt" 2>&1
listen=$(docker exec c1 bash -lc "ss -ltnp 2>/dev/null | grep -Eo ':(8888|9000) ' | sort -u | tr -d ' ' | paste -sd,") || listen="?"
warn_cpu=$(grep -c "UNSLOTH_ALLOW_CPU=1 and no GPU visible" "$OUT/c1.log" || true)
git_procs=$(sort -u "$OUT/c1_git_procs.txt" | grep -c . || true)
docker rm -f c1 >/dev/null 2>&1; sleep 3

# C2: UNSLOTH_SKIP_NOTEBOOK_SYNC=1
UNSLOTH_SKIP_NOTEBOOK_SYNC=1 UNSLOTH_PORTS="-p 8888:8888 --name c2" \
    bash docker/run.sh > "$OUT/c2.log" 2>&1 &
c2_8888=$(wait_http http://127.0.0.1:8888/lab 240); sleep 20
docker exec c2 printenv > "$OUT/c2_env.txt" 2>&1
nb_dir=$(docker exec c2 bash -c 'test -e /workspace/unsloth-notebooks && echo present || echo absent')
nb_count=$(docker exec c2 bash -c 'find /workspace/unsloth-notebooks -name "*.ipynb" 2>/dev/null | wc -l')
view_dir=$(docker exec c2 bash -c 'test -e "/workspace/Unsloth Notebooks" && echo present || echo absent')
docker rm -f c2 >/dev/null 2>&1

has() { grep -q "^$2=" "$OUT/$1" && echo yes || echo no; }
cat > "$OUT/facts.json" <<J
{"label":"$LABEL","run_sh_sha":"$(git hash-object docker/run.sh)",
 "c1":{"JUPYTER_PORT_in_container":"$(has c1_env.txt JUPYTER_PORT)","SKIP_REFRESH_in_container":"$(has c1_env.txt UNSLOTH_SKIP_NOTEBOOK_REFRESH)","SKIP_GPU_CHECK_in_container":"$(has c1_env.txt UNSLOTH_SKIP_GPU_CHECK)",
       "http_9000_lab":"$c9000","http_8888_lab":"$c8888","listening":"$listen","cpu_gate_warn_lines":$warn_cpu,"git_refresh_procs_seen":$git_procs},
 "c2":{"SKIP_SYNC_in_container":"$(has c2_env.txt UNSLOTH_SKIP_NOTEBOOK_SYNC)","http_8888_lab":"$c2_8888","unsloth_notebooks_dir":"$nb_dir","ipynb_count":$nb_count,"view_dir":"$view_dir"}}
J
cat "$OUT/facts.json"
