from __future__ import annotations

import logging
import math
import re
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import Depends, FastAPI, HTTPException, Request, Response, status
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator
from starlette.concurrency import run_in_threadpool

import activities
import auth
import automations
import audit
import avatars
import billing
import config
import crm
import customfields
import db
import honeypot
import intel
import mailer
import marketing
import oauth
import onboarding
import orgs
import plans
import privacidade
import routes_admin
import routes_billing
import routes_crm
import routes_org
import routes_intel
import routes_marketing
import routes_sales
import seed
import whatsapp

logging.basicConfig(level=logging.INFO, format="%(levelname)s [%(name)s] %(message)s")
logger = logging.getLogger("vertex.app")

BASE_DIR = Path(__file__).resolve().parent
FRONTEND_DIR = BASE_DIR.parent / "frontend"

Status = Literal["Prospecção", "Qualificação", "Proposta", "Negociação", "Ganho", "Perdido"]
Segment = Literal[
    "SaaS", "Saúde", "Varejo", "Educação", "Indústria", "Finanças", "Serviços", "Outros"
]

MONTH_ABBR = (
    "Jan", "Fev", "Mar", "Abr", "Mai", "Jun",
    "Jul", "Ago", "Set", "Out", "Nov", "Dez",
)

EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9](?:[A-Za-z0-9.\-]*[A-Za-z0-9])?\.[A-Za-z]{2,}$")

SCHEDULER_INTERVAL = 300
SCHEDULER_LEASE = 280

async def _agendador() -> None:
    import asyncio
    import os

    holder = f"pid-{os.getpid()}"

    await asyncio.sleep(20)
    while True:
        try:
            if await run_in_threadpool(db.acquire_lease, "automations", holder, SCHEDULER_LEASE):
                resultado = await run_in_threadpool(automations.scan_all)
                if any(v for k, v in resultado.items() if k != "contas"):
                    logger.info("Varredura de automações: %s", resultado)
                await run_in_threadpool(db.purge_old_notifications)

                await run_in_threadpool(intel.recalcular_todos)

                await run_in_threadpool(billing.vencer_expirados)

                res_mkt = await run_in_threadpool(marketing.drenar)
                if res_mkt.get("enviadas") or res_mkt.get("falhas"):
                    logger.info("Marketing: %s", res_mkt)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - o laco nao pode morrer
            logger.exception("Falha na varredura de automações.")
        await asyncio.sleep(SCHEDULER_INTERVAL)

@asynccontextmanager
async def lifespan(_: FastAPI):
    import asyncio

    db.init_db()
    db.purge_expired_sessions()
    db.purge_expired_email_codes()
    auth.purge_expired_rate_hits()
    auth.warm_dummy_hash()
    created = seed.seed_if_empty()
    if created:
        logger.info("Perfis de demonstração criados (ana / bruno / carla @vertex.test).")

    faltando = orgs.ensure_backfill()
    if faltando:
        logger.info("Organizações criadas no backfill: %d conta(s).", faltando)

    logger.info(db.describe_backend())
    logger.info(config.describe())
    if not config.google_enabled():
        logger.info(
            "Login com Google desligado: preencha GOOGLE_CLIENT_ID e GOOGLE_CLIENT_SECRET "
            "no .env para habilitar. /api/config informa isso ao frontend."
        )
    if not mailer.is_configured():
        logger.warning(
            "SMTP não configurado: os códigos de verificação serão IMPRESSOS NO CONSOLE "
            "deste servidor em vez de enviados por e-mail. Preencha SMTP_USER/SMTP_PASS no .env."
        )
    if not FRONTEND_DIR.is_dir():
        logger.error(
            "Pasta do frontend não encontrada em %s. A API continua funcionando em /api/*, "
            "mas nenhuma página será servida até que a pasta exista.",
            FRONTEND_DIR,
        )
    elif not (FRONTEND_DIR / "index.html").exists():
        logger.warning(
            "%s existe mas ainda não tem index.html. A API está no ar; a interface "
            "aparecerá assim que o arquivo for criado.",
            FRONTEND_DIR,
        )
    if not whatsapp.is_configured():
        logger.info(
            "WhatsApp desligado: preencha WHATSAPP_TOKEN (e WHATSAPP_APP_SECRET / "
            "WHATSAPP_VERIFY_TOKEN para receber mensagens) no .env. A tela de "
            "Configurações mostra exatamente o que falta."
        )

    tarefa = asyncio.create_task(_agendador())
    try:
        yield
    finally:
        tarefa.cancel()
        try:
            await tarefa
        except asyncio.CancelledError:
            pass

app = FastAPI(
    title="Vertex CRM API",
    version="1.0.0",
    summary="CRM multiusuário com sessões opacas, CSRF double-submit e isolamento por conta.",
    lifespan=lifespan,
)

UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

CSRF_EXEMPT_PATHS = frozenset(
    {
        "/api/auth/login",
        "/api/auth/register",
        "/api/auth/verify",
        "/api/auth/resend",

        "/api/auth/forgot",
        "/api/auth/reset",

        "/api/plan-interest",

        "/api/whatsapp/webhook",

        "/api/billing/webhook",

        "/api/marketing/unsubscribe",
    }
)

CSRF_EXEMPT_PREFIXES: tuple[str, ...] = ("/api/public/",)

SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; font-src 'self'; connect-src 'self'; "
        "frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
    ),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "same-origin",
    "Permissions-Policy": "geolocation=(), microphone=(), camera=()",
}

def _check_csrf(token: str | None, header_value: str | None) -> tuple[int, str] | None:
    if not token:
        return status.HTTP_401_UNAUTHORIZED, auth.UNAUTHENTICATED_DETAIL
    with db.get_conn() as conn:
        session_row = auth.lookup_session(conn, token)
        if session_row is None:
            return status.HTTP_401_UNAUTHORIZED, auth.UNAUTHENTICATED_DETAIL
        if not auth.verify_csrf(session_row, header_value):
            return status.HTTP_403_FORBIDDEN, "Token CSRF ausente ou inválido"
    return None

@app.middleware("http")
async def csrf_middleware(request: Request, call_next):
    path = request.url.path
    if (
        request.method in UNSAFE_METHODS
        and path.startswith("/api/")
        and path not in CSRF_EXEMPT_PATHS
        and not path.startswith(CSRF_EXEMPT_PREFIXES)
    ):
        failure = await run_in_threadpool(
            _check_csrf,
            request.cookies.get(auth.SESSION_COOKIE),
            request.headers.get(auth.CSRF_HEADER),
        )
        if failure is not None:
            code, detail = failure
            return JSONResponse({"detail": detail}, status_code=code)
    return await call_next(request)

RECURSO_POR_PREFIXO: tuple[tuple[str, str], ...] = (
    ("/api/automations", plans.AUTOMACOES),
    ("/api/automation-runs", plans.AUTOMACOES),
    ("/api/whatsapp", plans.WHATSAPP),
    ("/api/ai/", plans.IA),
    ("/api/reports/advanced", plans.RELATORIOS_AVANCADOS),

    ("/api/proposals", plans.PROPOSTAS),
)

RECURSO_POR_SUFIXO: tuple[tuple[str, str], ...] = (("/whatsapp", plans.WHATSAPP),)

PORTAO_ISENTO = frozenset({"/api/whatsapp/webhook"})

def _recurso_do_caminho(path: str) -> str | None:
    if path in PORTAO_ISENTO:
        return None
    for prefixo, recurso in RECURSO_POR_PREFIXO:
        if path.startswith(prefixo):
            return recurso
    for sufixo, recurso in RECURSO_POR_SUFIXO:
        if path.endswith(sufixo) and path.startswith("/api/leads/"):
            return recurso
    return None

ACESSO_ISENTO_PREFIXOS: tuple[str, ...] = (

    "/api/auth/",

    "/api/billing/",

    "/api/admin/",

    "/api/me/",

    "/api/public/",
)

ACESSO_ISENTO_EXATOS: frozenset[str] = frozenset(
    {
        "/api/config",
        "/api/health",
        "/api/me",
        "/api/plan-interest",
        "/api/whatsapp/webhook",

        "/api/org/invites/accept",
    }
)

def _exige_assinatura(path: str) -> bool:
    if not path.startswith("/api/"):
        return False
    if path in ACESSO_ISENTO_EXATOS:
        return False
    return not path.startswith(ACESSO_ISENTO_PREFIXOS)

def _corpo_sem_assinatura(estado: dict[str, Any]) -> dict[str, Any]:
    inicial = plans.obter(plans.INICIAL)
    return {
        "detail": "Escolha um plano para continuar usando o Vertex.",
        "erro": "assinatura_necessaria",
        "status_assinatura": estado.get("status", ""),
        "pode_testar": bool(estado.get("pode_testar")),
        "dias_do_teste": int(estado.get("dias_do_teste") or 0),
        "plano_sugerido": inicial.codigo,
        "plano_sugerido_nome": inicial.nome,
        "plano_sugerido_centavos": inicial.centavos,
    }

