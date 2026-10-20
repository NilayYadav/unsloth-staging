import hashlib
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

VERSION = "9999.12.31"
LOG = os.environ.get("E2E_BACKEND_LOG") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "backend-requests.jsonl")
token = os.environ.get("UNSLOTH_STUDIO_DESKTOP_OWNER_TOKEN", "")
OWNER = {"kind": "tauri", "token_sha256": hashlib.sha256(token.encode()).hexdigest()}
ROOT_ID = os.path.expanduser("~/.unsloth/studio/share/studio_install_id")


def root_id():
    try:
        with open(ROOT_ID, encoding = "utf-8") as handle:
            return handle.read().strip()
    except OSError:
        return ""


def desktop_secret():
    try:
        with open(os.path.expanduser("~/.unsloth/studio/auth/.desktop_secret"), encoding = "utf-8") as handle:
            return handle.read().strip()
    except OSError:
        return ""


def record(method, path):
    try:
        with open(LOG, "a", encoding = "utf-8") as handle:
            handle.write(json.dumps({"t": time.time(), "method": method, "path": path}) + "\n")
    except OSError:
        pass


DEVICE = {"index": 0, "index_kind": "physical", "name": "E2E GPU", "memory_total_gb": 24, "vram_free_gb": 22}

GET = {
    "/api/system": {
        "platform": sys.platform, "python_version": "3.12", "device_backend": "cuda", "uptime_seconds": 1,
        "cpu": {"logical_count": 8, "physical_count": 4, "usage_percent": 1, "frequency_mhz": 3000},
        "memory": {"total_gb": 32, "available_gb": 24, "percent_used": 25, "process_used_mb": 128},
        "disk": {"total_gb": 100, "free_gb": 80, "percent_used": 20},
        "gpu": {"available": True, "backend": "cuda", "devices": [DEVICE]},
        "inference_gpu": {"available": True, "backend": "cuda", "devices": [DEVICE]},
        "ml_packages": {},
    },
    "/api/inference/images/status": {"loaded": False, "repo_id": None, "family": None, "base_repo": None, "device": None, "dtype": None, "model_kind": None, "workflows": []},
    "/api/inference/images/load-progress": {"phase": None, "bytes_downloaded": 0, "bytes_total": 0, "error": None},
    "/api/inference/images/generate-progress": {"active": False, "step": 0, "total_steps": 0, "eta_seconds": None},
    "/api/inference/images/info": {"families": []},
    "/api/inference/images/gallery": {"images": [], "has_more": False},
    "/api/inference/monitor": {"status": "idle", "active_model": None, "active_requests": 0, "entries": []},
    "/api/models/diffusion-loras": {"loras": []},
    "/api/models/diffusion-controlnets": {"controlnets": []},
    "/api/models/list": {"models": [], "default_models": []},
    "/api/models/loras": {"loras": [], "outputs_dir": "/tmp/outputs"},
    "/api/hub/local": {"models_dir": "/tmp/models", "lmstudio_dirs": [], "models": []},
    "/api/hub/cached-gguf": {"cached": []},
    "/api/hub/cached-models": {"cached": []},
    "/api/hub/hidden-models": {"patterns": []},
    "/api/hub/active-downloads": {"downloads": []},
    "/api/hub/datasets/active-downloads": {"downloads": []},
    "/api/settings/personalization": {
        "version": 1,
        "profile": {"displayName": "", "nickname": "", "avatarDataUrl": None, "avatarShape": "circle", "showGreetingSloth": True},
        "appearance": {"theme": "light", "palette": "standard", "language": None, "customization": {}},
        "saved": False, "customizationSaved": False, "paletteSaved": False, "greetingSlothSaved": False,
    },
    "/api/export/status": {"current_checkpoint": None, "is_vision": False, "is_peft": False, "is_export_active": False},
    "/api/chat/threads": {"threads": []},
    "/api/chat/projects": {"projects": []},
    "/api/inference/status": {"loaded": False, "active_model": None, "model": None, "status": "idle", "models": []},
}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        return

    def cors(self):
        origin = self.headers.get("Origin") or "tauri://localhost"
        self.send_header("Access-Control-Allow-Origin", origin)
        self.send_header("Access-Control-Allow-Credentials", "true")
        self.send_header("Access-Control-Allow-Headers", self.headers.get("Access-Control-Request-Headers") or "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, PATCH, DELETE, OPTIONS")

    def send_json(self, payload, status = 200):
        raw = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.cors()
        self.end_headers()
        self.wfile.write(raw)

    def do_OPTIONS(self):
        record("OPTIONS", urlparse(self.path).path)
        self.send_response(204)
        self.cors()
        self.end_headers()

    def do_GET(self):
        path = urlparse(self.path).path
        record("GET", path)
        if path in ("/api/liveness", "/api/health"):
            return self.send_json({
                "status": "alive", "service": "Unsloth UI Backend", "version": VERSION,
                "desktop_protocol_version": 1, "desktop_manageability_version": 2,
                "supports_desktop_auth": True, "supports_desktop_backend_ownership": True,
                "studio_root_id": root_id(), "desktop_owner": OWNER,
                "device_type": "cuda", "chat_only": False, "hardware_detecting": False,
                "native_path_leases_supported": True,
            })
        if path in GET:
            return self.send_json(GET[path])
        return self.send_json({})

    def do_POST(self):
        path = urlparse(self.path).path
        length = int(self.headers.get("content-length", "0") or 0)
        raw = self.rfile.read(length) if length else b""
        record("POST", path)
        if path == "/api/auth/desktop-login":
            try:
                secret = json.loads(raw or b"{}").get("secret", "")
            except ValueError:
                secret = ""
            if not secret or secret != desktop_secret():
                return self.send_json({"detail": "Invalid desktop secret"}, 401)
            return self.send_json({"access_token": "e2e-access", "refresh_token": "e2e-refresh", "token_type": "bearer", "must_change_password": False})
        if path == "/api/auth/refresh":
            return self.send_json({"access_token": "e2e-access", "refresh_token": "e2e-refresh", "token_type": "bearer", "must_change_password": False})
        return self.send_json({})

    do_PUT = do_POST
    do_PATCH = do_POST


def watch_parent():
    if not sys.platform.startswith("linux"):
        return
    import ctypes
    import signal
    ctypes.CDLL("libc.so.6").prctl(1, signal.SIGTERM)


def main(port):
    watch_parent()
    server = ThreadingHTTPServer(("127.0.0.1", int(port)), Handler)
    print(f"TAURI_PORT={server.server_port}", flush = True)
    server.serve_forever()


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "8888")
