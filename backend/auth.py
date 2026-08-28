from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import logging
import os
import secrets
import threading
from datetime import timedelta
from typing import Annotated

from fastapi import Depends, HTTPException, Request, Response, status

import db
import orgs

logger = logging.getLogger("vertex.auth")

SCRYPT_N = int(os.environ.get("VERTEX_SCRYPT_N") or 2**17)
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_DKLEN = 64
SALT_BYTES = 16

SCRYPT_N_MAX = 2**18
SCRYPT_R_MAX = 16
SCRYPT_P_MAX = 4

def _maxmem_for(n: int, r: int, p: int) -> int:
    return 128 * n * r * p + (16 * 1024 * 1024)

def hash_password(password: str) -> str:
    salt = secrets.token_bytes(SALT_BYTES)
    derived = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=SCRYPT_N,
        r=SCRYPT_R,
        p=SCRYPT_P,
        dklen=SCRYPT_DKLEN,
        maxmem=_maxmem_for(SCRYPT_N, SCRYPT_R, SCRYPT_P),
    )
    return "scrypt${}${}${}${}${}".format(
        SCRYPT_N,
        SCRYPT_R,
        SCRYPT_P,
        base64.b64encode(salt).decode("ascii"),
        base64.b64encode(derived).decode("ascii"),
    )

NO_PASSWORD_SENTINEL = "google-oauth$conta-sem-senha-local"

def has_usable_password(stored: str | None) -> bool:
    return bool(stored) and stored.startswith("scrypt$")

def verify_password(password: str, stored: str) -> bool:
    if not has_usable_password(stored):
        return False
    try:
        scheme, raw_n, raw_r, raw_p, salt_b64, hash_b64 = stored.split("$")
        if scheme != "scrypt":
            return False
        n, r, p = int(raw_n), int(raw_r), int(raw_p)
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(hash_b64)
    except (ValueError, TypeError, base64.binascii.Error):
        return False

    if not (1 <= n <= SCRYPT_N_MAX and 1 <= r <= SCRYPT_R_MAX and 1 <= p <= SCRYPT_P_MAX):
        return False

    derived = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=n,
        r=r,
        p=p,
        dklen=len(expected),
        maxmem=_maxmem_for(n, r, p),
    )
    return hmac.compare_digest(derived, expected)

_dummy_lock = threading.Lock()
_dummy_hash: str | None = None

def warm_dummy_hash() -> str:
    global _dummy_hash
    with _dummy_lock:
        if _dummy_hash is None:
            _dummy_hash = hash_password(secrets.token_urlsafe(32))
        return _dummy_hash

def burn_password_time() -> None:
    verify_password("senha-inexistente-para-equalizar-tempo", warm_dummy_hash())

SESSION_COOKIE = "vertex_session"
CSRF_COOKIE = "vertex_csrf"
CSRF_HEADER = "X-CSRF-Token"

REMEMBER_MAX_AGE = 2592000
SESSION_TTL_REMEMBER = timedelta(seconds=REMEMBER_MAX_AGE)
SESSION_TTL_SHORT = timedelta(hours=8)

GENERIC_LOGIN_ERROR = "E-mail ou senha inválidos"