def _checar_portao(token: str | None, recurso: str | None, exige_acesso: bool) -> dict[str, Any] | None:
    if not token:
        return None
    with db.get_conn() as conn:
        sessao = auth.lookup_session(conn, token)
        if sessao is None:
            return None

        ctx = orgs.resolve_context(conn, int(sessao["user_id"]))
        estado = billing.assinatura_conn(conn, ctx["tenant_id"])

    if exige_acesso and not estado.get("tem_acesso"):
        return _corpo_sem_assinatura(estado)

    if recurso is None:
        return None
    plano = plans.obter(estado["plano"])
    if plano.libera(recurso):
        return None
    return {
        "detail": f"O plano {plano.nome} não inclui {plans.NOME_DO_RECURSO.get(recurso, recurso)}.",
        "erro": "plano_nao_inclui",
        "recurso": recurso,
        "plano": plano.codigo,
        "plano_nome": plano.nome,
    }

@app.middleware("http")
async def plano_middleware(request: Request, call_next):
    caminho = request.url.path
    recurso = _recurso_do_caminho(caminho)
    exige_acesso = config.paywall_ativo() and _exige_assinatura(caminho)
    if recurso is not None or exige_acesso:
        corpo = await run_in_threadpool(
            _checar_portao, request.cookies.get(auth.SESSION_COOKIE), recurso, exige_acesso
        )
        if corpo is not None:

            return JSONResponse(corpo, status_code=status.HTTP_402_PAYMENT_REQUIRED)
    return await call_next(request)

@app.middleware("http")
async def security_headers_middleware(request: Request, call_next):
    response = await call_next(request)
    for header, value in SECURITY_HEADERS.items():
        response.headers[header] = value
    if request.url.path.startswith("/api/") and "cache-control" not in response.headers:

        response.headers["Cache-Control"] = "no-store"
    return response

@app.middleware("http")
async def honeytoken_middleware(request: Request, call_next):
    if honeypot.token_isca_presente(request):
        await run_in_threadpool(honeypot.record, request, "honeytoken", request.url.path)
    return await call_next(request)

def _clean(value: str, label: str, max_len: int) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise ValueError(f"{label} não pode ficar em branco")
    if len(cleaned) > max_len:
        raise ValueError(f"{label} deve ter no máximo {max_len} caracteres")
    return cleaned

def _clean_email(value: str) -> str:
    cleaned = value.strip().lower()
    if not EMAIL_RE.match(cleaned) or len(cleaned) > 254:
        raise ValueError("E-mail inválido")
    return cleaned

_FONE_LIXO = re.compile(r"[^\d+()\-\s]")

def _clean_fone(value: str) -> str:
    limpo = _FONE_LIXO.sub("", value or "").strip()
    return limpo if any(c.isdigit() for c in limpo) else ""

class RegisterIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=8, max_length=128)
    remember: bool = False

    @field_validator("name")
    @classmethod
    def _v_name(cls, v: str) -> str:
        return _clean(v, "Nome", 80)

    @field_validator("email")
    @classmethod
    def _v_email(cls, v: str) -> str:
        return _clean_email(v)

class LoginIn(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=128)
    remember: bool = False

    @field_validator("email")
    @classmethod
    def _v_email(cls, v: str) -> str:
        return v.strip().lower()

class VerifyIn(BaseModel):

    email: str = Field(min_length=3, max_length=254)
    code: str = Field(min_length=1, max_length=32)
    remember: bool = False

    @field_validator("email")
    @classmethod
    def _v_email(cls, v: str) -> str:
        return v.strip().lower()

    @field_validator("code")
    @classmethod
    def _v_code(cls, v: str) -> str:
        return "".join(character for character in v if character.isdigit())

class ResendIn(BaseModel):
    email: str = Field(min_length=3, max_length=254)

    @field_validator("email")
    @classmethod
    def _v_email(cls, v: str) -> str:
        return v.strip().lower()

class ProfileUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=80)

    @field_validator("name")
    @classmethod
    def _v_name(cls, v: str) -> str:
        return _clean(v, "Nome", 80)

class LeadCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    company: str = Field(min_length=1, max_length=80)
    value: float = Field(default=0.0, ge=0, le=1e9)
    status: Status = "Prospecção"
    segment: Segment = "Outros"
    email: str = Field(default="", max_length=254)
    phone: str = Field(default="", max_length=32)
    whatsapp: str = Field(default="", max_length=32)
    source: str = Field(default="", max_length=60)
    owner: str = Field(default="", max_length=80)
    notes: str = Field(default="", max_length=4000)
    tags: list[str] = Field(default_factory=list)

    custom: dict[str, Any] = Field(default_factory=dict)

    @field_validator("name")
    @classmethod
    def _v_name(cls, v: str) -> str:
        return _clean(v, "Nome do lead", 80)

    @field_validator("company")
    @classmethod
    def _v_company(cls, v: str) -> str:
        return _clean(v, "Empresa", 80)

    @field_validator("email")
    @classmethod
    def _v_email(cls, v: str) -> str:
        limpo = (v or "").strip().lower()
        if limpo and not EMAIL_RE.match(limpo):
            raise ValueError("E-mail do lead inválido")
        return limpo

    @field_validator("source", "owner", "notes")
    @classmethod
    def _v_texto(cls, v: str) -> str:
        return (v or "").strip()

    @field_validator("phone", "whatsapp")
    @classmethod
    def _v_fone(cls, v: str) -> str:
        return _clean_fone(v)

    @field_validator("tags")
    @classmethod
    def _v_tags(cls, v: list[str]) -> list[str]:
        limpas: list[str] = []
        vistas: set[str] = set()
        for tag in v:
            texto = str(tag).strip()[:40]
            chave = texto.casefold()
            if texto and chave not in vistas:
                vistas.add(chave)
                limpas.append(texto)
        if len(limpas) > 20:
            raise ValueError("No máximo 20 tags por lead")
        return limpas

class LeadUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    company: str | None = Field(default=None, min_length=1, max_length=80)
    value: float | None = Field(default=None, ge=0, le=1e9)
    status: Status | None = None
    segment: Segment | None = None
    email: str | None = Field(default=None, max_length=254)
    phone: str | None = Field(default=None, max_length=32)
    whatsapp: str | None = Field(default=None, max_length=32)
    source: str | None = Field(default=None, max_length=60)
    owner: str | None = Field(default=None, max_length=80)
    notes: str | None = Field(default=None, max_length=4000)
    tags: list[str] | None = None
    custom: dict[str, Any] | None = None

    lost_reason: str = Field(default="", max_length=60)
    lost_note: str = Field(default="", max_length=500)

    @field_validator("name")
    @classmethod
    def _v_name(cls, v: str | None) -> str | None:
        return None if v is None else _clean(v, "Nome do lead", 80)

    @field_validator("company")
    @classmethod
    def _v_company(cls, v: str | None) -> str | None:
        return None if v is None else _clean(v, "Empresa", 80)

    @field_validator("email")
    @classmethod
    def _v_email(cls, v: str | None) -> str | None:
        if v is None:
            return None
        limpo = v.strip().lower()
        if limpo and not EMAIL_RE.match(limpo):
            raise ValueError("E-mail do lead inválido")
        return limpo

    @field_validator("phone", "whatsapp")
    @classmethod
    def _v_fone(cls, v: str | None) -> str | None:
        return None if v is None else _clean_fone(v)

    @field_validator("tags")
    @classmethod
    def _v_tags(cls, v: list[str] | None) -> list[str] | None:
        return None if v is None else LeadCreate._v_tags(v)

class UserOut(BaseModel):

    id: int
    name: str
    email: str

    is_owner: bool = False

    avatar: str = ""

    role: str = "admin"
    org_name: str = ""

class VerificationSentOut(BaseModel):

    status: Literal["verification_sent"]
    email: str

class ConfigOut(BaseModel):
    google_enabled: bool
    email_verification: bool

    email_delivery: Literal["smtp", "console"] = "console"

    whatsapp_server_ready: bool = False

    ia_server_ready: bool = False

    marketing_enabled: bool = False

class NextActionOut(BaseModel):

    id: int
    kind: str
    title: str
    due_at: str | None
    atrasada: bool

