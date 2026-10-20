import json
import os
import runpy
import sys

FIXTURE = os.environ.get("E2E_BACKEND_FIXTURE") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "backend_fixture.py")
CAPS = {
    "desktop_protocol_version": 1,
    "desktop_manageability_version": 2,
    "supports_api_only": True,
    "supports_provision_desktop_auth": True,
    "supports_desktop_backend_ownership": True,
    "studio_install_ok": True,
    "version": "9999.12.31",
}


def app():
    args = sys.argv[1:]
    log = os.environ.get("E2E_STUB_LOG")
    if log:
        with open(log, "a", encoding = "utf-8") as handle:
            handle.write(json.dumps(args) + "\n")
    if args[:1] in (["-h"], ["--help"]):
        return 0
    if "desktop-capabilities" in args:
        print(json.dumps(CAPS, separators = (",", ":")), flush = True)
        return 0
    if "provision-desktop-auth" in args:
        auth = os.path.expanduser("~/.unsloth/studio/auth")
        os.makedirs(auth, exist_ok = True)
        with open(os.path.join(auth, ".desktop_secret"), "w", encoding = "utf-8") as handle:
            handle.write("e2e-desktop-secret")
        return 0
    if "studio" in args and "--api-only" in args:
        port = "8888"
        for flag in ("-p", "--port"):
            if flag in args:
                port = args[args.index(flag) + 1]
        sys.argv = [FIXTURE, port]
        runpy.run_path(FIXTURE, run_name = "__main__")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(app())