def sha256_hex(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()

LAST_SEEN_THROTTLE = timedelta(minutes=5)

_NAVEGADORES = (
    ("Edg", "Edge"), ("OPR", "Opera"), ("Chrome", "Chrome"),
    ("Firefox", "Firefox"), ("Safari", "Safari"),
)
_SISTEMAS = (
    ("Windows", "Windows"), ("Android", "Android"), ("iPhone", "iPhone"),
    ("iPad", "iPad"), ("Mac OS", "Mac"), ("Linux", "Linux"),
)

def rotulo_do_aparelho(user_agent: str | None) -> str:
    ua = (user_agent or "")[:400]
    navegador = next((nome for chave, nome in _NAVEGADORES if chave in ua), "")
    sistema = next((nome for chave, nome in _SISTEMAS if chave in ua), "")
    if navegador and sistema:
        return f"{navegador} no {sistema}"
    return navegador or sistema or "Aparelho desconhecido"

def tocar_sessao(conn: db.Connection, session_row: db.Row) -> None:
    try:
        bruto = session_row["last_seen_at"]
    except (IndexError, KeyError):
        return
    anterior = None
    if bruto:
        try:
            anterior = db.parse_iso(bruto)
        except (ValueError, TypeError):
            anterior = None
    agora = db.utcnow()
    if anterior is not None and agora - anterior < LAST_SEEN_THROTTLE:
        return
    conn.execute(
        "UPDATE sessions SET last_seen_at = ? WHERE id = ?",
        (db.iso(agora), int(session_row["id"])),
    )

def create_session(
    conn: db.Connection, user_id: int, remember: bool, device: str = ""
) -> tuple[str, str]:
    token = secrets.token_urlsafe(32)
    csrf_token = secrets.token_urlsafe(32)
    created = db.utcnow()
    expires = created + (SESSION_TTL_REMEMBER if remember else SESSION_TTL_SHORT)
    conn.execute(
        """INSERT INTO sessions
               (user_id, token_hash, csrf_hash, remember, expires_at, created_at,
                device, last_seen_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            user_id,
            sha256_hex(token),
            sha256_hex(csrf_token),
            1 if remember else 0,
            db.iso(expires),
            db.iso(created),
            (device or "")[:60],
            db.iso(created),
        ),
    )
    return token, csrf_token

def lookup_session(conn: db.Connection, token: str) -> db.Row | None:
    row = conn.execute(
        "SELECT id, user_id, csrf_hash, remember, expires_at, device, last_seen_at "
        "FROM sessions WHERE token_hash = ?",
        (sha256_hex(token),),
    ).fetchone()
    if row is None:
        return None
    if db.parse_iso(row["expires_at"]) <= db.utcnow():
        conn.execute("DELETE FROM sessions WHERE id = ?", (row["id"],))
        return None
    return row

def delete_session_by_token(conn: db.Connection, token: str) -> None:
    conn.execute("DELETE FROM sessions WHERE token_hash = ?", (sha256_hex(token),))

def verify_csrf(session_row: db.Row, header_value: str | None) -> bool:
    if not header_value:
        return False
    return hmac.compare_digest(sha256_hex(header_value), session_row["csrf_hash"])

def _is_secure(request: Request) -> bool:
    return request.url.scheme == "https"

def set_session_cookies(
    request: Request, response: Response, token: str, csrf_token: str, remember: bool
) -> None:
    secure = _is_secure(request)
    shared = {"path": "/", "samesite": "lax", "secure": secure}

    if remember:
        response.set_cookie(
            SESSION_COOKIE, token, httponly=True, max_age=REMEMBER_MAX_AGE, **shared
        )
        response.set_cookie(
            CSRF_COOKIE, csrf_token, httponly=False, max_age=REMEMBER_MAX_AGE, **shared
        )
        return

    response.set_cookie(SESSION_COOKIE, token, httponly=True, **shared)
    response.set_cookie(CSRF_COOKIE, csrf_token, httponly=False, **shared)

def clear_session_cookies(request: Request, response: Response) -> None:
    secure = _is_secure(request)
    response.delete_cookie(
        SESSION_COOKIE, path="/", samesite="lax", secure=secure, httponly=True
    )
    response.delete_cookie(
        CSRF_COOKIE, path="/", samesite="lax", secure=secure, httponly=False
    )

DEVICE_COOKIE = "vertex_device"
DEVICE_COOKIE_MAX_AGE = 400 * 24 * 60 * 60
DEVICE_CODE_PURPOSE = "device"

def novo_token_de_dispositivo() -> str:
    return secrets.token_urlsafe(32)

def set_device_cookie(request: Request, response: Response, token: str) -> None:
    response.set_cookie(
        DEVICE_COOKIE,
        token,
        httponly=True,
        max_age=DEVICE_COOKIE_MAX_AGE,
        path="/",
        samesite="lax",
        secure=_is_secure(request),
    )

def dispositivo_conhecido(conn: db.Connection, user_id: int, token: str | None) -> bool:
    if not token:
        return False
    row = conn.execute(
        "SELECT id FROM known_devices WHERE user_id = ? AND device_hash = ?",
        (user_id, sha256_hex(token)),
    ).fetchone()
    if row is None:
        return False
    conn.execute(
        "UPDATE known_devices SET last_seen_at = ? WHERE id = ?",
        (db.iso(db.utcnow()), int(row["id"])),
    )
    return True

def registrar_dispositivo(
    conn: db.Connection, user_id: int, token: str, label: str = ""
) -> None:
    agora = db.iso(db.utcnow())
    conn.execute(
        """INSERT INTO known_devices (user_id, device_hash, label, created_at, last_seen_at)
           VALUES (?, ?, ?, ?, ?)
           ON CONFLICT(user_id, device_hash)
           DO UPDATE SET last_seen_at = excluded.last_seen_at""",
        (user_id, sha256_hex(token), (label or "")[:60], agora, agora),
    )

EMAIL_CODE_TTL = timedelta(minutes=15)
EMAIL_CODE_MAX_ATTEMPTS = 5
EMAIL_CODE_PURPOSE = "verify_email"

GENERIC_CODE_ERROR = "Código inválido ou expirado"
CODE_EXHAUSTED_ERROR = "Muitas tentativas incorretas. Solicite um novo código."

def generate_email_code() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"

def issue_email_code(
    conn: db.Connection, user_id: int, purpose: str = EMAIL_CODE_PURPOSE
) -> str:
    conn.execute(
        "DELETE FROM email_codes WHERE user_id = ? AND purpose = ?", (user_id, purpose)
    )
    code = generate_email_code()
    created = db.utcnow()
    conn.execute(
        """INSERT INTO email_codes (user_id, code_hash, purpose, attempts, expires_at, created_at)
           VALUES (?, ?, ?, 0, ?, ?)""",
        (
            user_id,
            sha256_hex(code),
            purpose,
            db.iso(created + EMAIL_CODE_TTL),
            db.iso(created),
        ),
    )
    return code

def consume_email_code(
    conn: db.Connection, user_id: int, code: str, purpose: str = EMAIL_CODE_PURPOSE
) -> tuple[bool, str]:
    row = conn.execute(
        """SELECT id, code_hash, attempts, expires_at FROM email_codes
           WHERE user_id = ? AND purpose = ? ORDER BY id DESC LIMIT 1""",
        (user_id, purpose),
    ).fetchone()
    if row is None:
        return False, GENERIC_CODE_ERROR

    if db.parse_iso(row["expires_at"]) <= db.utcnow():
        conn.execute("DELETE FROM email_codes WHERE id = ?", (row["id"],))
        return False, GENERIC_CODE_ERROR

    if hmac.compare_digest(sha256_hex(code), row["code_hash"]):
        conn.execute(
            "DELETE FROM email_codes WHERE user_id = ? AND purpose = ?", (user_id, purpose)
        )
        return True, ""

    attempts = int(row["attempts"]) + 1
    if attempts >= EMAIL_CODE_MAX_ATTEMPTS:
        conn.execute("DELETE FROM email_codes WHERE id = ?", (row["id"],))
        return False, CODE_EXHAUSTED_ERROR

    conn.execute("UPDATE email_codes SET attempts = ? WHERE id = ?", (attempts, row["id"]))
    return False, GENERIC_CODE_ERROR

LOGIN_LIMIT = 5
LOGIN_WINDOW = 15 * 60
REGISTER_LIMIT = 3
REGISTER_WINDOW = 60 * 60
RESEND_LIMIT = 3
RESEND_WINDOW = 15 * 60
VERIFY_LIMIT = 20
VERIFY_WINDOW = 15 * 60

PLAN_LIMIT = 5
PLAN_WINDOW = 60 * 60

PUBLIC_ACTION_LIMIT = 20
PUBLIC_ACTION_WINDOW = 60 * 60

BRUTE_BUCKET = "brute"
BRUTE_WINDOW = 24 * 60 * 60
BRUTE_THRESHOLD = 10

MAX_RATE_WINDOW = max(
    LOGIN_WINDOW, REGISTER_WINDOW, RESEND_WINDOW, VERIFY_WINDOW,
    PLAN_WINDOW, PUBLIC_ACTION_WINDOW, BRUTE_WINDOW,
)

LOGIN_BUCKET = "login"
REGISTER_BUCKET = "register"
RESEND_BUCKET = "resend"
VERIFY_BUCKET = "verify"
PLAN_BUCKET = "plan"
PUBLIC_BUCKET = "public"

def rate_bucket(prefix: str, key: str) -> str:
    return f"{prefix}|{key}"

def _register_hit(prefix: str, key: str, limit: int, window: int) -> bool:
    bucket = rate_bucket(prefix, key)
    now = db.utcnow()
    cutoff = db.iso(now - timedelta(seconds=window))

    with db.get_conn() as conn:
        conn.execute(
            "DELETE FROM rate_hits WHERE bucket = ? AND hit_at <= ?", (bucket, cutoff)
        )
        cursor = conn.execute(
            """INSERT INTO rate_hits (bucket, hit_at)
               SELECT ?, ?
               WHERE (SELECT COUNT(*) FROM rate_hits
                      WHERE bucket = ? AND hit_at > ?) < ?""",
            (bucket, db.iso(now), bucket, cutoff, limit),
        )
        recorded = cursor.rowcount or 0

    return recorded < 1

def clear_rate_bucket(prefix: str, key: str) -> None:
    with db.get_conn() as conn:
        conn.execute("DELETE FROM rate_hits WHERE bucket = ?", (rate_bucket(prefix, key),))

def purge_expired_rate_hits() -> int:
    return db.purge_rate_hits_older_than(MAX_RATE_WINDOW)

def client_ip(request: Request) -> str:
    return request.client.host if request.client else "desconhecido"

def rate_limit_ip(request: Request) -> str:
    raw = client_ip(request)
    try:
        addr = ipaddress.ip_address(raw)
    except ValueError:
        return raw

    if addr.version == 4:
        return str(addr)

    mapeado = addr.ipv4_mapped
    if mapeado is not None:
        return str(mapeado)

    prefixo = (int(addr) >> 64) << 64
    return str(ipaddress.IPv6Network((prefixo, 64)))

def _login_key(request: Request, email: str) -> str:
    return f"{rate_limit_ip(request)}|{email.strip().lower()}"

def enforce_login_rate_limit(request: Request, email: str) -> None:
    if _register_hit(LOGIN_BUCKET, _login_key(request, email), LOGIN_LIMIT, LOGIN_WINDOW):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Muitas tentativas de login. Tente novamente em alguns minutos.",
        )

def clear_login_rate_limit(request: Request, email: str) -> None:
    clear_rate_bucket(LOGIN_BUCKET, _login_key(request, email))

def enforce_register_rate_limit(request: Request) -> None:
    if _register_hit(REGISTER_BUCKET, rate_limit_ip(request), REGISTER_LIMIT, REGISTER_WINDOW):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Muitas contas criadas a partir deste endereço. Tente novamente mais tarde.",
        )

def enforce_plan_interest_rate_limit(request: Request) -> None:
    if _register_hit(PLAN_BUCKET, rate_limit_ip(request), PLAN_LIMIT, PLAN_WINDOW):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Já recebemos vários pedidos deste endereço. Tente novamente mais tarde.",
        )

def enforce_public_action_rate_limit(request: Request) -> None:
    if _register_hit(
        PUBLIC_BUCKET, rate_limit_ip(request), PUBLIC_ACTION_LIMIT, PUBLIC_ACTION_WINDOW
    ):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Muitas tentativas a partir deste endereço. Tente novamente mais tarde.",
        )

def enforce_resend_rate_limit(email: str) -> None:
    if _register_hit(RESEND_BUCKET, email.strip().lower(), RESEND_LIMIT, RESEND_WINDOW):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Muitos códigos solicitados para este e-mail. Aguarde alguns minutos.",
        )

def enforce_verify_rate_limit(request: Request, email: str) -> None:
    key = f"{rate_limit_ip(request)}|{email.strip().lower()}"
    if _register_hit(VERIFY_BUCKET, key, VERIFY_LIMIT, VERIFY_WINDOW):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Muitas tentativas de verificação. Tente novamente em alguns minutos.",
        )

def reset_rate_limits() -> None:
    with db.get_conn() as conn:
        conn.execute("DELETE FROM rate_hits")

def registrar_falha_login(user_id: int) -> None:
    with db.get_conn() as conn:
        conn.execute(
            "INSERT INTO rate_hits (bucket, hit_at) VALUES (?, ?)",
            (rate_bucket(BRUTE_BUCKET, str(user_id)), db.iso(db.utcnow())),
        )

def excesso_de_falhas(conn: db.Connection, user_id: int) -> bool:
    cutoff = db.iso(db.utcnow() - timedelta(seconds=BRUTE_WINDOW))
    linha = conn.execute(
        "SELECT COUNT(*) AS c FROM rate_hits WHERE bucket = ? AND hit_at > ?",
        (rate_bucket(BRUTE_BUCKET, str(user_id)), cutoff),
    ).fetchone()
    return int(linha["c"]) >= BRUTE_THRESHOLD

def limpar_falhas_login(conn: db.Connection, user_id: int) -> None:
    conn.execute(
        "DELETE FROM rate_hits WHERE bucket = ?",
        (rate_bucket(BRUTE_BUCKET, str(user_id)),),
    )

UNAUTHENTICATED_DETAIL = "Sessão inválida ou expirada"

def unauthenticated() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED, detail=UNAUTHENTICATED_DETAIL
    )

def get_current_user(request: Request) -> dict:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise unauthenticated()

    with db.get_conn() as conn:
        session_row = lookup_session(conn, token)
        if session_row is None:
            raise unauthenticated()
        user = conn.execute(
            "SELECT id, name, email, avatar_key FROM users WHERE id = ?",
            (session_row["user_id"],),
        ).fetchone()
        if user is None:
            raise unauthenticated()
        ctx = orgs.resolve_context(conn, int(user["id"]))

        tocar_sessao(conn, session_row)

    return {
        "id": ctx["tenant_id"],
        "actor_id": int(user["id"]),
        "name": user["name"],
        "email": user["email"],
        "org_id": ctx["org_id"],
        "org_name": ctx["org_name"],
        "role": ctx["role"],

        "avatar": (user["avatar_key"] or "") if "avatar_key" in user.keys() else "",
    }

CurrentUser = Annotated[dict, Depends(get_current_user)]