class LeadOut(BaseModel):
    id: int
    name: str
    company: str
    value: float
    status: str
    segment: str
    email: str
    phone: str
    whatsapp: str
    source: str
    notes: str
    tags: list[str]
    owner: str

    owner_user_id: int | None = None

    score: int | None = None
    score_band: str = ""
    lost_reason: str
    lost_note: str
    closed_at: str | None
    last_activity_at: str | None
    stage_changed_at: str | None
    created_at: str
    updated_at: str
    custom: dict[str, Any] = {}

    next_action: NextActionOut | None = None

class Kpis(BaseModel):
    receita_total: float
    leads_ativos: int
    fechados: int
    propostas: int
    ticket_medio: float
    taxa_conversao: float

class MonthlyPoint(BaseModel):
    label: str
    value: float
    count: int

class SegmentSlice(BaseModel):
    label: str
    value: float
    count: int
    percent: float

class FunnelStage(BaseModel):
    status: str
    count: int
    value: float
    percent: float

class StatsOut(BaseModel):
    has_data: bool
    kpis: Kpis
    monthly: list[MonthlyPoint]
    segments: list[SegmentSlice]
    funnel: list[FunnelStage]

class FollowUpItem(BaseModel):
    lead_id: int
    name: str
    company: str
    value: float
    status: str
    segment: str
    days: int
    severity: Literal["alta", "media", "baixa"]
    reason: str
    """Frase pronta em português: o front não deve remontar a explicação."""
    rule: Literal[
        "proposta_parada", "negociacao_parada", "sem_contato",
        "negocio_parado", "alto_valor_parado",
    ]

class FollowUpsOut(BaseModel):
    has_data: bool
    total: int
    value_at_risk: float
    items: list[FollowUpItem]

class PassoOut(BaseModel):
    id: str
    titulo: str
    porque: str
    acao: str
    rota: str
    feito: bool

class OnboardingOut(BaseModel):

    passos: list[PassoOut]
    total: int
    concluidos: int
    completo: bool
    leads: int
    dispensado: bool

    foco_lead_id: int | None = None

    oculto_auto: bool = False

class DispensarIn(BaseModel):
    dispensar: bool = True

class PlanInterestIn(BaseModel):

    plan: Literal["pro", "empresa"]
    name: str = Field(min_length=1, max_length=80)
    email: str = Field(min_length=3, max_length=254)
    company: str = Field(default="", max_length=120)
    phone: str = Field(default="", max_length=32)
    seats: int = Field(default=1, ge=1, le=10_000)
    message: str = Field(default="", max_length=1000)

    @field_validator("name")
    @classmethod
    def _v_name(cls, v: str) -> str:
        return _clean(v, "Nome", 80)

    @field_validator("email")
    @classmethod
    def _v_email(cls, v: str) -> str:
        return _clean_email(v)

    @field_validator("company", "phone", "message")
    @classmethod
    def _v_texto(cls, v: str) -> str:
        return v.strip()

class PlanInterestOut(BaseModel):
    ok: bool
    message: str

CurrentUser = Annotated[dict, Depends(auth.get_current_user)]

def _largest_remainder(values: list[float], decimals: int = 1) -> list[float]:
    if not values:
        return []
    total = sum(values)
    if total <= 0:
        return [0.0] * len(values)

    scale = 10**decimals
    target = 100 * scale
    raw = [v / total * target for v in values]
    floors = [math.floor(x) for x in raw]
    leftover = target - sum(floors)
    order = sorted(range(len(raw)), key=lambda i: raw[i] - floors[i], reverse=True)
    for offset in range(max(leftover, 0)):
        floors[order[offset % len(order)]] += 1
    return [f / scale for f in floors]

def _last_six_months(reference) -> list[tuple[int, int]]:
    buckets: list[tuple[int, int]] = []
    for back in range(5, -1, -1):
        month = reference.month - back
        year = reference.year
        while month <= 0:
            month += 12
            year -= 1
        buckets.append((year, month))
    return buckets

def _empty_stats() -> dict[str, Any]:
    return {
        "has_data": False,
        "kpis": {
            "receita_total": 0.0,
            "leads_ativos": 0,
            "fechados": 0,
            "propostas": 0,
            "ticket_medio": 0.0,
            "taxa_conversao": 0.0,
        },
        "monthly": [],
        "segments": [],
        "funnel": [],
    }

class SaudeOut(BaseModel):
    ok: bool
    banco: bool

@app.get("/api/health", response_model=SaudeOut)
def health(response: Response) -> dict[str, bool]:
    try:
        with db.get_conn() as conn:
            conn.execute("SELECT 1").fetchone()
        banco = True
    except Exception:  # noqa: BLE001 -- qualquer falha aqui e' "banco fora"
        logger.exception("Health check: o banco nao respondeu.")
        banco = False

    if not banco:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {"ok": banco, "banco": banco}

@app.get("/api/config", response_model=ConfigOut)
def public_config() -> dict[str, Any]:
    return {
        "google_enabled": config.google_enabled(),
        "email_verification": True,
        "email_delivery": "smtp" if mailer.is_configured() else "console",
        "whatsapp_server_ready": whatsapp.is_configured(),
        "ia_server_ready": config.ia_configured(),
        "marketing_enabled": config.marketing_enabled(),
    }

@app.post(
    "/api/auth/register",
    response_model=VerificationSentOut,
    status_code=status.HTTP_202_ACCEPTED,
)
def register(payload: RegisterIn, request: Request) -> dict[str, Any]:
    auth.enforce_register_rate_limit(request)

    password_hash = auth.hash_password(payload.password)
    with db.get_conn() as conn:
        existing = conn.execute(
            "SELECT id, email_verified FROM users WHERE email = ?", (payload.email,)
        ).fetchone()

        if existing is not None and int(existing["email_verified"] or 0) == 1:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Já existe uma conta com este e-mail",
            )

        if existing is not None:
            user_id = int(existing["id"])
            conn.execute(
                "UPDATE users SET name = ?, password_hash = ? WHERE id = ?",
                (payload.name, password_hash, user_id),
            )
        else:
            cursor = conn.execute(
                """INSERT INTO users (email, name, password_hash, created_at,
                                      email_verified, auth_provider)
                   VALUES (?, ?, ?, ?, 0, 'password')""",
                (payload.email, payload.name, password_hash, db.now_iso()),
            )
            user_id = int(cursor.lastrowid)

        code = auth.issue_email_code(conn, user_id)

    mailer.send_verification_code(payload.email, payload.name, code)
    return {"status": "verification_sent", "email": payload.email}

@app.post("/api/auth/verify", response_model=UserOut)
def verify_email(payload: VerifyIn, request: Request, response: Response) -> dict[str, Any]:
    auth.enforce_verify_rate_limit(request, payload.email)

    failure: str | None = None
    result: dict[str, Any] | None = None
    token = csrf_token = ""

    with db.get_conn() as conn:
        user = conn.execute(
            "SELECT id, name, email FROM users WHERE email = ?", (payload.email,)
        ).fetchone()

        if user is None:
            failure = auth.GENERIC_CODE_ERROR
        else:
            user_id = int(user["id"])
            accepted, message = auth.consume_email_code(conn, user_id, payload.code)
            if not accepted:
                failure = message
            else:
                conn.execute(
                    "UPDATE users SET email_verified = 1 WHERE id = ?", (user_id,)
                )

                billing.garantir(conn, user_id)

                orgs.ensure_org_for_user(conn, user_id, user["name"])
                ctx = orgs.resolve_context(conn, user_id)
                previous = request.cookies.get(auth.SESSION_COOKIE)
                if previous:
                    auth.delete_session_by_token(conn, previous)
                token, csrf_token = auth.create_session(
                    conn, user_id, payload.remember,
                    auth.rotulo_do_aparelho(request.headers.get("user-agent")),
                )
                result = {
                    "id": user_id,
                    "name": user["name"],
                    "email": user["email"],
                    "is_owner": config.is_owner(user["email"]),
                    "role": ctx["role"],
                    "org_name": ctx["org_name"],
                }

    if failure is not None or result is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=failure or auth.GENERIC_CODE_ERROR,
        )

    auth.clear_login_rate_limit(request, payload.email)
    auth.set_session_cookies(request, response, token, csrf_token, payload.remember)
    return result

@app.post(
    "/api/auth/resend",
    response_model=VerificationSentOut,
    status_code=status.HTTP_202_ACCEPTED,
)
def resend_code(payload: ResendIn) -> dict[str, Any]:
    auth.enforce_resend_rate_limit(payload.email)

    delivery: tuple[str, str, str] | None = None
    with db.get_conn() as conn:
        user = conn.execute(
            "SELECT id, name, email_verified FROM users WHERE email = ?", (payload.email,)
        ).fetchone()
        if user is not None and int(user["email_verified"] or 0) == 0:
            code = auth.issue_email_code(conn, int(user["id"]))
            delivery = (payload.email, user["name"], code)

    if delivery is not None:
        mailer.send_verification_code(*delivery)
    return {"status": "verification_sent", "email": payload.email}

