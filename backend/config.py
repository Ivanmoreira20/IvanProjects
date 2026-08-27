from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger("vertex.config")

BASE_DIR = Path(__file__).resolve().parent
DASHBOARD_DIR = BASE_DIR.parent
PROJECT_DIR = DASHBOARD_DIR.parent

ENV_FILENAME = ".env"

DEFAULTS: dict[str, str] = {
    "GOOGLE_CLIENT_ID": "",
    "GOOGLE_CLIENT_SECRET": "",
    "GOOGLE_REDIRECT_URI": "http://127.0.0.1:8000/api/auth/google/callback",
    "SMTP_HOST": "smtp.gmail.com",
    "SMTP_PORT": "587",
    "SMTP_USER": "",
    "SMTP_PASS": "",
    "SMTP_FROM": "",
    "APP_BASE_URL": "http://127.0.0.1:8000",

    "WHATSAPP_TOKEN": "",

    "WHATSAPP_APP_SECRET": "",

    "WHATSAPP_VERIFY_TOKEN": "",

    "VERTEX_SEED": "",

    "VERTEX_OWNER_EMAILS": "",

    "VERTEX_COMP_PRO_EMAILS": "",

    "VERTEX_PAYWALL": "",

    "VERTEX_DEVICE_CHECK": "",

    "MP_ACCESS_TOKEN": "",

    "MP_PUBLIC_KEY": "",

    "MP_WEBHOOK_SECRET": "",
}

SECRET_KEYS = frozenset(
    {
        "GOOGLE_CLIENT_SECRET",
        "SMTP_PASS",
        "WHATSAPP_TOKEN",
        "WHATSAPP_APP_SECRET",
        "GEMINI_API_KEY",
        "MP_ACCESS_TOKEN",
        "MP_WEBHOOK_SECRET",
    }
)

TRUTHY = frozenset({"1", "true", "sim", "on", "yes", "y", "s"})
FALSY = frozenset({"0", "false", "nao", "não", "off", "no", "n"})

