"""DadeCore private MCP auth standard — embedded single-owner Authorization Server.

Implements, self-contained inside this MCP (no external IdP, no session/token
storage):

- Owner-key gate as the mandatory first step of /authorize. If the key is
  wrong, the flow stops there and the login page shows an explicit error.
- OAuth 2.1 Authorization Code Flow with PKCE (S256 only).
- RFC 8414 Authorization Server Metadata discovery.
- RFC 7591 Dynamic Client Registration (client_id is itself a self-describing
  encrypted blob, so no client database is needed).
- Refresh tokens.
- Stateless tokens: authorization codes, access tokens and refresh tokens are
  all AES-256-GCM encrypted JSON blobs keyed by ENCRYPTION_KEY. Nothing is
  persisted server-side, so restarting the process does not lose state, and
  rotating ENCRYPTION_KEY instantly revokes every outstanding token.
- MCP_BEARER_TOKEN as a direct Bearer-auth shortcut for CLI clients that
  don't want to run the OAuth dance.

This is deliberately independent from gsc_mcp/auth.py, which handles the
*separate* OAuth flow against Google's API. The two must never be mixed.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import html
import json
import os
import time
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from starlette.applications import Starlette
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.routing import Mount, Route

# --- Token/code lifetimes ---
AUTH_CODE_TTL = 120  # 2 minutes
ACCESS_TOKEN_TTL = 60 * 60  # 1 hour
REFRESH_TOKEN_TTL = 60 * 60 * 24 * 90  # 90 days
CLIENT_TTL = 60 * 60 * 24 * 365 * 2  # 2 years


# --- Crypto helpers -------------------------------------------------------

_key_cache: bytes | None = None


def _get_key() -> bytes:
    global _key_cache
    if _key_cache is None:
        raw = os.environ.get("ENCRYPTION_KEY")
        if not raw:
            raise RuntimeError(
                "ENCRYPTION_KEY environment variable is required for the MCP "
                "auth server (32 random bytes, base64/base64url encoded)."
            )
        padded = raw + "=" * (-len(raw) % 4)
        key = base64.urlsafe_b64decode(padded)
        if len(key) != 32:
            raise RuntimeError("ENCRYPTION_KEY must decode to exactly 32 bytes (AES-256).")
        _key_cache = key
    return _key_cache


def _b64u_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64u_decode(s: str) -> bytes:
    padded = s + "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(padded)


def _seal(payload: dict[str, Any], purpose: str) -> str:
    """Encrypt payload as a stateless, self-verifying token bound to `purpose`."""
    aesgcm = AESGCM(_get_key())
    nonce = os.urandom(12)
    data = json.dumps(payload, separators=(",", ":")).encode()
    ct = aesgcm.encrypt(nonce, data, purpose.encode())
    return _b64u_encode(nonce + ct)


def _open(token: str, purpose: str) -> dict[str, Any] | None:
    """Decrypt and validate a stateless token. Returns None if invalid/expired."""
    try:
        raw = _b64u_decode(token)
        nonce, ct = raw[:12], raw[12:]
        aesgcm = AESGCM(_get_key())
        data = aesgcm.decrypt(nonce, ct, purpose.encode())
        payload = json.loads(data)
    except Exception:
        return None
    if payload.get("exp", 0) < time.time():
        return None
    return payload


def _pkce_matches(code_verifier: str, code_challenge: str) -> bool:
    digest = hashlib.sha256(code_verifier.encode()).digest()
    expected = _b64u_encode(digest)
    return hmac.compare_digest(expected, code_challenge)


# --- Client registry (stateless — encoded into client_id itself) ----------


def _issue_client_id(redirect_uris: list[str], client_name: str) -> str:
    return _seal(
        {
            "redirect_uris": redirect_uris,
            "client_name": client_name,
            "exp": time.time() + CLIENT_TTL,
        },
        purpose="client",
    )


def _load_client(client_id: str) -> dict[str, Any] | None:
    return _open(client_id, purpose="client")


def _is_allowed_redirect_uri(uri: str) -> bool:
    if uri.startswith("https://"):
        return True
    # Loopback redirects for native/CLI clients per OAuth 2.1.
    if uri.startswith("http://127.0.0.1") or uri.startswith("http://localhost"):
        return True
    return False


# --- Base URL resolution ----------------------------------------------------


def _base_url_from_scope(scope: dict) -> str:
    override = os.environ.get("MCP_PUBLIC_URL")
    if override:
        return override.rstrip("/")
    headers = dict(scope.get("headers") or [])
    scheme = scope.get("scheme", "http")
    xf_proto = headers.get(b"x-forwarded-proto", b"").decode()
    if xf_proto:
        scheme = xf_proto.split(",")[0].strip()
    host = headers.get(b"host", b"").decode()
    if not host:
        server = scope.get("server") or ("", None)
        host = server[0] or "localhost"
    return f"{scheme}://{host}"


def _base_url(request: Request) -> str:
    return _base_url_from_scope(request.scope)


# --- RFC 8414 / RFC 9728 discovery ------------------------------------------


async def _authorization_server_metadata(request: Request) -> JSONResponse:
    base = _base_url(request)
    return JSONResponse(
        {
            "issuer": base,
            "authorization_endpoint": f"{base}/authorize",
            "token_endpoint": f"{base}/token",
            "registration_endpoint": f"{base}/register",
            "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code", "refresh_token"],
            "code_challenge_methods_supported": ["S256"],
            "token_endpoint_auth_methods_supported": ["none"],
            "scopes_supported": ["mcp"],
        }
    )


async def _protected_resource_metadata(request: Request) -> JSONResponse:
    base = _base_url(request)
    return JSONResponse(
        {
            "resource": f"{base}/mcp",
            "authorization_servers": [base],
        }
    )


# --- RFC 7591 Dynamic Client Registration -----------------------------------


async def _register_client(request: Request) -> JSONResponse:
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid_client_metadata"}, status_code=400)

    redirect_uris = body.get("redirect_uris")
    if not isinstance(redirect_uris, list) or not redirect_uris:
        return JSONResponse(
            {"error": "invalid_client_metadata", "error_description": "redirect_uris is required"},
            status_code=400,
        )
    for uri in redirect_uris:
        if not isinstance(uri, str) or not _is_allowed_redirect_uri(uri):
            return JSONResponse(
                {
                    "error": "invalid_redirect_uri",
                    "error_description": f"redirect_uri not allowed: {uri!r}",
                },
                status_code=400,
            )

    client_name = body.get("client_name") or "mcp-client"
    client_id = _issue_client_id(redirect_uris, client_name)

    return JSONResponse(
        {
            "client_id": client_id,
            "client_id_issued_at": int(time.time()),
            "redirect_uris": redirect_uris,
            "client_name": client_name,
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "none",
        },
        status_code=201,
    )


# --- /authorize: owner-key gate is the mandatory first step -----------------


def _authorize_form_html(*, client_name: str, error: str | None, fields: dict[str, str]) -> str:
    error_html = ""
    if error:
        error_html = f'<p class="error">{html.escape(error)}</p>'
    hidden_inputs = "\n".join(
        f'<input type="hidden" name="{html.escape(k)}" value="{html.escape(v)}">'
        for k, v in fields.items()
    )
    safe_client_name = html.escape(client_name)
    return f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8">
<title>Autorizar acceso — GSC MCP</title>
<style>
body {{ font-family: system-ui, sans-serif; max-width: 420px; margin: 80px auto; color: #1a1a1a; }}
h1 {{ font-size: 1.2rem; }}
p.error {{ color: #b91c1c; font-weight: 600; }}
input[type=password] {{ width: 100%; padding: 10px; margin: 12px 0; box-sizing: border-box; }}
button {{ padding: 10px 20px; background: #111; color: #fff; border: none; border-radius: 6px; cursor: pointer; }}
</style>
</head>
<body>
<h1>Autorizar "{safe_client_name}"</h1>
<p>Este cliente solicita acceso al servidor MCP de Google Search Console. Introduce la clave del propietario para continuar.</p>
{error_html}
<form method="post">
{hidden_inputs}
<input type="password" name="owner_key" placeholder="Clave del propietario" required autofocus>
<button type="submit">Autorizar</button>
</form>
</body>
</html>"""