@app.post("/api/auth/login", response_model=UserOut)
def login(payload: LoginIn, request: Request, response: Response) -> Any:
    auth.enforce_login_rate_limit(request, payload.email)

    invalid = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED, detail=auth.GENERIC_LOGIN_ERROR
    )

    pending_email: str | None = None
    result: dict[str, Any] | None = None
    token = csrf_token = ""

    with db.get_conn() as conn:
        user = conn.execute(
            "SELECT id, name, email, password_hash, email_verified FROM users WHERE email = ?",
            (payload.email,),
        ).fetchone()

        if user is None:

            auth.burn_password_time()
            raise invalid
        if not auth.has_usable_password(user["password_hash"]):

            auth.burn_password_time()
            raise invalid
        if not auth.verify_password(payload.password, user["password_hash"]):
            raise invalid

        if int(user["email_verified"] or 0) == 0:

            pending_email = user["email"]
        else:

            previous = request.cookies.get(auth.SESSION_COOKIE)
            if previous:
                auth.delete_session_by_token(conn, previous)
            token, csrf_token = auth.create_session(
                conn, int(user["id"]), payload.remember,
                auth.rotulo_do_aparelho(request.headers.get("user-agent")),
            )
            ctx = orgs.resolve_context(conn, int(user["id"]))
            result = {
                "id": int(user["id"]),
                "name": user["name"],
                "email": user["email"],
                "is_owner": config.is_owner(user["email"]),
                "role": ctx["role"],
                "org_name": ctx["org_name"],
            }

    auth.clear_login_rate_limit(request, payload.email)

    if pending_email is not None:
        return JSONResponse(
            {"detail": "email_not_verified", "email": pending_email},
            status_code=status.HTTP_403_FORBIDDEN,
        )

    assert result is not None
    auth.set_session_cookies(request, response, token, csrf_token, payload.remember)
    return result

@app.post("/api/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(request: Request) -> Response:
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    token = request.cookies.get(auth.SESSION_COOKIE)
    if token:
        with db.get_conn() as conn:
            auth.delete_session_by_token(conn, token)
    auth.clear_session_cookies(request, response)
    return response

@app.get("/api/auth/me", response_model=UserOut)
def me(user: CurrentUser) -> dict[str, Any]:

    return {
        "id": user["actor_id"],
        "name": user["name"],
        "email": user["email"],
        "is_owner": config.is_owner(user["email"]),
        "role": user["role"],
        "org_name": user["org_name"],
        "avatar": user.get("avatar", ""),
    }

@app.patch("/api/me", response_model=UserOut)
def update_me(payload: ProfileUpdate, user: CurrentUser) -> dict[str, Any]:

    with db.get_conn() as conn:
        conn.execute("UPDATE users SET name = ? WHERE id = ?", (payload.name, user["actor_id"]))
    return {
        "id": user["actor_id"],
        "name": payload.name,
        "email": user["email"],
        "is_owner": config.is_owner(user["email"]),
        "role": user["role"],
        "org_name": user["org_name"],
        "avatar": user.get("avatar", ""),
    }

RESET_BUCKET = "reset"
RESET_LIMIT = 5
RESET_WINDOW = 60 * 60

SENHA_BUCKET = "senha"
SENHA_LIMIT = 10
SENHA_WINDOW = 60 * 60

class EsqueciIn(BaseModel):

    email: str = Field(min_length=3, max_length=254)

    @field_validator("email")
    @classmethod
    def _v_email(cls, v: str) -> str:
        return _clean_email(v)

class RedefinirIn(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    code: str = Field(min_length=6, max_length=6)
    password: str = Field(min_length=8, max_length=128)

    @field_validator("email")
    @classmethod
    def _v_email(cls, v: str) -> str:
        return _clean_email(v)

class TrocarSenhaIn(BaseModel):
    senha_atual: str = Field(min_length=1, max_length=128)
    senha_nova: str = Field(min_length=8, max_length=128)

@app.post("/api/auth/forgot", status_code=status.HTTP_202_ACCEPTED)
def esqueci_a_senha(payload: EsqueciIn, request: Request) -> dict[str, str]:
    resposta = {"status": "enviado_se_existir"}
    email = payload.email.strip().lower()

    if auth._register_hit(RESET_BUCKET, f"{auth.rate_limit_ip(request)}|{email}",
                          RESET_LIMIT, RESET_WINDOW):
        logger.info("pedido de redefinição barrado por limite (%s)", email)
        return resposta

    codigo = ""
    nome = ""
    with db.get_conn() as conn:
        user = conn.execute(
            "SELECT id, name, password_hash FROM users WHERE email = ?", (email,)
        ).fetchone()
        if user is not None and auth.has_usable_password(user["password_hash"]):
            codigo = auth.issue_email_code(conn, int(user["id"]), RESET_PURPOSE)
            nome = user["name"]

    if codigo:
        mailer.send_verification_code(email, nome, codigo, tipo=RESET_PURPOSE)
    return resposta

@app.post("/api/auth/reset", status_code=status.HTTP_204_NO_CONTENT)
def redefinir_senha(payload: RedefinirIn, request: Request) -> Response:
    email = payload.email.strip().lower()
    if auth._register_hit(RESET_BUCKET, f"{auth.rate_limit_ip(request)}|{email}",
                          RESET_LIMIT * 2, RESET_WINDOW):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Muitas tentativas. Peça um novo código em alguns minutos.",
        )

    erro = ""
    with db.get_conn() as conn:
        user = conn.execute(
            "SELECT id, password_hash FROM users WHERE email = ?", (email,)
        ).fetchone()
        if user is None or not auth.has_usable_password(user["password_hash"]):

            erro = auth.GENERIC_CODE_ERROR
        else:
            ok, motivo = auth.consume_email_code(
                conn, int(user["id"]), payload.code, RESET_PURPOSE
            )
            if not ok:
                erro = motivo
            else:
                conn.execute(
                    "UPDATE users SET password_hash = ?, email_verified = 1 WHERE id = ?",
                    (auth.hash_password(payload.password), int(user["id"])),
                )

                conn.execute("DELETE FROM sessions WHERE user_id = ?", (int(user["id"]),))

    if erro:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=erro)

    logger.info("senha redefinida por código de e-mail (%s)", email)

    with db.get_conn() as conn:
        conn.execute(
            "INSERT INTO security_events (kind, path, ip, user_agent, detail, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                "senha_redefinida",
                "/api/auth/reset",
                auth.rate_limit_ip(request),
                auth.rotulo_do_aparelho(request.headers.get("user-agent")),
                email,
                db.now_iso(),
            ),
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)

@app.post("/api/me/password", status_code=status.HTTP_204_NO_CONTENT)
def trocar_senha(
    payload: TrocarSenhaIn, user: CurrentUser, request: Request, response: Response
) -> Response:
    actor = int(user["actor_id"])
    if auth._register_hit(SENHA_BUCKET, str(actor), SENHA_LIMIT, SENHA_WINDOW):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Muitas tentativas. Tente novamente em alguns minutos.",
        )

    atual_token = request.cookies.get(auth.SESSION_COOKIE) or ""
    with db.get_conn() as conn:
        linha = conn.execute(
            "SELECT password_hash FROM users WHERE id = ?", (actor,)
        ).fetchone()
        if linha is None or not auth.has_usable_password(linha["password_hash"]):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Esta conta entra pelo Google e não tem senha para trocar.",
            )
        if not auth.verify_password(payload.senha_atual, linha["password_hash"]):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="A senha atual está incorreta."
            )
        conn.execute(
            "UPDATE users SET password_hash = ? WHERE id = ?",
            (auth.hash_password(payload.senha_nova), actor),
        )

        sessao = auth.lookup_session(conn, atual_token) if atual_token else None
        if sessao is not None:
            conn.execute(
                "DELETE FROM sessions WHERE user_id = ? AND id <> ?", (actor, int(sessao["id"]))
            )
        else:
            conn.execute("DELETE FROM sessions WHERE user_id = ?", (actor,))
        audit.log(conn, user, audit.SENHA_ALTERADA, target_type="user", target_id=actor)

    logger.info("senha alterada pela própria conta %s", actor)
    return Response(status_code=status.HTTP_204_NO_CONTENT)

EMAIL_BUCKET = "trocaemail"
EMAIL_LIMIT = 5
EMAIL_WINDOW = 60 * 60

class TrocarEmailIn(BaseModel):
    novo_email: str = Field(min_length=3, max_length=254)
    senha: str = Field(min_length=1, max_length=128)

    @field_validator("novo_email")
    @classmethod
    def _v_email(cls, v: str) -> str:
        return _clean_email(v)

