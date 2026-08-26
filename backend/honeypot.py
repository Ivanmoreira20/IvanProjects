from __future__ import annotations

import logging
from typing import Any

import db

logger = logging.getLogger("vertex.honeypot")

HONEY_API_KEY = "vx_live_sk_9f83ac2be7d14c0aa1e6b5f2c8d4e7a1"
HONEY_ADMIN_TOKEN = "vtx_adm_7Hn2QpZ9kR4mYs6Wc1Bf0Xd8Lg3Vt5J"

DECOY_PATHS: tuple[str, ...] = (
    "/.env",
    "/api/internal/users",
    "/api/debug/config",
    "/api/v1/users",
    "/api/admin/users",
    "/api/backup",
)

HONEY_HEADERS: tuple[str, ...] = ("x-api-key", "x-admin-token", "authorization")
HONEY_QUERY_KEYS: tuple[str, ...] = ("api_key", "token", "admin_token")

_HONEY_VALUES = frozenset({HONEY_API_KEY, HONEY_ADMIN_TOKEN})

def token_isca_presente(request: Any) -> bool:
    for h in HONEY_HEADERS:
        valor = request.headers.get(h, "")
        if valor and any(tok in valor for tok in _HONEY_VALUES):
            return True
    for k in HONEY_QUERY_KEYS:
        valor = request.query_params.get(k, "")
        if valor and valor in _HONEY_VALUES:
            return True
    return False

_UA_MAX = 400
_DETAIL_MAX = 500

def record(request: Any, kind: str, detail: str = "") -> None:
    try:
        ip = request.client.host if getattr(request, "client", None) else "desconhecido"
        ua = (request.headers.get("user-agent", "") or "")[:_UA_MAX]
        path = str(getattr(request, "url", "").path if getattr(request, "url", None) else "")
        logger.warning(
            "ISCA acionada (%s): ip=%s path=%s ua=%r detalhe=%s",
            kind, ip, path, ua[:120], detail[:120],
        )
        with db.get_conn() as conn:
            conn.execute(
                "INSERT INTO security_events (kind, path, ip, user_agent, detail, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (kind, path[:200], ip, ua, detail[:_DETAIL_MAX], db.now_iso()),
            )
    except Exception:  # noqa: BLE001 -- alarme jamais derruba a requisição
        logger.exception("Falha ao registrar acesso à isca (ignorado).")

def fake_users() -> dict[str, Any]:
    return {
        "ok": True,
        "count": 3,
        "users": [
            {"id": 1, "name": "Administrator", "email": "admin@vertexcrm.tech",
             "role": "superadmin", "api_key": HONEY_API_KEY, "active": True},
            {"id": 2, "name": "Ricardo Nunes", "email": "ricardo@vertexcrm.tech",
             "role": "admin", "active": True},
            {"id": 3, "name": "Suporte", "email": "suporte@vertexcrm.tech",
             "role": "operator", "active": False},
        ],
    }

def fake_config() -> dict[str, Any]:
    return {
        "env": "production",
        "debug": False,
        "database_url": "postgres://vertex_app:Pr0d-Db-2024!@10.20.0.14:5432/vertex",
        "redis_url": "redis://10.20.0.9:6379/0",
        "api_key": HONEY_API_KEY,
        "admin_token": HONEY_ADMIN_TOKEN,
        "jwt_secret": "b7d1e0c93f2a4a6e8c15d7f0a2b4c6e8",
        "stripe_secret": "sk_live_51Jx8Kd2eZvKYlo3RFAKEfakeFAKE",
        "aws_access_key_id": "AKIAIOSFODNN7EXAMPLE",
    }

def fake_env_text() -> str:
    return (
        "# Vertex CRM — production\n"
        "APP_ENV=production\n"
        f"API_KEY={HONEY_API_KEY}\n"
        f"ADMIN_TOKEN={HONEY_ADMIN_TOKEN}\n"
        "DATABASE_URL=postgres://vertex_app:Pr0d-Db-2024!@10.20.0.14:5432/vertex\n"
        "JWT_SECRET=b7d1e0c93f2a4a6e8c15d7f0a2b4c6e8\n"
        "SMTP_PASS=Vtx-Mail-2024-Prod\n"
        "STRIPE_SECRET=sk_live_51Jx8Kd2eZvKYlo3RFAKEfakeFAKE\n"
    )

def fake_para_caminho(path: str) -> tuple[str, Any]:
    if path == "/.env":
        return "text", fake_env_text()
    if path in ("/api/debug/config", "/api/backup"):
        return "json", fake_config()
    return "json", fake_users()
