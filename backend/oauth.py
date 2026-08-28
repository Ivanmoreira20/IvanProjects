from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import secrets
import sqlite3
import time
from typing import Any
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse, RedirectResponse
from starlette.concurrency import run_in_threadpool

import auth
import config
import db

logger = logging.getLogger("vertex.oauth")

router = APIRouter(prefix="/api/auth/google", tags=["google"])

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v3/userinfo"
GOOGLE_SCOPE = "openid email profile"

HTTP_TIMEOUT = 10.0

STATE_COOKIE = "vertex_oauth"
STATE_COOKIE_PATH = "/api/auth/google"
STATE_TTL = 600

SUCCESS_REDIRECT = "/app#/dashboard"
FAILURE_REDIRECT = "/app#/login?erro=google"

DISABLED_DETAIL = "Login com Google não está configurado neste servidor"

def _carregar_signing_key() -> bytes:
    bruto = config.get("VERTEX_OAUTH_SIGNING_KEY")
    if bruto:
        try:
            chave = base64.b64decode(bruto)
        except (ValueError, base64.binascii.Error):
            chave = b""
        if len(chave) >= 32:
            return chave
        logger.warning("VERTEX_OAUTH_SIGNING_KEY inválida ou curta; usando chave efêmera.")
    return secrets.token_bytes(32)

_SIGNING_KEY = _carregar_signing_key()

def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")

def _sign(payload: str) -> str:
    return _b64url(hmac.new(_SIGNING_KEY, payload.encode("utf-8"), hashlib.sha256).digest())

def pack_state(state: str, verifier: str, remember: bool) -> str:
    payload = f"{state}.{verifier}.{1 if remember else 0}.{int(time.time()) + STATE_TTL}"
    return f"{payload}.{_sign(payload)}"

def unpack_state(raw: str | None) -> dict[str, Any] | None:
    if not raw:
        return None
    parts = raw.split(".")
    if len(parts) != 5:
        return None
    payload = ".".join(parts[:4])
    if not hmac.compare_digest(_sign(payload), parts[4]):
        return None
    try:
        expires_at = int(parts[3])
    except ValueError:
        return None
    if expires_at <= int(time.time()):
        return None
    return {"state": parts[0], "verifier": parts[1], "remember": parts[2] == "1"}

def _clear_state_cookie(request: Request, response: Response) -> None:
    response.delete_cookie(
        STATE_COOKIE,
        path=STATE_COOKIE_PATH,
        samesite="lax",
        secure=request.url.scheme == "https",
        httponly=True,
    )

def _truthy(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "sim", "on", "yes"}

def _failure(request: Request) -> RedirectResponse:
    response = RedirectResponse(FAILURE_REDIRECT, status_code=status.HTTP_302_FOUND)
    _clear_state_cookie(request, response)
    return response

def _bad_request(request: Request, detail: str) -> JSONResponse:
    response = JSONResponse({"detail": detail}, status_code=status.HTTP_400_BAD_REQUEST)
    _clear_state_cookie(request, response)
    return response

@router.get("/start", include_in_schema=True)
def google_start(request: Request, remember: str = "0") -> Response:
    if not config.google_enabled():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=DISABLED_DETAIL)

    state = secrets.token_urlsafe(24)
    verifier = secrets.token_urlsafe(64)
    challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())

    params = {
        "client_id": config.google_client_id(),
        "redirect_uri": config.google_redirect_uri(),
        "response_type": "code",
        "scope": GOOGLE_SCOPE,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "access_type": "online",
        "prompt": "select_account",
    }

    response = RedirectResponse(
        f"{GOOGLE_AUTH_URL}?{urlencode(params)}", status_code=status.HTTP_302_FOUND
    )
    response.set_cookie(
        STATE_COOKIE,
        pack_state(state, verifier, _truthy(remember)),
        max_age=STATE_TTL,
        path=STATE_COOKIE_PATH,
        httponly=True,
        samesite="lax",
        secure=request.url.scheme == "https",
    )
    return response

async def exchange_code(code: str, verifier: str) -> dict[str, Any]:
    data = {
        "code": code,
        "client_id": config.google_client_id(),
        "client_secret": config.google_client_secret(),
        "redirect_uri": config.google_redirect_uri(),
        "grant_type": "authorization_code",
        "code_verifier": verifier,
    }
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        response = await client.post(GOOGLE_TOKEN_URL, data=data)
    response.raise_for_status()
    payload = response.json()
    if not payload.get("access_token"):
        raise ValueError("resposta do Google sem access_token")
    return payload