class ConfirmarEmailIn(BaseModel):
    code: str = Field(min_length=6, max_length=6)

@app.post("/api/me/email", status_code=status.HTTP_202_ACCEPTED)
def pedir_troca_de_email(payload: TrocarEmailIn, user: CurrentUser) -> dict[str, str]:
    actor = int(user["actor_id"])
    if auth._register_hit(EMAIL_BUCKET, str(actor), EMAIL_LIMIT, EMAIL_WINDOW):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Muitos pedidos de troca. Tente novamente em alguns minutos.",
        )

    novo = payload.novo_email
    codigo = ""
    nome = ""
    with db.get_conn() as conn:
        linha = conn.execute(
            "SELECT name, email, password_hash FROM users WHERE id = ?", (actor,)
        ).fetchone()
        if linha is None or not auth.has_usable_password(linha["password_hash"]):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Esta conta entra pelo Google. O e-mail é gerenciado por lá.",
            )
        if not auth.verify_password(payload.senha, linha["password_hash"]):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="A senha está incorreta."
            )
        if novo == (linha["email"] or "").strip().lower():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Esse já é o e-mail da sua conta.",
            )

        existe = conn.execute(
            "SELECT 1 FROM users WHERE email = ? AND id <> ?", (novo, actor)
        ).fetchone()
        if existe is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Esse e-mail já está em uso por outra conta.",
            )

        nome = linha["name"]
        codigo = auth.issue_email_code(conn, actor, TROCA_EMAIL_PURPOSE)

        conn.execute("DELETE FROM email_changes WHERE user_id = ?", (actor,))
        conn.execute(
            "INSERT INTO email_changes (user_id, novo_email, created_at) VALUES (?, ?, ?)",
            (actor, novo, db.now_iso()),
        )

    mailer.send_verification_code(novo, nome, codigo, tipo=TROCA_EMAIL_PURPOSE)
    return {"status": "codigo_enviado", "email": novo}

@app.post("/api/me/email/confirm", response_model=UserOut)
def confirmar_troca_de_email(payload: ConfirmarEmailIn, user: CurrentUser) -> dict[str, Any]:
    actor = int(user["actor_id"])
    erro = ""
    novo_email = ""
    with db.get_conn() as conn:
        pedido = conn.execute(
            "SELECT novo_email FROM email_changes WHERE user_id = ?", (actor,)
        ).fetchone()
        if pedido is None:
            erro = "Não há troca de e-mail pendente."
        else:
            ok, motivo = auth.consume_email_code(conn, actor, payload.code, TROCA_EMAIL_PURPOSE)
            if not ok:
                erro = motivo
            else:
                novo_email = pedido["novo_email"]

                tomado = conn.execute(
                    "SELECT 1 FROM users WHERE email = ? AND id <> ?", (novo_email, actor)
                ).fetchone()
                if tomado is not None:
                    erro = "Esse e-mail foi cadastrado por outra pessoa enquanto você confirmava."
                    novo_email = ""
                else:
                    conn.execute(
                        "UPDATE users SET email = ?, email_verified = 1 WHERE id = ?",
                        (novo_email, actor),
                    )
                    conn.execute("DELETE FROM email_changes WHERE user_id = ?", (actor,))
                    audit.log(conn, user, audit.EMAIL_ALTERADO, target_type="user", target_id=actor)

    if erro:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=erro)

    logger.info("e-mail da conta %s alterado", actor)
    return {
        "id": actor,
        "name": user["name"],
        "email": novo_email,
        "is_owner": config.is_owner(novo_email),
        "role": user["role"],
        "org_name": user["org_name"],
        "avatar": user.get("avatar", ""),
    }

class SessaoOut(BaseModel):
    id: int
    device: str
    created_at: str
    last_seen_at: str
    expires_at: str

    atual: bool

class SessoesOut(BaseModel):
    items: list[SessaoOut]

@app.get("/api/me/sessions", response_model=SessoesOut)
def listar_sessoes(user: CurrentUser, request: Request) -> dict[str, Any]:
    actor = int(user["actor_id"])
    token = request.cookies.get(auth.SESSION_COOKIE) or ""
    with db.get_conn() as conn:
        atual = auth.lookup_session(conn, token) if token else None
        id_atual = int(atual["id"]) if atual is not None else -1
        linhas = conn.execute(
            """SELECT id, device, created_at, last_seen_at, expires_at
                 FROM sessions
                WHERE user_id = ? AND datetime(expires_at) > datetime(?)
             ORDER BY datetime(last_seen_at) DESC, id DESC
                LIMIT 50""",
            (actor, db.now_iso()),
        ).fetchall()
    return {
        "items": [
            {
                "id": int(r["id"]),
                "device": r["device"] or "Aparelho desconhecido",
                "created_at": r["created_at"],
                "last_seen_at": r["last_seen_at"] or r["created_at"],
                "expires_at": r["expires_at"],
                "atual": int(r["id"]) == id_atual,
            }
            for r in linhas
        ]
    }

@app.delete("/api/me/sessions", response_model=SessoesOut)
def encerrar_outras_sessoes(user: CurrentUser, request: Request) -> dict[str, Any]:
    actor = int(user["actor_id"])
    token = request.cookies.get(auth.SESSION_COOKIE) or ""
    with db.get_conn() as conn:
        atual = auth.lookup_session(conn, token) if token else None
        if atual is None:

            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="Sessão não reconhecida."
            )
        cur = conn.execute(
            "DELETE FROM sessions WHERE user_id = ? AND id <> ?", (actor, int(atual["id"]))
        )
        removidas = cur.rowcount or 0
        if removidas:
            audit.log(conn, user, audit.SESSOES_ENCERRADAS, target_type="user",
                      target_id=actor, detail=str(removidas))
    logger.info("conta %s encerrou %s outra(s) sessão(ões)", actor, removidas)
    return listar_sessoes(user, request)

RESET_PURPOSE = "reset_password"
TROCA_EMAIL_PURPOSE = "change_email"

AVATAR_BUCKET = "avatar"
AVATAR_LIMIT = 12
AVATAR_WINDOW = 60 * 60

EXPORT_BUCKET = "export"
EXPORT_LIMIT = 12
EXPORT_WINDOW = 60 * 60

LEAD_BUCKET = "lead"
LEAD_LIMIT = 60
LEAD_WINDOW = 60

class AvatarIn(BaseModel):

    imagem: str = Field(min_length=8, max_length=avatars.MAX_BASE64 + 1024)

class AvatarOut(BaseModel):
    avatar: str

@app.post("/api/me/avatar", response_model=AvatarOut)
def upload_avatar(payload: AvatarIn, user: CurrentUser, request: Request) -> dict[str, str]:
    if auth._register_hit(AVATAR_BUCKET, str(user["actor_id"]), AVATAR_LIMIT, AVATAR_WINDOW):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Muitas trocas de foto seguidas. Tente novamente mais tarde.",
        )

    actor = int(user["actor_id"])
    try:
        chave_nova = avatars.salvar(actor, payload.imagem)
    except avatars.AvatarInvalido as erro:

        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(erro)) from erro

    with db.get_conn() as conn:
        linha = conn.execute("SELECT avatar_key FROM users WHERE id = ?", (actor,)).fetchone()
        chave_antiga = (linha["avatar_key"] if linha else "") or ""
        conn.execute("UPDATE users SET avatar_key = ? WHERE id = ?", (chave_nova, actor))
        audit.log(conn, user, audit.FOTO_TROCADA, target_type="user", target_id=actor)

    if chave_antiga and chave_antiga != chave_nova:
        avatars.remover(actor, chave_antiga)
    return {"avatar": chave_nova}

@app.delete("/api/me/avatar", response_model=AvatarOut)
def remover_avatar(user: CurrentUser) -> dict[str, str]:
    actor = int(user["actor_id"])
    with db.get_conn() as conn:
        linha = conn.execute("SELECT avatar_key FROM users WHERE id = ?", (actor,)).fetchone()
        chave = (linha["avatar_key"] if linha else "") or ""
        conn.execute("UPDATE users SET avatar_key = '' WHERE id = ?", (actor,))
        if chave:
            audit.log(conn, user, audit.FOTO_REMOVIDA, target_type="user", target_id=actor)
    if chave:
        avatars.remover(actor, chave)
    return {"avatar": ""}

