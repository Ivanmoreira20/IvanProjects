from __future__ import annotations

import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

import auth
import billing
import config
import db
import mercadopago as mp
import plans
from auth import CurrentUser

logger = logging.getLogger("vertex.routes.billing")

router = APIRouter(prefix="/api/billing", tags=["cobranca"])

DIAS_DO_CICLO = 31

class PlanoOut(BaseModel):
    codigo: str
    nome: str
    resumo: str
    centavos: int
    preco: float
    assinavel: bool
    recursos: list[str]
    limites: dict[str, int]

class AssinaturaOut(BaseModel):
    plano: str
    plano_nome: str
    plano_contratado: str
    status: str
    vigente: bool
    tem_acesso: bool = False
    em_trial: bool
    dias_de_trial: int | None = None
    trial_ends_at: str | None = None
    current_period_end: str | None = None
    modo: str = ""
    cancela_no_fim: bool = False
    centavos: int = 0

    pode_testar: bool = False
    dias_do_teste: int = 0
    recursos: list[str]
    limites: dict[str, int]

    pagamento_ligado: bool
    pagamento_modo: str

class FaturaOut(BaseModel):
    referencia: str
    plano: str
    centavos: int
    valor: float
    moeda: str
    status: str
    metodo: str
    periodo_ate: str | None = None
    pago_em: str | None = None
    criado_em: str

class AssinarIn(BaseModel):

    plano: Literal["inicial", "pro"]
    modo: Literal["cartao", "avulso"]

class AssinarOut(BaseModel):
    link: str
    modo: str
    referencia: str

@router.get("/plans", response_model=list[PlanoOut])
def listar_planos() -> list[dict]:
    return plans.catalogo_publico()

def _estado(user_id: int) -> dict[str, Any]:
    estado = billing.assinatura(user_id)
    plano = plans.obter(estado["plano"])
    return {
        **estado,
        "plano_nome": plano.nome,
        "recursos": sorted(plano.recursos),
        "limites": dict(plano.limites),
        "pagamento_ligado": config.mp_configured(),
        "pagamento_modo": config.mp_modo(),
    }

@router.get("/me", response_model=AssinaturaOut)
def minha_assinatura(user: CurrentUser) -> dict[str, Any]:
    return _estado(int(user["id"]))

@router.get("/invoices", response_model=list[FaturaOut])
def minhas_faturas(user: CurrentUser) -> list[dict]:
    return billing.faturas(int(user["id"]))

@router.post("/assinar", response_model=AssinarOut)
async def assinar(payload: AssinarIn, user: CurrentUser) -> dict[str, Any]:
    if not config.mp_configured():
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "A cobrança ainda não está configurada neste servidor.",
        )

    user_id = int(user["id"])
    plano = plans.obter(payload.plano)
    if plano.codigo not in plans.ASSINAVEIS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Este plano não é assinável pelo site.")

    referencia = f"vertex-{user_id}-{secrets.token_hex(8)}"
    email = str(user.get("email") or "")

    try:
        if payload.modo == billing.MODO_CARTAO:
            criado = await run_in_threadpool(
                mp.criar_assinatura_cartao, email, plano.nome, plano.centavos, referencia
            )
        else:
            criado = await run_in_threadpool(
                mp.criar_cobranca_avulsa, email, plano.nome, plano.centavos, referencia
            )
    except mp.MPIndisponivel as erro:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(erro)) from erro
    except mp.MPFalhou as erro:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(erro)) from erro

    if not criado.get("link"):
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "O Mercado Pago não devolveu link de pagamento.")

    with db.get_conn() as conn:
        billing.marcar_pendente(
            conn,
            user_id,
            plano.codigo,
            payload.modo,
            mp.PROVEDOR,
            str(criado.get("id") or referencia),
        )

    logger.info(
        "Cobrança criada: conta %s, plano %s, modo %s, ref %s",
        user_id, plano.codigo, payload.modo, criado.get("id"),
    )
    return {"link": criado["link"], "modo": payload.modo, "referencia": referencia}

@router.post("/testar", response_model=AssinaturaOut)
def ativar_teste(user: CurrentUser) -> dict[str, Any]:
    user_id = int(user["id"])
    try:
        with db.get_conn() as conn:
            billing.ativar_teste(conn, user_id)
    except billing.TesteIndisponivel as erro:

        raise HTTPException(status.HTTP_409_CONFLICT, str(erro)) from erro
    logger.info("Teste de 14 dias ativado: conta %s", user_id)
    return _estado(user_id)

