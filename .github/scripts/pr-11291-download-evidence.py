# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026-present the Unsloth AI Inc. team. All rights reserved.
"""Real POSIX downloader/pipeline plus desktop hook/renderer evidence.

The native IPC bridge is simulated; renderer and downloader are unmodified.
Does not install/update a real desktop application.
"""
import argparse
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import queue
import re
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request


def download(repo, out):
    payload = b"x" * (4 * 1024 * 1024)
    release, half = threading.Event(), threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload[:1024 * 1024])
            self.wfile.flush()
            time.sleep(0.7)
            self.wfile.write(payload[1024 * 1024:2 * 1024 * 1024])
            self.wfile.flush()
            half.set()
            release.wait(20)
            self.wfile.write(payload[2 * 1024 * 1024:])

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    source = (repo / "studio/setup.sh").read_text()
    helpers = []
    for name in ("_is_verbose", "_filter_download_output"):
        match = re.search(rf"{name}\(\) \{{.*?\n\}}", source, re.S)
        if match:
            helpers.append(match.group())
    start = source.index('    _NODE_LOG="$(mktemp)"')
    end = source.index("    set -e", start) + len("    set -e")
    with tempfile.TemporaryDirectory(prefix="download-evidence-") as temporary:
        work = Path(temporary)
        destination = work / "node-runtime.tar.gz"
        child = work / "install_node_prebuilt.py"
        child.write_text(
            "import sys\nfrom pathlib import Path\n"
            f"sys.path.insert(0, {str(repo / 'studio')!r})\n"
            "import install_node_prebuilt as installer\n"
            "installer._LOG_TO_STDOUT = True\n"
            "print('resolving release', flush=True)\n"
            f"installer.download_file('http://127.0.0.1:{server.server_port}/archive', Path({str(destination)!r}))\n"
            "print('runtime verified', flush=True)\n"
        )
        script = work / "run.sh"
        script.write_text(
            "\n".join(helpers)
            + '\n_NODE_PY="$1"\nSCRIPT_DIR="$2"\nNODE_DIR="$2"\n'
            + source[start:end]
            + '\ncat "$_NODE_LOG" > "$2/captured.log"\nrm -f "$_NODE_LOG"\nexit "$_NODE_STATUS"\n'
        )
        env = dict(os.environ, UNSLOTH_TAURI_UPDATE="1", UNSLOTH_VERBOSE="0")
        proc = subprocess.Popen(
            ["bash", str(script), sys.executable, str(work)],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env,
        )
        lines = queue.Queue()
        reader = threading.Thread(target=lambda: [lines.put(line.rstrip()) for line in proc.stdout], daemon=True)
        reader.start()
        try:
            assert half.wait(15), "SETUP: HTTP transfer did not reach halfway"
            time.sleep(0.8)
            halfway_lines = []
            while not lines.empty():
                halfway_lines.append(lines.get_nowait())
            facts = {
                "total_bytes": len(payload), "served_bytes_at_snapshot": len(payload) // 2,
                "installer_running_at_snapshot": proc.poll() is None,
                "destination_exists_at_snapshot": destination.exists(),
                "halfway_lines": halfway_lines,
            }
            assert facts["installer_running_at_snapshot"]
            assert not facts["destination_exists_at_snapshot"]
        finally:
            release.set()
            proc.wait(timeout=20)
            reader.join(timeout=5)
            proc.stdout.close()
            server.shutdown()
            server.server_close()
            worker.join(timeout=5)
        final_lines = halfway_lines[:]
        while not lines.empty():
            final_lines.append(lines.get_nowait())
        assert proc.returncode == 0, final_lines
        facts["sha256_matches"] = hashlib.sha256(destination.read_bytes()).digest() == hashlib.sha256(payload).digest()
        assert facts["sha256_matches"]
        facts["final_lines"] = final_lines
        facts["diagnostics_filtered"] = not any("resolving release" in line or "runtime verified" in line for line in final_lines)
        assert facts["diagnostics_filtered"]
        captured = (work / "captured.log").read_text()
        assert "resolving release" in captured and "runtime verified" in captured
        (out / "download.json").write_text(json.dumps(facts, indent=2))
        return facts


BRIDGE = '''
export async function addPluginListener() { throw new Error('Unexpected native plugin listener'); }
const handlers = new Map();
window.__handlers = handlers;
window.__emit = (name, payload) => { for (const f of handlers.get(name) ?? []) f({payload}); };
export async function listen(name, callback) {
  const bucket = handlers.get(name) ?? []; bucket.push(callback); handlers.set(name, bucket);
  return () => handlers.set(name, bucket.filter(f => f !== callback));
}
export async function invoke(name) {
  if (name === 'desktop_update_policy') return {mode:'in_app', releasePageBaseUrl:'',releaseTagPrefix:'v'};
  if (name === 'desktop_update_cleanup_armed') return true;
  if (name === 'check_desktop_update') return {version:'0.1.900',currentVersion:'0.1.899',rawJson:{}};
  if (name === 'start_backend_update') { window.__backendStarted = true; return; }
  if (name === 'desktop_update_bundle_status') return {version:'0.1.900',downloaded:false,downloading:false};
  if (name === 'download_desktop_update') return new Promise(() => {});
  if (name === 'set_renderer_activity') return;
  throw new Error('Unexpected native command: '+name);
}
'''