@app.get("/api/avatars/{user_id}")
def ver_avatar(user_id: int, user: CurrentUser) -> Response:
    actor = int(user["actor_id"])
    with db.get_conn() as conn:
        if user_id != actor:
            mesmo_time = conn.execute(
                """SELECT 1 FROM memberships meu
                     JOIN memberships dele ON dele.org_id = meu.org_id
                    WHERE meu.user_id = ? AND dele.user_id = ?""",
                (actor, user_id),
            ).fetchone()
            if mesmo_time is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Foto não encontrada.")
        linha = conn.execute("SELECT avatar_key FROM users WHERE id = ?", (user_id,)).fetchone()

    chave = (linha["avatar_key"] if linha else "") or ""
    dados = avatars.ler(user_id, chave) if chave else None
    if dados is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Foto não encontrada.")
    return Response(
        content=dados,
        media_type="image/webp",
        headers={

            "Cache-Control": "private, max-age=86400, immutable",
            "X-Content-Type-Options": "nosniff",
        },
    )

@app.get("/api/me/export")
def export_my_data(user: CurrentUser) -> JSONResponse:

    if auth._register_hit(EXPORT_BUCKET, str(user["actor_id"]), EXPORT_LIMIT, EXPORT_WINDOW):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Muitas exportações seguidas. Tente novamente em uma hora.",
        )
    with db.get_conn() as conn:
        dados = privacidade.exportar(conn, user["actor_id"])
    return JSONResponse(
        dados,
        headers={"Content-Disposition": 'attachment; filename="meus-dados-vertex.json"'},
    )

class DeleteAccountIn(BaseModel):
    password: str = Field(default="", max_length=128)
    confirm: str = Field(default="", max_length=40)

@app.post("/api/me/delete", status_code=status.HTTP_204_NO_CONTENT)
def delete_my_account(payload: DeleteAccountIn, user: CurrentUser, request: Request) -> Response:
    with db.get_conn() as conn:
        linha = conn.execute(
            "SELECT password_hash FROM users WHERE id = ?", (user["actor_id"],)
        ).fetchone()
        armazenada = linha["password_hash"] if linha else ""
        if auth.has_usable_password(armazenada):
            if not auth.verify_password(payload.password, armazenada):
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Senha incorreta.")
        elif payload.confirm.strip().upper() != "EXCLUIR":

            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Para confirmar a exclusão, digite EXCLUIR.",
            )

        if user["actor_id"] == user["id"]:
            outros = conn.execute(
                "SELECT COUNT(*) AS c FROM memberships WHERE org_id = ? AND user_id <> ?",
                (user["org_id"], user["actor_id"]),
            ).fetchone()["c"]
            if outros:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Remova os outros membros da equipe antes de excluir a conta.",
                )
        privacidade.excluir(conn, user["actor_id"])

    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    auth.clear_session_cookies(request, response)
    return response

app.include_router(oauth.router)

class LeadsOut(BaseModel):

    items: list[LeadOut]
    total: int
    truncado: bool
    teto: int

@app.get("/api/leads", response_model=LeadsOut)
def list_leads(user: CurrentUser) -> dict[str, Any]:
    escopo = orgs.escopo_owner(user)
    with db.get_conn() as conn:
        total = crm.contar_leads(conn, user["id"], escopo)
        rows = crm.list_leads(conn, user["id"], escopo)
        leads = [crm.lead_to_dict(row) for row in rows]
        ids = [lead["id"] for lead in leads]

        personalizados = customfields.values_for_many(conn, user["id"], "lead", ids)

        proximas = activities.next_action_for_many(conn, user["id"], ids)
    for lead in leads:
        lead["custom"] = personalizados.get(lead["id"], {})
        lead["next_action"] = proximas.get(lead["id"])
    return {
        "items": leads,
        "total": total,
        "truncado": total > len(leads),
        "teto": crm.TETO_LISTA,
    }

@app.get("/api/leads/{lead_id}", response_model=LeadOut)
def get_lead(lead_id: int, user: CurrentUser) -> dict[str, Any]:
    with db.get_conn() as conn:

        lead = crm.lead_to_dict(crm.fetch_lead(conn, lead_id, user["id"], orgs.escopo_owner(user)))
        lead["custom"] = customfields.values_for(conn, user["id"], "lead", lead_id)
        lead["next_action"] = activities.next_action_for(conn, user["id"], lead_id)
    return lead

class ValorEventoOut(BaseModel):
    de: float
    para: float
    note: str
    created_at: str

class NegociacaoOut(BaseModel):
    valor_inicial: float
    valor_atual: float
    variacao: float
    variacao_pct: float
    eventos: list[ValorEventoOut]

@app.get("/api/leads/{lead_id}/negociacao", response_model=NegociacaoOut)
def lead_negociacao(lead_id: int, user: CurrentUser) -> dict[str, Any]:
    with db.get_conn() as conn:

        crm.fetch_lead(conn, lead_id, user["id"], orgs.escopo_owner(user))
        return crm.negociacao(conn, user["id"], lead_id)

def _erro_motivo(erro: crm.LossReasonRequired) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail={"message": "Informe o motivo da perda", "loss_reasons": erro.opcoes},
    )