@router.post("/cancelar", response_model=AssinaturaOut)
async def cancelar(user: CurrentUser) -> dict[str, Any]:
    user_id = int(user["id"])
    with db.get_conn() as conn:
        linha = conn.execute(
            "SELECT provider, provider_ref, modo FROM subscriptions WHERE user_id = ?",
            (user_id,),
        ).fetchone()

    if linha and linha["provider"] == mp.PROVEDOR and linha["provider_ref"] and linha["modo"] == billing.MODO_CARTAO:
        try:
            await run_in_threadpool(mp.cancelar_assinatura, str(linha["provider_ref"]))
        except mp.MPIndisponivel:
            logger.warning("Cancelamento local sem provedor configurado (conta %s).", user_id)
        except mp.MPFalhou as erro:
            raise HTTPException(
                status.HTTP_502_BAD_GATEWAY,
                "Não foi possível cancelar no Mercado Pago. Nada foi alterado — tente de novo.",
            ) from erro

    with db.get_conn() as conn:
        billing.cancelar(conn, user_id, imediato=False)
    return _estado(user_id)

def _registrar_evento(
    conn: db.Connection,
    topic: str,
    event_id: str,
    ok: bool,
    corpo: str,
    resultado: str,
    user_id: int | None = None,
) -> bool:
    cur = conn.execute(
        """INSERT OR IGNORE INTO billing_events
               (provider, topic, event_id, signature_ok, payload, resultado, user_id, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            mp.PROVEDOR,
            topic,
            event_id,
            1 if ok else 0,
            corpo[:4000],
            resultado,
            user_id,
            billing.carimbo(),
        ),
    )
    return cur.rowcount > 0

def _plano_contratado(conn: db.Connection, user_id: int) -> str:
    row = conn.execute("SELECT plan FROM subscriptions WHERE user_id = ?", (user_id,)).fetchone()
    cod = str(row["plan"]) if row and row["plan"] else ""
    return cod if cod in plans.ASSINAVEIS else plans.PRO

def _fim_do_ciclo(dados: dict[str, Any]) -> datetime:
    bruto = (
        (dados.get("auto_recurring") or {}).get("end_date")
        or dados.get("next_payment_date")
        or ""
    )
    if bruto:
        try:
            d = datetime.fromisoformat(str(bruto).replace("Z", "+00:00"))
            if d.tzinfo is None:
                d = d.replace(tzinfo=timezone.utc)
            if d > billing.agora():
                return d
        except (TypeError, ValueError):
            pass
    return billing.agora() + timedelta(days=DIAS_DO_CICLO)

def _tratar_preapproval(ref: str) -> tuple[str, int | None]:
    dados = mp.consultar_preapproval(ref)
    status_mp = str(dados.get("status") or "")
    fim = _fim_do_ciclo(dados)

    with db.get_conn() as conn:
        user_id = billing.dono_da_referencia(conn, mp.PROVEDOR, ref)
        if user_id is None:

            return "assinatura desconhecida", None
        if status_mp in mp.PREAPPROVAL_VIVA:
            billing.ativar(
                conn, user_id, _plano_contratado(conn, user_id), fim,
                provider=mp.PROVEDOR, ref=ref, modo=billing.MODO_CARTAO,
            )
            return f"ativada ({status_mp})", user_id
        if status_mp in {"cancelled", "paused"}:
            billing.cancelar(conn, user_id, imediato=(status_mp == "cancelled"))
            return f"cancelada ({status_mp})", user_id
        return f"ignorado ({status_mp or 'sem status'})", user_id

def _tratar_pagamento(pagamento_id: str) -> tuple[str, int | None]:
    dados = mp.consultar_pagamento(pagamento_id)
    status_mp = str(dados.get("status") or "")
    centavos = int(round(float(dados.get("transaction_amount") or 0) * 100))
    metodo = str(dados.get("payment_method_id") or "")
    ref_assinatura = str(dados.get("preapproval_id") or "")
    externa = str(dados.get("external_reference") or "")
    ref_pagamento = str(dados.get("id") or pagamento_id)

    with db.get_conn() as conn:

        user_id = (
            billing.dono_da_referencia(conn, mp.PROVEDOR, ref_assinatura)
            if ref_assinatura
            else None
        )
        if user_id is None and externa:

            partes = externa.split("-")
            if len(partes) >= 3 and partes[0] == "vertex" and partes[1].isdigit():
                candidato = int(partes[1])
                if conn.execute("SELECT 1 FROM users WHERE id = ?", (candidato,)).fetchone():
                    user_id = candidato
        if user_id is None:
            return "pagamento sem conta correspondente", None

        plano_cod = _plano_contratado(conn, user_id)
        pago = status_mp == mp.PAGO
        ate = billing.agora() + timedelta(days=DIAS_DO_CICLO)
        novo = billing.registrar_fatura(
            conn, user_id, mp.PROVEDOR, ref_pagamento, plano_cod, centavos,
            "aprovado" if pago else (status_mp or "pendente"),
            metodo=metodo,
            periodo_ate=ate if pago else None,
            pago_em=billing.agora() if pago else None,
        )

        if pago:

            if novo:
                billing.ativar(
                    conn, user_id, plano_cod, ate,
                    provider=mp.PROVEDOR, ref=ref_assinatura or ref_pagamento,
                    modo=billing.MODO_CARTAO if ref_assinatura else billing.MODO_AVULSO,
                    centavos=centavos,
                )
                return "pago", user_id
            return "pago (reenvio, período não estendido)", user_id
        if status_mp in {"refunded", "charged_back"}:
            billing.vencer(conn, user_id, motivo=status_mp)
            return f"estornado ({status_mp})", user_id
        if status_mp in {"rejected", "cancelled"}:
            return f"recusado ({status_mp})", user_id
        return f"pendente ({status_mp or 'sem status'})", user_id

@router.post("/webhook")
async def webhook(request: Request, response: Response) -> dict[str, str]:
    corpo_cru = await request.body()
    try:
        corpo = await request.json()
    except Exception:
        corpo = {}

    request_id = str(request.headers.get("x-request-id", "")).strip()
    topic = str(
        corpo.get("type") or corpo.get("topic") or request.query_params.get("type") or ""
    ).strip()
    data_id = str(
        (corpo.get("data") or {}).get("id")
        or corpo.get("data.id")
        or request.query_params.get("data.id")
        or request.query_params.get("id")
        or ""
    ).strip()

    assinatura_ok = mp.verificar_assinatura(
        request.headers.get("x-signature", ""), request_id, data_id
    )

    if not assinatura_ok:

        with db.get_conn() as conn:
            _registrar_evento(
                conn,
                topic or "?",
                f"{data_id or '?'}::{secrets.token_hex(6)}",
                False,
                corpo_cru.decode("utf-8", "replace"),
                "assinatura inválida",
            )
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Assinatura do webhook inválida.")

    if not topic or not data_id:
        return {"status": "ignorado"}

    chave = f"{data_id}#{request_id or '-'}"
    with db.get_conn() as conn:
        primeira_entrega = _registrar_evento(
            conn, topic, chave, True, corpo_cru.decode("utf-8", "replace"), "recebido"
        )
    if not primeira_entrega:
        return {"status": "repetido"}

    try:
        if topic in {"subscription_preapproval", "preapproval"}:
            resultado, user_id = await run_in_threadpool(_tratar_preapproval, data_id)
        elif topic in {"payment", "subscription_authorized_payment"}:
            resultado, user_id = await run_in_threadpool(_tratar_pagamento, data_id)
        else:
            resultado, user_id = f"tópico não tratado ({topic})", None
    except (mp.MPIndisponivel, mp.MPFalhou) as erro:

        with db.get_conn() as conn:
            conn.execute(
                "DELETE FROM billing_events WHERE provider = ? AND topic = ? AND event_id = ?",
                (mp.PROVEDOR, topic, chave),
            )
        if isinstance(erro, mp.MPIndisponivel):
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(erro)) from erro

        logger.error("Webhook %s/%s falhou ao consultar a API: %s", topic, data_id, erro)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Falha ao confirmar com o Mercado Pago.") from erro

    with db.get_conn() as conn:
        conn.execute(
            "UPDATE billing_events SET resultado = ?, user_id = ? "
            "WHERE provider = ? AND topic = ? AND event_id = ?",
            (resultado, user_id, mp.PROVEDOR, topic, chave),
        )
    logger.info("Webhook %s/%s: %s", topic, data_id, resultado)
    return {"status": "ok"}
