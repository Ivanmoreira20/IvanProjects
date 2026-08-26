from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from pydantic import BaseModel

import admin
import config
import db
from auth import CurrentUser

logger = logging.getLogger("vertex.routes.admin")

def get_platform_owner(user: CurrentUser) -> dict[str, Any]:
    if not config.is_owner(user.get("email")):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Não encontrado")
    return user

PlatformOwner = Annotated[dict, Depends(get_platform_owner)]

router = APIRouter(
    prefix="/api/admin",
    tags=["dono"],
    dependencies=[Depends(get_platform_owner)],
)

class OverviewOut(BaseModel):
    total_contas: int
    novas_30d: int
    ativas_30d: int
    pagantes: int
    em_trial: int
    mrr_centavos: int
    receita_mes_centavos: int
    receita_total_centavos: int
    total_leads: int
    ia_chamadas_30d: int
    ia_tokens_30d: int
    pedidos_plano: int
    pedidos_plano_30d: int
    alertas_seguranca_7d: int
    por_status: dict[str, int]
    por_plano: dict[str, int]

class AccountRow(BaseModel):
    id: int
    name: str
    email: str
    created_at: str
    auth_provider: str
    plano: str
    status: str
    vigente: bool
    em_trial: bool
    centavos: int
    n_leads: int
    n_abertos: int
    pipeline: float
    ia_chamadas: int
    ultimo_visto: str | None = None
    is_owner: bool

class AccountsOut(BaseModel):
    total: int
    items: list[AccountRow]

class LeadStatusCount(BaseModel):
    status: str
    total: int
    valor: float

class InvoiceRow(BaseModel):
    provider: str
    plan: str
    centavos: int
    currency: str
    status: str
    metodo: str
    periodo_ate: str | None = None
    paid_at: str | None = None
    created_at: str

class AccountDetailOut(BaseModel):
    id: int
    name: str
    email: str
    created_at: str
    auth_provider: str
    email_verified: bool
    plano: str
    status: str
    vigente: bool
    em_trial: bool
    centavos: int
    current_period_end: str | None = None
    n_leads: int
    pipeline: float
    ganho_total: float
    por_status: list[LeadStatusCount]
    ia_chamadas: int
    ia_tokens: int
    atividades_30d: int
    ultimo_visto: str | None = None
    faturas: list[InvoiceRow]
    is_owner: bool

class PlanInterestRow(BaseModel):
    id: int
    plan: str
    name: str
    email: str
    company: str
    phone: str
    seats: int
    message: str
    created_at: str
    conta_email: str | None = None

class PlanInterestsOut(BaseModel):
    total: int
    items: list[PlanInterestRow]

class RevenuePoint(BaseModel):
    mes: str
    centavos: int
    faturas: int

class RevenueOut(BaseModel):
    total_centavos: int
    points: list[RevenuePoint]

class SecurityEventRow(BaseModel):
    id: int
    kind: str
    path: str
    ip: str
    user_agent: str
    detail: str
    created_at: str

class SecurityEventsOut(BaseModel):
    total: int
    items: list[SecurityEventRow]

class BackupSaude(BaseModel):
    estado: str
    ultimo_em: str | None = None
    horas: float | None = None
    tamanho: int | None = None
    arquivos: int

class SaudeOut(BaseModel):
    estado: str
    gerado_em: str
    problemas: list[str]
    backup: BackupSaude
    alertas_recentes: list[str]
    uso: dict[str, int]

@router.get("/overview", response_model=OverviewOut)
def owner_overview() -> dict[str, Any]:
    with db.get_conn() as conn:
        return admin.overview(conn)

@router.get("/accounts", response_model=AccountsOut)
def owner_accounts(
    q: Annotated[str, Query(max_length=120)] = "",
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0, le=1_000_000)] = 0,
) -> dict[str, Any]:
    with db.get_conn() as conn:
        return admin.accounts(conn, q=q, limit=limit, offset=offset)

@router.get("/accounts/{user_id}", response_model=AccountDetailOut)
def owner_account(user_id: Annotated[int, Path(ge=1)]) -> dict[str, Any]:
    with db.get_conn() as conn:
        data = admin.account_detail(conn, user_id)
    if data is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conta não encontrada")
    return data

@router.get("/plan-interests", response_model=PlanInterestsOut)
def owner_plan_interests(
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> dict[str, Any]:
    with db.get_conn() as conn:
        return admin.plan_interests(conn, limit=limit)

@router.get("/revenue", response_model=RevenueOut)
def owner_revenue(
    months: Annotated[int, Query(ge=1, le=36)] = 12,
) -> dict[str, Any]:
    with db.get_conn() as conn:
        return admin.revenue_series(conn, months=months)

@router.get("/security-events", response_model=SecurityEventsOut)
def owner_security_events(
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
) -> dict[str, Any]:
    with db.get_conn() as conn:
        return admin.security_events(conn, limit=limit)

@router.get("/saude", response_model=SaudeOut)
def owner_saude() -> dict[str, Any]:
    with db.get_conn() as conn:
        return admin.saude(conn)