@app.post("/api/leads", response_model=LeadOut, status_code=status.HTTP_201_CREATED)
def create_lead(payload: LeadCreate, user: CurrentUser) -> dict[str, Any]:
    moment = db.now_iso()
    if payload.status == "Perdido":

        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Um lead não pode ser criado já como Perdido. Crie-o e depois marque a perda com o motivo.",
        )

    if auth._register_hit(LEAD_BUCKET, str(user["actor_id"]), LEAD_LIMIT, LEAD_WINDOW):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Muitos cadastros em sequência. Espere um minuto — ou use a importação por CSV para trazer vários de uma vez.",
        )

    with db.get_conn() as conn:

        if crm.espaco_para_leads(conn, user["id"]) < 1:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"Esta conta atingiu o limite de {crm.TETO_LEADS_CONTA:,} negócios. "
                    "Fale com a gente pelo contato@vertexcrm.tech para uma operação maior."
                ).replace(",", "."),
            )
        cursor = conn.execute(
            """INSERT INTO leads
                   (user_id, name, company, value, status, segment, email, phone,
                    whatsapp, source, owner, owner_user_id, notes, tags,
                    stage_changed_at, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                user["id"], payload.name, payload.company, float(payload.value),
                payload.status, payload.segment, payload.email, payload.phone,
                payload.whatsapp, payload.source, payload.owner,

                user["actor_id"], payload.notes,
                db.json_dump(payload.tags), moment, moment, moment,
            ),
        )
        lead_id = int(cursor.lastrowid)

        try:
            customfields.set_values(conn, user["id"], "lead", lead_id, payload.custom)
        except ValueError as erro:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(erro)) from None

        activities.log(
            conn, user["id"], lead_id=lead_id, kind="criacao",
            title="Lead criado", detail=f"Etapa inicial: {payload.status}", source="user",
        )
        activities.notify(
            conn, user["id"], type="lead_novo", title=f"Novo lead: {payload.name}",
            body=payload.company, severity="info", ref_type="lead", ref_id=lead_id,
            dedup_key=f"leadnovo:{lead_id}",
        )
        crm.dispatch_events(conn, user["id"], lead_id, [("lead.criado", {})])
        intel.marcar(conn, user["id"], "primeiro_lead")
        intel.marcar(conn, user["id"], "primeiro_negocio")

        intel.recalcular(conn, user["id"])

        lead = crm.lead_to_dict(crm.fetch_lead(conn, lead_id, user["id"]))
        lead["custom"] = customfields.values_for(conn, user["id"], "lead", lead_id)
        lead["next_action"] = activities.next_action_for(conn, user["id"], lead_id)
        return lead

@app.patch("/api/leads/{lead_id}", response_model=LeadOut)
def update_lead(lead_id: int, payload: LeadUpdate, user: CurrentUser) -> dict[str, Any]:
    changes = payload.model_dump(exclude_unset=True, exclude_none=True)
    changes.pop("lost_reason", None)
    changes.pop("lost_note", None)
    if not changes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Informe ao menos um campo para atualizar",
        )

    with db.get_conn() as conn:

        antes = crm.fetch_lead(conn, lead_id, user["id"], orgs.escopo_owner(user))
        novo_status = changes.pop("status", None)
        personalizados = changes.pop("custom", None)
        if "tags" in changes:
            changes["tags"] = db.json_dump(changes["tags"])

        eventos: list[tuple[str, dict]] = []

        if novo_status is not None:
            try:
                eventos = crm.change_status(
                    conn, user["id"], lead_id, novo_status,
                    lost_reason=payload.lost_reason, lost_note=payload.lost_note,
                )
            except crm.LossReasonRequired as erro:
                raise _erro_motivo(erro) from None

        if changes:

            allowed = ("name", "company", "value", "segment", "email", "phone",
                       "whatsapp", "source", "owner", "notes", "tags")
            assignments = [f"{column} = ?" for column in allowed if column in changes]
            if assignments:
                params: list[Any] = [changes[column] for column in allowed if column in changes]
                assignments.append("updated_at = ?")
                params.extend([db.now_iso(), lead_id, user["id"]])
                conn.execute(
                    f"UPDATE leads SET {', '.join(assignments)} WHERE id = ? AND user_id = ?",
                    params,
                )

            if "value" in changes and float(changes["value"]) != float(antes["value"]):
                crm.log_value_change(
                    conn, user["id"], lead_id, float(antes["value"]), float(changes["value"])
                )

        if personalizados is not None:
            try:
                customfields.set_values(conn, user["id"], "lead", lead_id, personalizados)
            except ValueError as erro:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(erro)) from None

        crm.dispatch_events(conn, user["id"], lead_id, eventos)

        lead = crm.lead_to_dict(crm.fetch_lead(conn, lead_id, user["id"]))
        lead["custom"] = customfields.values_for(conn, user["id"], "lead", lead_id)
        lead["next_action"] = activities.next_action_for(conn, user["id"], lead_id)
        return lead

class LeadOwnerIn(BaseModel):

    owner_user_id: int | None = None

@app.patch("/api/leads/{lead_id}/owner", response_model=LeadOut)
def assign_lead_owner(lead_id: int, payload: LeadOwnerIn, user: CurrentUser) -> dict[str, Any]:
    orgs.exigir(user, orgs.ATRIBUIR_LEAD)
    with db.get_conn() as conn:
        antes = crm.fetch_lead(conn, lead_id, user["id"])
        nome_novo_dono = ""
        if payload.owner_user_id is not None:
            membro = conn.execute(
                "SELECT u.name FROM memberships m JOIN users u ON u.id = m.user_id"
                " WHERE m.org_id = ? AND m.user_id = ?",
                (user["org_id"], payload.owner_user_id),
            ).fetchone()
            if membro is None:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Essa pessoa não faz parte da sua equipe.",
                )
            nome_novo_dono = membro["name"] or ""
        conn.execute(
            "UPDATE leads SET owner_user_id = ?, updated_at = ? WHERE id = ? AND user_id = ?",
            (payload.owner_user_id, db.now_iso(), lead_id, user["id"]),
        )
        titulo_lead = antes["name"] if antes is not None else ""
        if payload.owner_user_id is None:
            audit.log(
                conn, user, audit.LEAD_SEM_DONO,
                target_type="lead", target_id=lead_id, target_label=titulo_lead,
            )
        else:
            audit.log(
                conn, user, audit.LEAD_ATRIBUIDO,
                target_type="lead", target_id=lead_id, target_label=titulo_lead,
                detail=nome_novo_dono,
            )
        lead = crm.lead_to_dict(crm.fetch_lead(conn, lead_id, user["id"]))
        lead["custom"] = customfields.values_for(conn, user["id"], "lead", lead_id)
        lead["next_action"] = activities.next_action_for(conn, user["id"], lead_id)
        return lead

class LeadDeleteInfo(BaseModel):

    atividades: int
    propostas: int
    mensagens: int
    propostas_aceitas: int

@app.get("/api/leads/{lead_id}/impact", response_model=LeadDeleteInfo)
def lead_delete_impact(lead_id: int, user: CurrentUser) -> dict[str, int]:
    with db.get_conn() as conn:
        crm.fetch_lead(conn, lead_id, user["id"], orgs.escopo_owner(user))
        def conta(sql: str) -> int:
            return int(conn.execute(sql, (lead_id, user["id"])).fetchone()["t"])

        return {
            "atividades": conta("SELECT COUNT(*) AS t FROM activities WHERE lead_id = ? AND user_id = ?"),
            "propostas": conta("SELECT COUNT(*) AS t FROM proposals WHERE lead_id = ? AND user_id = ?"),
            "mensagens": conta("SELECT COUNT(*) AS t FROM wa_messages WHERE lead_id = ? AND user_id = ?"),
            "propostas_aceitas": conta(
                "SELECT COUNT(*) AS t FROM proposals WHERE lead_id = ? AND user_id = ? AND status = 'Aceita'"
            ),
        }

@app.delete("/api/leads/{lead_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_lead(lead_id: int, user: CurrentUser) -> Response:
    with db.get_conn() as conn:
        scope = orgs.escopo_owner(user)
        antes = crm.fetch_lead(conn, lead_id, user["id"], scope)

        if scope is not None and antes["owner_user_id"] != user["actor_id"]:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Você só pode excluir os leads dos quais você é o responsável.",
            )

        customfields.delete_values(conn, user["id"], "lead", lead_id)
        conn.execute("DELETE FROM leads WHERE id = ? AND user_id = ?", (lead_id, user["id"]))
    return Response(status_code=status.HTTP_204_NO_CONTENT)

@app.get("/api/stats", response_model=StatsOut)
def stats(user: CurrentUser) -> dict[str, Any]:
    user_id = user["id"]

    vis, vp = orgs.clausula_visibilidade(orgs.escopo_owner(user))
    with db.get_conn() as conn:
        totals = conn.execute(
            f"""SELECT COUNT(*) AS total,
                      COALESCE(SUM(value), 0) AS receita,
                      COALESCE(SUM(CASE WHEN status = 'Ganho'   THEN 1 ELSE 0 END), 0) AS fechados,
                      COALESCE(SUM(CASE WHEN status = 'Perdido' THEN 1 ELSE 0 END), 0) AS perdidos,
                      COALESCE(SUM(CASE WHEN status IN ('Proposta', 'Negociação')
                                        THEN 1 ELSE 0 END), 0) AS propostas,
                      COALESCE(SUM(CASE WHEN status NOT IN ('Ganho', 'Perdido')
                                        THEN 1 ELSE 0 END), 0) AS ativos
               FROM leads WHERE user_id = ?{vis}""",
            (user_id, *vp),
        ).fetchone()

        total_leads = int(totals["total"])
        if total_leads == 0:

            return _empty_stats()

        segment_rows = conn.execute(
            f"""SELECT segment, COALESCE(SUM(value), 0) AS valor, COUNT(*) AS quantidade
               FROM leads WHERE user_id = ?{vis}
               GROUP BY segment
               ORDER BY valor DESC, quantidade DESC, segment ASC""",
            (user_id, *vp),
        ).fetchall()

        funnel_rows = conn.execute(
            f"""SELECT status, COUNT(*) AS quantidade, COALESCE(SUM(value), 0) AS valor
               FROM leads WHERE user_id = ?{vis}
               GROUP BY status""",
            (user_id, *vp),
        ).fetchall()

        monthly_rows = conn.execute(
            f"SELECT created_at, value FROM leads WHERE user_id = ?{vis}", (user_id, *vp)
        ).fetchall()

    receita_total = round(float(totals["receita"]), 2)
    fechados = int(totals["fechados"])
    perdidos = int(totals["perdidos"])
    propostas = int(totals["propostas"])
    ativos = int(totals["ativos"])

    decididos = fechados + perdidos

    kpis = {
        "receita_total": receita_total,
        "leads_ativos": ativos,
        "fechados": fechados,
        "propostas": propostas,
        "ticket_medio": round(receita_total / total_leads, 2),
        "taxa_conversao": round(fechados / decididos * 100, 1) if decididos else 0.0,
    }

    seg_values = [float(row["valor"]) for row in segment_rows]
    basis = seg_values if sum(seg_values) > 0 else [float(row["quantidade"]) for row in segment_rows]
    seg_percents = _largest_remainder(basis)
    segments = [
        {
            "label": row["segment"],
            "value": round(float(row["valor"]), 2),
            "count": int(row["quantidade"]),
            "percent": percent,
        }
        for row, percent in zip(segment_rows, seg_percents)
    ]

    by_status = {row["status"]: row for row in funnel_rows}
    counts = [int(by_status[s]["quantidade"]) if s in by_status else 0 for s in db.STATUSES]
    funnel_percents = _largest_remainder([float(c) for c in counts])
    funnel = [
        {
            "status": stage,
            "count": counts[index],
            "value": round(float(by_status[stage]["valor"]), 2) if stage in by_status else 0.0,
            "percent": funnel_percents[index],
        }
        for index, stage in enumerate(db.STATUSES)
    ]

    buckets = _last_six_months(db.utcnow())
    aggregated: dict[tuple[int, int], list[float]] = {key: [0.0, 0.0] for key in buckets}
    for row in monthly_rows:
        try:
            created = db.parse_iso(row["created_at"])
        except ValueError:
            continue
        key = (created.year, created.month)
        if key in aggregated:
            aggregated[key][0] += float(row["value"])
            aggregated[key][1] += 1
    monthly = [
        {
            "label": f"{MONTH_ABBR[month - 1]}/{year % 100:02d}",
            "value": round(aggregated[(year, month)][0], 2),
            "count": int(aggregated[(year, month)][1]),
        }
        for year, month in buckets
    ]

    return {
        "has_data": True,
        "kpis": kpis,
        "monthly": monthly,
        "segments": segments,
        "funnel": funnel,
    }

FOLLOWUP_RULES: tuple[tuple[str, int, str, str], ...] = (

    ("Proposta", 5, "proposta_parada", "alta"),

    ("Negociação", 3, "negociacao_parada", "alta"),
    ("Qualificação", 7, "negocio_parado", "media"),
    ("Prospecção", 7, "sem_contato", "media"),
)

FOLLOWUP_ALTO_VALOR = 30_000.0
FOLLOWUP_ALTO_VALOR_DIAS = 4

FOLLOWUP_LIMITE = 50

def _dias_desde(carimbo: str, agora) -> int:
    try:
        momento = db.parse_iso(carimbo)
    except (TypeError, ValueError):
        return 0
    return max(0, (agora - momento).days)

def _frase_do_alerta(regra: str, dias: int, status: str) -> str:
    plural = "s" if dias != 1 else ""
    if regra == "proposta_parada":
        return f"Proposta enviada há {dias} dia{plural}, sem resposta."
    if regra == "negociacao_parada":
        return f"Em negociação há {dias} dia{plural} sem avanço."
    if regra == "alto_valor_parado":
        return f"Negócio de valor alto parado há {dias} dia{plural} em {status}."
    if regra == "sem_contato":
        return f"Sem contato há {dias} dia{plural}."
    return f"Parado em {status} há {dias} dia{plural}."

@app.get("/api/followups", response_model=FollowUpsOut)
def followups(user: CurrentUser) -> dict[str, Any]:
    agora = db.utcnow()
    prazos = {status: dias for status, dias, _, _ in FOLLOWUP_RULES}
    regras = {status: (regra, base) for status, _, regra, base in FOLLOWUP_RULES}

    vis, vp = orgs.clausula_visibilidade(orgs.escopo_owner(user))
    with db.get_conn() as conn:
        linhas = conn.execute(

            f"""SELECT id, name, company, value, status, segment,
                      COALESCE(last_activity_at, created_at) AS ultimo_contato
                 FROM leads
                WHERE user_id = ? AND status NOT IN ('Ganho', 'Perdido'){vis}
             ORDER BY value DESC""",
            (user["id"], *vp),
        ).fetchall()

    itens: list[dict[str, Any]] = []
    for linha in linhas:
        status = linha["status"]
        prazo = prazos.get(status)
        if prazo is None:
            continue

        dias = _dias_desde(linha["ultimo_contato"], agora)
        valor = round(float(linha["value"]), 2)
        regra, gravidade = regras[status]

        alto_valor = valor >= FOLLOWUP_ALTO_VALOR and dias >= FOLLOWUP_ALTO_VALOR_DIAS
        if dias < prazo and not alto_valor:
            continue
        if alto_valor and dias < prazo:
            regra, gravidade = "alto_valor_parado", "alta"
        elif alto_valor:
            gravidade = "alta"

        itens.append(
            {
                "lead_id": int(linha["id"]),
                "name": linha["name"],
                "company": linha["company"],
                "value": valor,
                "status": status,
                "segment": linha["segment"],
                "days": dias,
                "severity": gravidade,
                "reason": _frase_do_alerta(regra, dias, status),
                "rule": regra,
            }
        )

    peso = {"alta": 0, "media": 1, "baixa": 2}
    itens.sort(key=lambda i: (peso[i["severity"]], -i["days"], -i["value"]))

    return {
        "has_data": bool(itens),
        "total": len(itens),
        "value_at_risk": round(sum(i["value"] for i in itens), 2),
        "items": itens[:FOLLOWUP_LIMITE],
    }

@app.get("/api/onboarding", response_model=OnboardingOut)
def onboarding_estado(user: CurrentUser) -> dict[str, Any]:
    with db.get_conn() as conn:
        return onboarding.calcular(conn, user)

@app.post("/api/onboarding/dispensar", response_model=OnboardingOut)
def onboarding_dispensar(payload: DispensarIn, user: CurrentUser) -> dict[str, Any]:
    with db.get_conn() as conn:
        onboarding.dispensar(conn, user, payload.dispensar)
        return onboarding.calcular(conn, user)

@app.post("/api/plan-interest", response_model=PlanInterestOut, status_code=status.HTTP_201_CREATED)
def plan_interest(payload: PlanInterestIn, request: Request) -> dict[str, Any]:

    auth.enforce_plan_interest_rate_limit(request)

    user_id = None
    token = request.cookies.get(auth.SESSION_COOKIE)
    if token:
        with db.get_conn() as conn:
            sessao = auth.lookup_session(conn, token)
            if sessao is not None:
                user_id = int(sessao["user_id"])

    dados = payload.model_dump()
    dados["user_id"] = user_id

    with db.get_conn() as conn:
        conn.execute(
            """INSERT INTO plan_interests
                   (user_id, plan, name, email, company, phone, seats, message, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                user_id, payload.plan, payload.name, payload.email, payload.company,
                payload.phone, payload.seats, payload.message, db.now_iso(),
            ),
        )

    logger.info("Pedido do plano %s registrado para %s.", payload.plan, payload.email)

    mailer.send_plan_interest(dados)

    return {
        "ok": True,
        "message": "Pedido recebido. Entramos em contato pelo e-mail informado em até 1 dia útil.",
    }