async def _authorize(request: Request) -> Response:
    if request.method == "GET":
        params = request.query_params
    else:
        form = await request.form()
        params = form

    response_type = params.get("response_type", "code")
    client_id = params.get("client_id", "")
    redirect_uri = params.get("redirect_uri", "")
    state = params.get("state", "")
    code_challenge = params.get("code_challenge", "")
    code_challenge_method = params.get("code_challenge_method", "")
    scope = params.get("scope", "mcp")

    if response_type != "code":
        return JSONResponse({"error": "unsupported_response_type"}, status_code=400)
    if code_challenge_method != "S256" or not code_challenge:
        return JSONResponse(
            {"error": "invalid_request", "error_description": "PKCE S256 code_challenge is required"},
            status_code=400,
        )

    client = _load_client(client_id)
    if client is None:
        return JSONResponse({"error": "invalid_client"}, status_code=400)
    if redirect_uri not in client["redirect_uris"]:
        return JSONResponse({"error": "invalid_redirect_uri"}, status_code=400)

    fields = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "state": state,
        "code_challenge": code_challenge,
        "scope": scope,
    }

    if request.method == "GET":
        return HTMLResponse(_authorize_form_html(client_name=client["client_name"], error=None, fields=fields))

    # POST: owner-key verification is the mandatory first (and gating) step.
    owner_key = os.environ.get("OWNER_KEY")
    submitted = params.get("owner_key", "")
    if not owner_key or not hmac.compare_digest(submitted, owner_key):
        return HTMLResponse(
            _authorize_form_html(
                client_name=client["client_name"],
                error="Clave incorrecta. Inténtalo de nuevo.",
                fields=fields,
            ),
            status_code=401,
        )

    code = _seal(
        {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "code_challenge": code_challenge,
            "scope": scope,
            "exp": time.time() + AUTH_CODE_TTL,
        },
        purpose="code",
    )
    sep = "&" if "?" in redirect_uri else "?"
    location = f"{redirect_uri}{sep}code={code}&state={state}"
    return RedirectResponse(location, status_code=302)