ENTRY = '''
import React from 'react';
import {createRoot} from 'react-dom/client';
import {UpdateScreen} from '../src/components/tauri/update-screen';
import {useTauriUpdate} from '../src/hooks/use-tauri-update';
import '../src/index.css';
function App() {
  const controller = useTauriUpdate();
  window.__controller = controller;
  if (controller.status === 'idle') return <button onClick={controller.checkForUpdate}>Check for update</button>;
  if (controller.status === 'available') return <button onClick={controller.installUpdate}>Install update</button>;
  return <UpdateScreen status={controller.status} logs={controller.logs} progress={controller.progress}
    error={controller.error} onRetry={controller.retryUpdate} onSkipRestart={controller.skipAndRestart}
    onCopyDiagnostics={controller.copyDiagnostics}/>;
}
createRoot(document.getElementById('root')).render(<App/>);
'''


def browser(repo, out, download_facts):
    from playwright.sync_api import sync_playwright
    frontend = repo / "studio/frontend"
    fixture = frontend / ".review-evidence"
    fixture.mkdir(exist_ok=True)
    (fixture / "bridge.ts").write_text(BRIDGE)
    (fixture / "entry.tsx").write_text(ENTRY)
    (fixture / "index.html").write_text('<html><head><style>html,body,#root{height:100%;margin:0}</style></head><body><div id="root"></div><script>window.__TAURI_INTERNALS__={};</script><script type="module" src="./entry.tsx"></script></body></html>')
    (fixture / "vite.config.ts").write_text('''
import path from 'node:path';
import {mergeConfig} from 'vite';
import original from '../vite.config';
export default mergeConfig(original, {resolve:{alias:{
  '@tauri-apps/api/core':path.resolve(__dirname,'bridge.ts'),
  '@tauri-apps/api/event':path.resolve(__dirname,'bridge.ts')
}}});
''')
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    log = (out / "vite.log").open("w")
    proc = subprocess.Popen(
        ["node", "node_modules/vite/bin/vite.js", "--config", ".review-evidence/vite.config.ts", "--host", "127.0.0.1", "--port", str(port), "--strictPort"],
        cwd=frontend, stdout=log, stderr=subprocess.STDOUT,
    )
    url = f"http://127.0.0.1:{port}/.review-evidence/index.html"
    try:
        for _ in range(150):
            try:
                urllib.request.urlopen(url, timeout=1).close()
                break
            except Exception:
                if proc.poll() is not None:
                    raise RuntimeError((out / "vite.log").read_text())
                time.sleep(0.2)
        else:
            raise RuntimeError("Vite did not become ready")
        with sync_playwright() as p:
            browser = p.chromium.launch()
            context = browser.new_context(viewport={"width":800,"height":650}, device_scale_factor=1)
            page = context.new_page()
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(url, wait_until="domcontentloaded")
            page.get_by_role("button", name="Check for update", exact=True).click()
            page.get_by_role("button", name="Install update", exact=True).click()
            page.get_by_text("Updating backend...", exact=True).wait_for()
            page.wait_for_function("window.__backendStarted && window.__handlers.has('update-progress')")
            for line in download_facts["halfway_lines"]:
                page.evaluate("line => window.__emit('update-progress', line)", line)
            page.wait_for_timeout(900)
            facts = page.evaluate('''() => ({
                status:window.__controller.status,
                rendered_text:document.body.innerText,
                progress_count:document.querySelectorAll('progress').length,
                progress_value:document.querySelector('progress')?.value ?? null,
                progress_label:document.querySelector('progress')?.getAttribute('aria-label') ?? null,
                logs_received:window.__controller.logs.length
            })''')
            page.screenshot(path=str(out / "halfway.png"))
            page.evaluate("window.__emit('update-progress','runtime verified')")
            page.wait_for_timeout(400)
            facts["progress_after_next_step"] = page.locator("progress").count()
            assert facts["progress_after_next_step"] == 0
            if facts["progress_count"]:
                assert facts["progress_value"] == 50
                assert "2.0 MiB/4.0 MiB" in facts["rendered_text"]
                page.evaluate("window.__emit('update-progress', 'Downloading unknown.tar.gz: 2.0 MiB downloaded at 1.0 MiB/s')")
                page.get_by_text("2.0 MiB downloaded at 1.0 MiB/s", exact=True).wait_for()
                facts["unknown_size_bar_count"] = page.locator("progress").count()
                assert facts["unknown_size_bar_count"] == 0
            facts["browser"] = browser.version
            facts["viewport"] = {"width":800,"height":650}
            facts["page_errors"] = errors
            assert not errors, errors
            context.close()
            browser.close()
            return facts
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        log.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--label", required=True)
    parser.add_argument("--assert-progress", action="store_true")
    parser.add_argument("--download-only", action="store_true")
    args = parser.parse_args()
    args.repo, args.out = args.repo.resolve(), args.out.resolve()
    args.out.mkdir(parents=True, exist_ok=True)
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=args.repo, text=True).strip()
    transport = download(args.repo, args.out)
    if args.download_only:
        print(json.dumps({"sha":sha,"download":transport}, indent=2), flush=True)
        if args.assert_progress:
            assert any("50.0%" in line for line in transport["halfway_lines"]), "EXPECTED_REGRESSION_ASSERTION: active 2 MiB/4 MiB download must stream 50% progress"
        return
    ui = browser(args.repo, args.out, transport)
    meta = {"label":args.label,"sha":sha,"download":transport,"ui":ui,
            "boundary":"Real HTTP download and production setup.sh pipeline; production desktop hook and UpdateScreen in Chromium with simulated native IPC. No real desktop binary update."}
    (args.out / "meta.json").write_text(json.dumps(meta, indent=2))
    print(json.dumps(meta, indent=2), flush=True)
    if args.assert_progress:
        assert ui["progress_value"] == 50, "EXPECTED_REGRESSION_ASSERTION: active 2 MiB/4 MiB download must display 50% progress"


if __name__ == "__main__":
    main()
