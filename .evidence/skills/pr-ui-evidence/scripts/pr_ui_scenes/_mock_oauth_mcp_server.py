#!/usr/bin/env python3
"""A local MCP server behind an OAuth authorization server that enforces Notion's
client-authentication rule.

Run as a subprocess by the ``mcp_oauth_dual_client_auth`` scene:

    python3 _mock_oauth_mcp_server.py --port 9123 --state /abs/state.json \
        --tools search,fetch_page,create_page

Why a local stand-in rather than mcp.notion.com: the real flow needs a human at
Notion's consent screen, which cannot run inside a deterministic scene. Everything
this file reproduces was read off the live server first (see the issue's evidence):

* dynamic client registration assigns ``token_endpoint_auth_method =
  client_secret_basic`` and returns a ``client_secret``;
* ``/token`` answers a request that carries BOTH an ``Authorization`` header and a
  ``client_id`` form field with 400 ``invalid_request`` / "Client must not use
  multiple authentication methods";
* the same request without the body ``client_id`` authenticates normally.

``/authorize`` auto-approves, which is the only piece that is not Notion's
behaviour, and it is the piece a human would otherwise perform.

Every token request is appended to ``--state`` so the scene can report what the
client actually sent as facts, not just what the screenshot shows.
"""

from __future__ import annotations

import argparse
import base64
import json
import secrets
from pathlib import Path
from urllib.parse import unquote, urlencode

import uvicorn
from fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse
from starlette.routing import Route

STATE_PATH: Path
BASE_URL: str
CLIENTS: dict[str, dict] = {}
CODES: dict[str, dict] = {}
TOKENS: set[str] = set()


def _record(event: dict) -> None:
    events = []
    if STATE_PATH.exists():
        events = json.loads(STATE_PATH.read_text())
    events.append(event)
    STATE_PATH.write_text(json.dumps(events, indent=2))


async def protected_resource(request: Request) -> JSONResponse:
    return JSONResponse({
        "resource": BASE_URL,
        "authorization_servers": [BASE_URL],
        "scopes_supported": ["default"],
        "bearer_methods_supported": ["header"],
        "resource_name": "Mock Notion MCP",
    })


async def authorization_server(request: Request) -> JSONResponse:
    return JSONResponse({
        "issuer": BASE_URL,
        "authorization_endpoint": f"{BASE_URL}/authorize",
        "token_endpoint": f"{BASE_URL}/token",
        "registration_endpoint": f"{BASE_URL}/register",
        "scopes_supported": ["default"],
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        # Notion's order, and the reason DCR hands back client_secret_basic.
        "token_endpoint_auth_methods_supported": [
            "client_secret_basic", "client_secret_post", "none",
        ],
        "code_challenge_methods_supported": ["S256"],
    })


async def register(request: Request) -> JSONResponse:
    body = await request.json()
    client_id = secrets.token_urlsafe(12)
    client = {
        "client_id": client_id,
        "client_secret": secrets.token_urlsafe(24),
        "redirect_uris": body.get("redirect_uris", []),
        "client_name": body.get("client_name", ""),
        "grant_types": body.get("grant_types", ["authorization_code"]),
        "response_types": body.get("response_types", ["code"]),
        "token_endpoint_auth_method": "client_secret_basic",
    }
    CLIENTS[client_id] = client
    _record({"event": "register", "assigned_auth_method": "client_secret_basic"})
    return JSONResponse(client)


async def authorize(request: Request) -> RedirectResponse | JSONResponse:
    params = request.query_params
    client = CLIENTS.get(params.get("client_id", ""))
    if client is None:
        return JSONResponse({"error": "invalid_client"}, status_code=400)
    code = secrets.token_urlsafe(16)
    CODES[code] = {
        "client_id": client["client_id"],
        "redirect_uri": params.get("redirect_uri", ""),
        "code_challenge": params.get("code_challenge", ""),
    }
    query = {"code": code}
    if params.get("state"):
        query["state"] = params["state"]
    return RedirectResponse(f"{params['redirect_uri']}?{urlencode(query)}", status_code=302)


