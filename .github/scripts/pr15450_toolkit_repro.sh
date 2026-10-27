#!/usr/bin/env bash
# PR 15450 repro: real installer (merge base vs head) on a GPU-less runner, then the printed command with the real image.
set -euo pipefail
MB=78b2f82b3ad8747aa305d9db25b00c4a71382c9c
HEAD_SHA=bd4bd3359aafc0027447be0a4a016ec45241099a
NEWPW=Repro-pass-15450x
mkdir -p out && cd out

curl -fsSL "https://raw.githubusercontent.com/unslothai/unsloth/${MB}/docker/install_nvidia_toolkit.sh" -o base.sh
curl -fsSL "https://raw.githubusercontent.com/NilayYadav/unsloth/${HEAD_SHA}/docker/install_nvidia_toolkit.sh" -o head.sh
sha256sum base.sh head.sh

for arm in base head; do
    echo "::group::installer $arm"
    sudo -E env UNSLOTH_TOOLKIT_VERIFY=0 bash "$arm.sh" > "${arm}_install.log" 2>&1 || { cat "${arm}_install.log"; echo "INSTALLER_FAILED arm=$arm"; exit 2; }
    cat "${arm}_install.log"
    echo "::endgroup::"
done
docker info 2>/dev/null | grep -i 'Runtimes'

grep '^Done. Run: ' base_install.log | sed 's/^Done. Run: //' > base_cmd.txt
sed -n '/^Done. Start Unsloth with:$/,$p' head_install.log | tail -n +2 > head_cmd.txt
for arm in base head; do
    echo "PRINTED_COMMAND arm=$arm:"; cat "${arm}_cmd.txt"
done

wait_up() {
    for _ in $(seq 1 180); do
        curl -fsS http://localhost:8000/api/health >/dev/null 2>&1 && return 0
        sleep 5
    done
    docker logs --tail 80 "$1"; return 1
}
login() { curl -s -o /dev/null -w '%{http_code}' -H 'Content-Type: application/json' \
    -d "{\"username\":\"unsloth\",\"password\":\"$1\"}" http://localhost:8000/api/auth/login; }

flow() {
    local arm=$1 cmd
    # No GPU on the runner: drop --gpus (latest starts CPU-only) and do not follow the log.
    cmd="$(sed -e 's/ --gpus all//' -e 's/ && docker logs -f unsloth//' "${arm}_cmd.txt")"
    echo "RUN_COMMAND arm=$arm: $cmd"
    docker volume rm -f unsloth-studio unsloth-workspace >/dev/null 2>&1 || true
    local c1 c2 pw tok code
    c1="$(bash -c "$cmd" | tail -1)"
    wait_up "$c1"
    for _ in $(seq 1 60); do docker exec "$c1" test -s /opt/unsloth-studio/auth/.bootstrap_password && break; sleep 5; done
    pw="$(docker exec "$c1" cat /opt/unsloth-studio/auth/.bootstrap_password)"
    tok="$(curl -fsS -H 'Content-Type: application/json' -d "{\"username\":\"unsloth\",\"password\":\"$pw\"}" \
        http://localhost:8000/api/auth/login | python3 -c 'import json,sys;print(json.load(sys.stdin)["access_token"])')"
    code="$(curl -s -o /dev/null -w '%{http_code}' -H 'Content-Type: application/json' -H "Authorization: Bearer $tok" \
        -d "{\"current_password\":\"$pw\",\"new_password\":\"$NEWPW\"}" http://localhost:8000/api/auth/change-password)"
    echo "STEP arm=$arm change_password_http=$code login_new_pw_before_rm=$(login "$NEWPW")"
    docker rm -f "$c1" >/dev/null
    echo "STEP arm=$arm docker_rm_done volumes: $(docker volume ls -q | tr '\n' ' ')"
    c2="$(bash -c "$cmd" | tail -1)"
    wait_up "$c2"
    sleep 20
    echo "STEP arm=$arm second_boot_log:"; docker logs "$c2" 2>&1 | grep -iE 'password|earlier boot' | grep -v "$NEWPW" | head -8 || true
    code="$(login "$NEWPW")"
    echo "RESULT arm=$arm login_new_pw_after_rm=$code"
    echo "$code" > "${arm}_result.txt"
    docker rm -f "$c2" >/dev/null
    docker volume prune -f >/dev/null
}
flow base
flow head

b="$(cat base_result.txt)"; h="$(cat head_result.txt)"
echo "SUMMARY base=$b head=$h"
[[ "$b" == 401 ]] || { echo "VOID: the merge base did not lose the password ($b)"; exit 1; }
[[ "$h" == 200 ]] || { echo "FAIL: the PR command lost the password too ($h)"; exit 1; }
echo "REPRODUCED_AND_FIXED"