async def fetch_profile(access_token: str) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        response = await client.get(
            GOOGLE_USERINFO_URL, headers={"Authorization": f"Bearer {access_token}"}
        )
    response.raise_for_status()
    return response.json()

def resolve_and_login(
    previous_token: str | None, sub: str, email: str, name: str, remember: bool
) -> tuple[dict[str, Any], str, str]:
    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT id, name, email FROM users WHERE google_sub = ?", (sub,)
        ).fetchone()

        if row is None:
            row = conn.execute(
                "SELECT id, name, email FROM users WHERE email = ?", (email,)
            ).fetchone()
            if row is not None:

                conn.execute(
                    "UPDATE users SET google_sub = ?, email_verified = 1 WHERE id = ?",
                    (sub, int(row["id"])),
                )
                conn.execute(
                    "DELETE FROM email_codes WHERE user_id = ?", (int(row["id"]),)
                )
                logger.info("Conta %s vinculada ao Google.", email)

        if row is None:
            cursor = conn.execute(
                """INSERT INTO users (email, name, password_hash, created_at,
                                      email_verified, google_sub, auth_provider)
                   VALUES (?, ?, ?, ?, 1, ?, 'google')""",
                (email, name, auth.NO_PASSWORD_SENTINEL, db.now_iso(), sub),
            )
            user = {"id": int(cursor.lastrowid), "name": name, "email": email}
            logger.info("Conta criada via Google: %s", email)
        else:
            user = {"id": int(row["id"]), "name": row["name"], "email": row["email"]}

        if previous_token:
            auth.delete_session_by_token(conn, previous_token)
        token, csrf_token = auth.create_session(
            conn, user["id"], remember,
            auth.rotulo_do_aparelho(request.headers.get("user-agent")),
        )

    return user, token, csrf_token

@router.get("/callback", include_in_schema=True)
async def google_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> Response:
    if not config.google_enabled():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=DISABLED_DETAIL)

    unpacked = unpack_state(request.cookies.get(STATE_COOKIE))

    if unpacked is None or not state or not hmac.compare_digest(state, unpacked["state"]):
        logger.warning("Callback do Google com state inválido ou expirado.")
        return _bad_request(
            request, "Sessão de login com Google inválida ou expirada. Tente novamente."
        )

    if error or not code:
        logger.info("Login com Google interrompido pelo usuário ou sem code (error=%s).", error)
        return _failure(request)

    try:
        tokens = await exchange_code(code, unpacked["verifier"])
        profile = await fetch_profile(str(tokens["access_token"]))
    except (httpx.HTTPError, ValueError, KeyError) as exc:
        logger.error("Falha ao falar com o Google (%s: %s).", type(exc).__name__, exc)
        return _failure(request)

    sub = str(profile.get("sub") or "").strip()
    email = str(profile.get("email") or "").strip().lower()
    verified = profile.get("email_verified")
    name = str(profile.get("name") or "").strip() or (email.split("@")[0] if email else "Usuário")

    if not sub or not email:
        logger.error("Perfil do Google veio sem sub ou sem email.")
        return _failure(request)

    if verified is not True and str(verified).lower() != "true":
        logger.warning("Login com Google recusado: e-mail %s não verificado na origem.", email)
        return _bad_request(
            request,
            "O Google informou que este e-mail não está verificado. "
            "Confirme o e-mail na sua conta Google e tente novamente.",
        )

    try:
        user, token, csrf_token = await run_in_threadpool(
            resolve_and_login,
            request.cookies.get(auth.SESSION_COOKIE),
            sub,
            email,
            name[:80],
            bool(unpacked["remember"]),
        )
    except sqlite3.Error as exc:
        logger.error("Falha ao criar/vincular conta do Google (%s: %s).", type(exc).__name__, exc)
        return _failure(request)

    response = RedirectResponse(SUCCESS_REDIRECT, status_code=status.HTTP_302_FOUND)
    auth.set_session_cookies(request, response, token, csrf_token, bool(unpacked["remember"]))
    _clear_state_cookie(request, response)
    logger.info("Login com Google concluído para %s.", user["email"])
    return response