app.include_router(routes_admin.router)
app.include_router(routes_org.router)
app.include_router(routes_billing.router)
app.include_router(routes_crm.router)
app.include_router(routes_intel.router)
app.include_router(routes_sales.router)
app.include_router(routes_sales.public_router)
app.include_router(routes_marketing.router)
app.include_router(routes_marketing.router_pub)

def _isca(request: Request) -> Response:
    caminho = request.url.path
    honeypot.record(request, "decoy_path", caminho)
    tipo, corpo = honeypot.fake_para_caminho(caminho)
    if tipo == "text":
        return PlainTextResponse(corpo)
    return JSONResponse(corpo)

for _caminho_isca in honeypot.DECOY_PATHS:
    app.get(_caminho_isca, include_in_schema=False)(_isca)

CACHE_LONGO = "public, max-age=31536000, immutable"
CACHE_REVALIDA = "no-cache"

class EstaticosComCache(StaticFiles):
    def file_response(self, *args: Any, **kwargs: Any) -> Response:
        resposta = super().file_response(*args, **kwargs)

        escopo = args[2] if len(args) > 2 else kwargs.get("scope", {})
        consulta = (escopo or {}).get("query_string", b"") or b""

        tipo = (resposta.headers.get("content-type") or "").lower()
        e_html = tipo.startswith("text/html")
        carimbado = b"v=" in consulta and not e_html
        resposta.headers["Cache-Control"] = CACHE_LONGO if carimbado else CACHE_REVALIDA
        return resposta

PAGINAS = {
    "/app": "app.html",
    "/como-funciona": "como-funciona.html",
    "/planos": "planos.html",
    "/termos": "termos.html",
    "/privacidade": "privacidade.html",
    "/descadastro": "descadastro.html",
}

def _pagina(nome: str) -> FileResponse:
    caminho = FRONTEND_DIR / nome
    if not caminho.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Página não encontrada")

    return FileResponse(caminho, media_type="text/html", headers={"Cache-Control": "no-cache"})

if FRONTEND_DIR.is_dir():

    @app.get("/proposta/{token}", include_in_schema=False)
    def pagina_proposta(token: str) -> FileResponse:
        return _pagina("proposta.html")

    for _rota, _arquivo in PAGINAS.items():

        def _rota_pagina(arquivo: str = _arquivo) -> FileResponse:
            return _pagina(arquivo)

        app.api_route(_rota, methods=["GET", "HEAD"], include_in_schema=False)(_rota_pagina)

        app.api_route(_rota + "/", methods=["GET", "HEAD"], include_in_schema=False)(
            lambda rota=_rota: RedirectResponse(rota, status_code=status.HTTP_308_PERMANENT_REDIRECT)
        )

    app.mount("/", EstaticosComCache(directory=str(FRONTEND_DIR), html=True), name="frontend")
else:
    logger.error(
        "Frontend não montado: a pasta %s não existe. Crie-a (index.html, css/, js/) "
        "e reinicie o servidor. A API em /api/* continua disponível.",
        FRONTEND_DIR,
    )

    @app.get("/", include_in_schema=False)
    def frontend_missing() -> JSONResponse:
        return JSONResponse(
            {
                "detail": "Frontend ainda não disponível. Esperado em "
                f"{FRONTEND_DIR}. A API está no ar em /api/*."
            },
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        )
