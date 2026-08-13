"""OAuth2 authentication for Google Search Console API.

Auth priority:
1. Cached GSC-specific token (~/.config/gsc-mcp/token.json)
2. gcloud Application Default Credentials (ADC)
3. OAuth2 InstalledAppFlow via client_secrets.json (browser consent)

Token caching uses secure file permissions (0o600).
Scope escalation via GSC_WRITE_ACCESS=true env var.
"""

import os
import stat
from pathlib import Path

import google.auth
import google.auth.credentials
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

CONFIG_DIR = Path.home() / ".config" / "gsc-mcp"
TOKEN_PATH = CONFIG_DIR / "token.json"
CLIENT_SECRETS_PATH = CONFIG_DIR / "client_secrets.json"

SCOPE_READONLY = "https://www.googleapis.com/auth/webmasters.readonly"
SCOPE_READWRITE = "https://www.googleapis.com/auth/webmasters"


def _get_scopes() -> list[str]:
    if os.environ.get("GSC_WRITE_ACCESS", "").lower() == "true":
        return [SCOPE_READWRITE]
    return [SCOPE_READONLY]


def _secure_write_token(creds: Credentials) -> None:
    """Write token file with 0o600 permissions."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    TOKEN_PATH.write_text(creds.to_json())
    os.chmod(TOKEN_PATH, stat.S_IRUSR | stat.S_IWUSR)


def _verify_token_permissions() -> None:
    """Fix token file permissions if overly permissive."""
    if TOKEN_PATH.exists():
        mode = TOKEN_PATH.stat().st_mode
        if mode & (stat.S_IRGRP | stat.S_IROTH):
            os.chmod(TOKEN_PATH, stat.S_IRUSR | stat.S_IWUSR)


def _try_cached_token(scopes: list[str]) -> Credentials | None:
    """Try loading cached GSC-specific token."""
    if not TOKEN_PATH.exists():
        return None
    _verify_token_permissions()
    creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), scopes)
    if creds.valid:
        return creds
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        _secure_write_token(creds)
        return creds
    return None


def _try_adc() -> google.auth.credentials.Credentials | None:
    """Try gcloud Application Default Credentials."""
    try:
        creds, _ = google.auth.default()
        if not creds.valid:
            creds.refresh(Request())
        return creds
    except google.auth.exceptions.DefaultCredentialsError:
        return None


def _run_oauth_flow(scopes: list[str]) -> Credentials:
    """Run OAuth2 browser consent flow using client_secrets.json."""
    if not CLIENT_SECRETS_PATH.exists():
        raise FileNotFoundError(
            f"No credentials found. Either:\n"
            f"  1. Run 'gcloud auth application-default login' for ADC, or\n"
            f"  2. Download OAuth client secrets to {CLIENT_SECRETS_PATH}"
        )
    flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_SECRETS_PATH), scopes)
    creds = flow.run_local_server(port=0)
    _secure_write_token(creds)
    return creds


def get_credentials() -> google.auth.credentials.Credentials:
    """Get valid credentials for Search Console API.

    Tries in order: cached token, ADC, OAuth flow.
    """
    scopes = _get_scopes()

    cached = _try_cached_token(scopes)
    if cached:
        return cached

    adc = _try_adc()
    if adc:
        return adc

    return _run_oauth_flow(scopes)