def parse_env_text(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line[:7].lower() == "export ":
            line = line[7:].lstrip()
        key, separator, value = line.partition("=")
        if not separator:
            continue
        key = key.strip()
        if not key:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        values[key] = value
    return values

def candidate_paths() -> list[Path]:
    paths: list[Path] = []
    home = os.environ.get("VERTEX_HOME", "").strip()
    if home:
        paths.append(Path(home).expanduser() / ENV_FILENAME)
    paths.append(DASHBOARD_DIR / ENV_FILENAME)
    paths.append(PROJECT_DIR / ENV_FILENAME)
    paths.append(BASE_DIR / ENV_FILENAME)
    return paths

def _load_env_file() -> tuple[dict[str, str], Path | None]:
    for path in candidate_paths():
        try:
            if not path.is_file():
                continue

            text = path.read_text(encoding="utf-8-sig", errors="replace")
        except OSError as error:
            logger.warning("Não foi possível ler %s: %s", path, error)
            continue
        return parse_env_text(text), path
    return {}, None

_FILE_VALUES, _SOURCE_PATH = _load_env_file()

def reload() -> Path | None:
    global _FILE_VALUES, _SOURCE_PATH
    _FILE_VALUES, _SOURCE_PATH = _load_env_file()
    return _SOURCE_PATH

def source_path() -> Path | None:
    return _SOURCE_PATH

def get(key: str, default: str | None = None) -> str:
    raw = os.environ.get(key)
    if raw is None or not raw.strip():
        raw = _FILE_VALUES.get(key)
    if raw is None or not raw.strip():
        raw = DEFAULTS.get(key)
    if raw is None:
        raw = default if default is not None else ""
    return raw.strip()

def get_int(key: str, default: int) -> int:
    try:
        return int(get(key) or default)
    except (TypeError, ValueError):
        logger.warning("%s não é um número inteiro válido; usando %s.", key, default)
        return default

def get_flag(key: str, default: bool) -> bool | None:
    raw = get(key).lower()
    if not raw:
        return None
    if raw in TRUTHY:
        return True
    if raw in FALSY:
        return False
    logger.warning("%s=%r não é um sim/não reconhecido; usando %s.", key, raw, default)
    return default

def google_client_id() -> str:
    return get("GOOGLE_CLIENT_ID")

def google_client_secret() -> str:
    return get("GOOGLE_CLIENT_SECRET")

def google_redirect_uri() -> str:
    return get("GOOGLE_REDIRECT_URI")

def google_enabled() -> bool:
    return bool(google_client_id()) and bool(google_client_secret())

def marketing_enabled() -> bool:
    return get_flag("MARKETING_ENABLED", False) is True

def mkt_provider() -> str:
    p = get("MKT_PROVIDER").strip().lower()
    return p or "smtp"

def mkt_smtp_daily_cap() -> int:
    return get_int("MKT_SMTP_DAILY_CAP", 50)

def brevo_api_key() -> str:
    return get("BREVO_API_KEY")

def smtp_host() -> str:
    return get("SMTP_HOST")

def smtp_port() -> int:
    return get_int("SMTP_PORT", 587)

def smtp_user() -> str:
    return get("SMTP_USER")

def smtp_password() -> str:
    return get("SMTP_PASS")

def smtp_from() -> str:
    return get("SMTP_FROM") or smtp_user()

def smtp_configured() -> bool:
    return bool(smtp_host()) and bool(smtp_user()) and bool(smtp_password())

def app_base_url() -> str:
    return get("APP_BASE_URL").rstrip("/")

def seed_flag() -> bool | None:
    return get_flag("VERTEX_SEED", default=False)

def whatsapp_configured() -> bool:
    return bool(get("WHATSAPP_TOKEN"))

def owner_emails() -> frozenset[str]:
    raw = get("VERTEX_OWNER_EMAILS")
    return frozenset(e.strip().lower() for e in raw.split(",") if e.strip())

def is_owner(email: str | None) -> bool:
    return bool(email) and email.strip().lower() in owner_emails()

def paywall_ativo() -> bool:
    return get_flag("VERTEX_PAYWALL", default=False) is True

def device_check_ativo() -> bool:
    return get_flag("VERTEX_DEVICE_CHECK", default=False) is True

def comp_pro_emails() -> frozenset[str]:
    raw = get("VERTEX_COMP_PRO_EMAILS")
    return frozenset(e.strip().lower() for e in raw.split(",") if e.strip())

def is_comp_pro(email: str | None) -> bool:
    return bool(email) and email.strip().lower() in comp_pro_emails()

GEMINI_MODEL_PADRAO = "gemini-3.1-flash-lite"

def gemini_api_key() -> str:
    return get("GEMINI_API_KEY")

def gemini_model() -> str:
    return get("GEMINI_MODEL") or GEMINI_MODEL_PADRAO

def ia_configured() -> bool:
    return bool(gemini_api_key())

def mp_access_token() -> str:
    return get("MP_ACCESS_TOKEN")

def mp_public_key() -> str:
    return get("MP_PUBLIC_KEY")

def mp_webhook_secret() -> str:
    return get("MP_WEBHOOK_SECRET")

def mp_configured() -> bool:
    return bool(mp_access_token()) and bool(mp_webhook_secret())

def mp_modo() -> str:
    token = mp_access_token()
    if not token:
        return "desligado"
    return "teste" if token.startswith("TEST-") else "producao"

def describe() -> str:
    origem = str(_SOURCE_PATH) if _SOURCE_PATH else "nenhum (usando padrões)"
    return (
        f"config: arquivo={origem} | google={'ligado' if google_enabled() else 'desligado'} "
        f"| smtp={'configurado' if smtp_configured() else 'não configurado'} "
        f"| whatsapp={'configurado' if whatsapp_configured() else 'não configurado'} "
        f"| ia={'configurada' if ia_configured() else 'não configurada'} "
        f"| pagamento={mp_modo()}"
    )