async def token(request: Request) -> JSONResponse:
    form = await request.form()
    header = request.headers.get("authorization", "")
    body_client_id = form.get("client_id")

    _record({
        "event": "token_request",
        "grant_type": form.get("grant_type"),
        "had_authorization_header": bool(header),
        "auth_scheme": header.split(" ")[0] if header else None,
        "had_body_client_id": body_client_id is not None,
        "had_body_client_secret": form.get("client_secret") is not None,
        "form_keys": sorted(form.keys()),
    })

    # Notion's rule, verbatim: a Basic header plus a body client_id is two
    # authentication methods on one request (RFC 6749 2.3.1).
    if header and body_client_id is not None:
        payload = {
            "error": "invalid_request",
            "error_description": "Client must not use multiple authentication methods",
        }
        _record({"event": "token_response", "status": 400, "body": payload})
        return JSONResponse(payload, status_code=400)

    if header.startswith("Basic "):
        raw = base64.b64decode(header[len("Basic "):]).decode()
        client_id, client_secret = (unquote(p) for p in raw.split(":", 1))
    else:
        client_id, client_secret = body_client_id, form.get("client_secret")

    client = CLIENTS.get(client_id or "")
    if client is None or client_secret != client["client_secret"]:
        payload = {"error": "invalid_client"}
        _record({"event": "token_response", "status": 401, "body": payload})
        return JSONResponse(payload, status_code=401)

    if form.get("grant_type") == "authorization_code":
        entry = CODES.pop(form.get("code", ""), None)
        if entry is None or entry["client_id"] != client_id:
            payload = {"error": "invalid_grant"}
            _record({"event": "token_response", "status": 400, "body": payload})
            return JSONResponse(payload, status_code=400)

    access_token = secrets.token_urlsafe(24)
    TOKENS.add(access_token)
    _record({"event": "token_response", "status": 200, "body": {"access_token": "<issued>"}})
    return JSONResponse({
        "access_token": access_token,
        "token_type": "Bearer",
        "expires_in": 3600,
        "refresh_token": secrets.token_urlsafe(24),
        "scope": "default",
    })


async def probe(request: Request) -> JSONResponse:
    events = json.loads(STATE_PATH.read_text()) if STATE_PATH.exists() else []
    return JSONResponse({"events": events})


def build_app(tool_names: list[str]):
    mcp = FastMCP("Mock Notion MCP")
    for name in tool_names:
        def _make(tool_name: str):
            def _tool(query: str = "") -> str:
                return f"{tool_name} result for {query!r}"
            _tool.__name__ = tool_name
            _tool.__doc__ = f"{tool_name.replace('_', ' ').capitalize()} in the mock workspace."
            return _tool
        mcp.tool(_make(name))

    # Served at /mcp by the MCP app itself: mounting a sub-app under /mcp would make
    # Starlette answer the client's POST /mcp with a 307 to /mcp/, and the MCP client
    # does not re-POST a redirect.
    app = mcp.http_app(path="/mcp", stateless_http=True)
    app.router.routes[0:0] = [
        Route("/.well-known/oauth-protected-resource", protected_resource),
        Route("/.well-known/oauth-protected-resource/mcp", protected_resource),
        Route("/.well-known/oauth-authorization-server", authorization_server),
        Route("/register", register, methods=["POST"]),
        Route("/authorize", authorize),
        Route("/token", token, methods=["POST"]),
        Route("/_probe", probe),
    ]

    async def gated(scope, receive, send):
        """Bearer gate on the MCP endpoint.

        The 401 challenge is what starts the whole OAuth flow: the MCP client reads
        `resource_metadata` off WWW-Authenticate to find this authorization server.
        """
        if scope["type"] == "http" and scope["path"].rstrip("/") == "/mcp":
            headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
            auth = headers.get("authorization", "")
            if not (auth.startswith("Bearer ") and auth[len("Bearer "):] in TOKENS):
                _record({"event": "mcp_unauthenticated"})
                challenge = (
                    'Bearer error="invalid_token", resource_metadata='
                    f'"{BASE_URL}/.well-known/oauth-protected-resource"'
                )
                response = JSONResponse(
                    {"error": "unauthorized"}, status_code=401,
                    headers={"WWW-Authenticate": challenge},
                )
                await response(scope, receive, send)
                return
            _record({"event": "mcp_authenticated"})
        await app(scope, receive, send)

    gated.lifespan = app.lifespan
    return gated


def main() -> int:
    global STATE_PATH, BASE_URL
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--state", type=Path, required=True)
    ap.add_argument("--tools", default="search,fetch_page,create_page")
    args = ap.parse_args()

    STATE_PATH = args.state
    BASE_URL = f"http://127.0.0.1:{args.port}"
    STATE_PATH.write_text("[]")
    uvicorn.run(build_app(args.tools.split(",")), host="127.0.0.1", port=args.port,
                log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