# --- /token ------------------------------------------------------------------


def _issue_token_pair(client_id: str, scope: str) -> dict[str, Any]:
    now = time.time()
    access_token = _seal(
        {"client_id": client_id, "scope": scope, "sub": "owner", "exp": now + ACCESS_TOKEN_TTL},
        purpose="access",
    )
    refresh_token = _seal(
        {"client_id": client_id, "scope": scope, "sub": "owner", "exp": now + REFRESH_TOKEN_TTL},
        purpose="refresh",
    )
    return {
        "access_token": access_token,
        "token_type": "Bearer",
        "expires_in": ACCESS_TOKEN_TTL,
        "refresh_token": refresh_token,
        "scope": scope,
    }


async def _token(request: Request) -> JSONResponse:
    form = await request.form()
    grant_type = form.get("grant_type")

    if grant_type == "authorization_code":
        code = form.get("code", "")
        redirect_uri = form.get("redirect_uri", "")
        client_id = form.get("client_id", "")
        code_verifier = form.get("code_verifier", "")

        payload = _open(code, purpose="code")
        if payload is None:
            return JSONResponse({"error": "invalid_grant"}, status_code=400)
        if payload["client_id"] != client_id or payload["redirect_uri"] != redirect_uri:
            return JSONResponse({"error": "invalid_grant"}, status_code=400)
        if not code_verifier or not _pkce_matches(code_verifier, payload["code_challenge"]):
            return JSONResponse({"error": "invalid_grant", "error_description": "PKCE verification failed"}, status_code=400)

        return JSONResponse(_issue_token_pair(client_id, payload["scope"]))

    if grant_type == "refresh_token":
        refresh_token = form.get("refresh_token", "")
        payload = _open(refresh_token, purpose="refresh")
        if payload is None:
            return JSONResponse({"error": "invalid_grant"}, status_code=400)
        return JSONResponse(_issue_token_pair(payload["client_id"], payload["scope"]))

    return JSONResponse({"error": "unsupported_grant_type"}, status_code=400)


# --- Health -------------------------------------------------------------------


async def _health(request: Request) -> JSONResponse:
    return JSONResponse({"service": "gsc-mcp", "status": "ok"})


# --- Bearer gate for the actual MCP endpoint -----------------------------------


def _is_bearer_valid(token: str) -> bool:
    static = os.environ.get("MCP_BEARER_TOKEN", "")
    if static and hmac.compare_digest(token, static):
        return True
    return _open(token, purpose="access") is not None


class AuthGateMiddleware:
    """Raw ASGI middleware: gates the streamable-http MCP endpoint behind Bearer auth.

    Deliberately implemented as pure ASGI (not BaseHTTPMiddleware) so it does
    not buffer or interfere with the streamable-http/SSE response body.
    """

    def __init__(self, app, protected_prefix: str = "/mcp"):
        self.app = app
        self.protected_prefix = protected_prefix

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not scope["path"].startswith(self.protected_prefix):
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers") or [])
        auth_header = headers.get(b"authorization", b"").decode()
        token = auth_header[7:].strip() if auth_header.lower().startswith("bearer ") else ""

        if not token or not _is_bearer_valid(token):
            base = _base_url_from_scope(scope)
            resource_metadata = f"{base}/.well-known/oauth-protected-resource"
            body = json.dumps({"error": "invalid_token"}).encode()
            await send(
                {
                    "type": "http.response.start",
                    "status": 401,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"www-authenticate", f'Bearer resource_metadata="{resource_metadata}"'.encode()),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": body})
            return

        await self.app(scope, receive, send)


# --- App assembly --------------------------------------------------------------


def build_app(mcp) -> Starlette:
    """Wrap a FastMCP instance with the embedded owner-gated OAuth 2.1 AS."""
    mcp_app = mcp.http_app(path="/mcp")

    routes = [
        Route("/", _health, methods=["GET"]),
        Route("/.well-known/oauth-authorization-server", _authorization_server_metadata, methods=["GET"]),
        Route("/.well-known/oauth-authorization-server/mcp", _authorization_server_metadata, methods=["GET"]),
        Route("/.well-known/oauth-protected-resource", _protected_resource_metadata, methods=["GET"]),
        Route("/.well-known/oauth-protected-resource/mcp", _protected_resource_metadata, methods=["GET"]),
        Route("/register", _register_client, methods=["POST"]),
        Route("/authorize", _authorize, methods=["GET", "POST"]),
        Route("/token", _token, methods=["POST"]),
        Mount("/", app=mcp_app),
    ]

    app = Starlette(routes=routes, lifespan=mcp_app.lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(AuthGateMiddleware, protected_prefix="/mcp")
    return app
