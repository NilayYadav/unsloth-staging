#!/usr/bin/env bash
# Drags a filled image slot onto an empty one inside the real desktop shell under Xvfb.
set -u
OUT="${OUT:-$PWD/slot-drag-out}"
mkdir -p "$OUT"
EV="$OUT/events.log"
: > "$EV"

cat > "$OUT/logserver.py" <<'PY'
import http.server, sys, time
class H(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("content-length", 0))).decode()
        with open(sys.argv[1], "a") as f:
            f.write(f"{time.time():.3f} {body}\n")
        self.send_response(204); self.end_headers()
    def log_message(self, *a): pass
http.server.ThreadingHTTPServer(("127.0.0.1", 8765), H).serve_forever()
PY
python3 "$OUT/logserver.py" "$EV" &

H="$RUNNER_TEMP/home"
mkdir -p "$H/.unsloth/studio/unsloth_studio/bin" "$H/.config" "$H/xdg"
printf '#!/bin/sh\nexec sleep 600\n' > "$H/.unsloth/studio/unsloth_studio/bin/unsloth"
chmod +x "$H/.unsloth/studio/unsloth_studio/bin/unsloth"

Xvfb :99 -screen 0 1440x900x24 -nolisten tcp > "$OUT/xvfb.log" 2>&1 &
for _ in $(seq 1 40); do [ -e /tmp/.X11-unix/X99 ] && break; sleep 0.25; done
export RUSTUP_HOME="${RUSTUP_HOME:-$HOME/.rustup}" CARGO_HOME="${CARGO_HOME:-$HOME/.cargo}"
export DISPLAY=:99 HOME="$H" XDG_RUNTIME_DIR="$H/xdg" XDG_CONFIG_HOME="$H/.config"
chmod 700 "$XDG_RUNTIME_DIR"

npx --prefix "$GITHUB_WORKSPACE/studio" tauri dev \
  --config '{"build":{"devUrl":"http://localhost:5173/smoke-image-slots.html"}}' \
  > "$OUT/tauri-dev.log" 2>&1 &
DEV=$!

for _ in $(seq 1 1500); do
  grep -q '"layout"' "$EV" && break
  kill -0 "$DEV" 2>/dev/null || break
  sleep 1
done
if ! grep -q '"layout"' "$EV"; then
  echo "::error::test page never loaded"; tail -60 "$OUT/tauri-dev.log"; exit 1
fi
sleep 2

WIN=$(xdotool search --onlyvisible --name '^Unsloth$' | head -1)
[ -n "$WIN" ] || WIN=$(xdotool search --onlyvisible --class unsloth | head -1)
echo "window: $WIN"; xdotool getwindowgeometry "$WIN"
eval "$(xdotool getwindowgeometry --shell "$WIN")"
read -r AX AY BX BY < <(python3 - "$EV" "$X" "$Y" <<'PY'
import json, sys
layout = [json.loads(l.split(" ", 1)[1]) for l in open(sys.argv[1]) if '"layout"' in l][-1]
ox, oy = int(sys.argv[2]), int(sys.argv[3])
c = lambda r: (round(ox + r["x"] + r["w"] / 2), round(oy + r["y"] + r["h"] / 2))
print(*c(layout["a"]), *c(layout["b"]))
PY
)
echo "drag ($AX,$AY) -> ($BX,$BY)"
import -window root "$OUT/before.png" || true

xdotool mousemove "$AX" "$AY"; sleep 0.3
xdotool mousedown 1; sleep 0.3
for i in $(seq 1 30); do
  xdotool mousemove $((AX + (BX - AX) * i / 30)) $((AY + (BY - AY) * i / 30)); sleep 0.05
done
sleep 0.5
xdotool mouseup 1
sleep 3
import -window root "$OUT/after.png" || true

echo "--- events ---"; cat "$EV"
if grep -q '"slot": "b", "same": true' "$EV" || grep -q '"slot":"b","same":true' "$EV"; then
  echo "RESULT: slot b received the image"
  echo "copied=1" >> "$GITHUB_OUTPUT"
else
  echo "RESULT: slot b stayed empty"
  echo "copied=0" >> "$GITHUB_OUTPUT"
fi
if grep -q '"toast"' "$EV"; then
  echo "toast=1" >> "$GITHUB_OUTPUT"; grep '"toast"' "$EV"
else
  echo "toast=0" >> "$GITHUB_OUTPUT"
fi
