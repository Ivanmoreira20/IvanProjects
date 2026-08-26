from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field, field_validator
from starlette.concurrency import run_in_threadpool

import activities
import ai
import config
import db
import intel
import orgs
from auth import CurrentUser

logger = logging.getLogger("vertex.routes.intel")

router = APIRouter(prefix="/api", tags=["inteligencia"])

def _so_visiveis(conn: db.Connection, user: dict, leads: list[dict]) -> list[dict]:
    scope = orgs.escopo_owner(user)
    if scope is None:
        return leads
    vis, vp = orgs.clausula_visibilidade(scope)
    ids = {
        r["id"]
        for r in conn.execute(
            f"SELECT id FROM leads WHERE user_id = ?{vis}", (user["id"], *vp)
        ).fetchall()
    }
    return [l for l in leads if l["id"] in ids]

class FatorOut(BaseModel):
    nome: str
    pontos: float
    maximo: int
    texto: str

class RiscoOut(BaseModel):
    codigo: str
    gravidade: str
    texto: str

class SugestaoOut(BaseModel):
    acao: str
    em_dias: int
    quando: str
    porque: str

class LeadIntelOut(BaseModel):
    id: int
    name: str
    company: str
    value: float
    status: str
    score: int | None
    banda: str
    dias_sem_contato: int | None
    proposta_status: str
    fatores: list[FatorOut]
    riscos: list[RiscoOut]
    sugestao: SugestaoOut | None

class ResumoIntelOut(BaseModel):
    prioridades: list[LeadIntelOut]
    riscos: list[LeadIntelOut]
    contagem: dict[str, int]
    valor_em_risco: float
    calculado_em: str

def _lead_publico(lead: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": lead["id"],
        "name": lead["name"],
        "company": lead["company"],
        "value": lead["value"],
        "status": lead["status"],
        "score": lead["score"],
        "banda": lead["banda"],
        "dias_sem_contato": lead["dias_sem_contato"],
        "proposta_status": lead["proposta_status"],
        "fatores": lead["fatores"],
        "riscos": lead["riscos"],
        "sugestao": lead["sugestao"],
    }

def _dias_desde(carimbo: str | None, agora: datetime) -> int:
    momento = db.try_parse_iso(carimbo) if carimbo else None
    return 0 if momento is None else max(0, (agora - momento).days)

class SemAcaoItem(BaseModel):
    lead_id: int
    name: str
    company: str
    value: float
    status: str
    segment: str
    dias_parado: int

class SemAcaoOut(BaseModel):
    has_data: bool
    total: int
    valor_parado: float
    items: list[SemAcaoItem]

LIMITE_SEM_ACAO = 50

@router.get("/intel/sem-proxima-acao", response_model=SemAcaoOut)
def sem_proxima_acao(user: CurrentUser) -> dict[str, Any]:
    agora = db.utcnow()
    with db.get_conn() as conn:
        linhas = activities.leads_without_next_action(
            conn, user["id"], owner_scope=orgs.escopo_owner(user)
        )

    itens = [
        {
            "lead_id": int(l["id"]),
            "name": l["name"],
            "company": l["company"],
            "value": round(float(l["value"]), 2),
            "status": l["status"],
            "segment": l["segment"],
            "dias_parado": _dias_desde(l["ultimo_contato"], agora),
        }
        for l in linhas
    ]
    return {
        "has_data": bool(itens),
        "total": len(itens),
        "valor_parado": round(sum(i["value"] for i in itens), 2),
        "items": itens[:LIMITE_SEM_ACAO],
    }

@router.get("/intel/resumo", response_model=ResumoIntelOut)
def intel_resumo(user: CurrentUser) -> dict[str, Any]:
    with db.get_conn() as conn:
        dados = intel.analisar(conn, user["id"])
        intel.gravar_scores(conn, user["id"], dados["leads"])
        leads_visiveis = _so_visiveis(conn, user, dados["leads"])

    abertos = [l for l in leads_visiveis if l["status"] in db.OPEN_STATUSES]
    abertos.sort(key=lambda l: (l["score"] or 0), reverse=True)

    com_risco = [l for l in abertos if l["riscos"]]
    com_risco.sort(
        key=lambda l: (
            intel.GRAVIDADE_ORDEM.get(l["riscos"][0]["gravidade"], 9),
            -(l["value"] or 0),
        )
    )

    contagem = {faixa: 0 for faixa in intel.FAIXAS}
    for lead in abertos:
        if lead["banda"] in contagem:
            contagem[lead["banda"]] += 1

    return {
        "prioridades": [_lead_publico(l) for l in abertos[:12]],
        "riscos": [_lead_publico(l) for l in com_risco[:12]],
        "contagem": contagem,
        "valor_em_risco": round(sum(l["value"] for l in com_risco), 2),
        "calculado_em": db.now_iso(),
    }

@router.get("/intel/leads", response_model=list[LeadIntelOut])
def intel_leads(
    user: CurrentUser,
    banda: Literal["alta", "media", "baixa", ""] = "",
    limite: int = Query(default=100, ge=1, le=500),
) -> list[dict[str, Any]]:
    with db.get_conn() as conn:
        dados = intel.analisar(conn, user["id"])
        intel.gravar_scores(conn, user["id"], dados["leads"])
        leads_visiveis = _so_visiveis(conn, user, dados["leads"])

    abertos = [l for l in leads_visiveis if l["status"] in db.OPEN_STATUSES]
    if banda:
        abertos = [l for l in abertos if l["banda"] == banda]
    abertos.sort(key=lambda l: (l["score"] or 0), reverse=True)
    return [_lead_publico(l) for l in abertos[:limite]]

@router.get("/intel/leads/{lead_id}", response_model=LeadIntelOut)
def intel_lead(lead_id: int, user: CurrentUser) -> dict[str, Any]:
    with db.get_conn() as conn:
        dados = intel.analisar(conn, user["id"])
        leads_visiveis = _so_visiveis(conn, user, dados["leads"])

    for lead in leads_visiveis:
        if lead["id"] == lead_id:
            return _lead_publico(lead)

    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lead não encontrado")

class PrevisaoLinhaOut(BaseModel):
    etapa: str
    negocios: int
    valor: float
    probabilidade: float
    ponderado: float

class PrevisaoOut(BaseModel):
    ganho: float
    potencial: float
    ponderado: float
    linhas: list[PrevisaoLinhaOut]
    probabilidade_origem: str
    amostra: int
    amostra_minima: int
    taxa_ganho_conta: float
    aviso: str

@router.get("/intel/previsao", response_model=PrevisaoOut)
def intel_previsao(user: CurrentUser) -> dict[str, Any]:
    with db.get_conn() as conn:
        dados = intel.carregar(conn, user["id"])
        return intel.previsao(conn, user["id"], dados)

class MarcoOut(BaseModel):
    marco: str
    em: str | None

class AtivacaoOut(BaseModel):
    marcos: list[MarcoOut]
    concluidos: int
    total: int

@router.get("/intel/ativacao", response_model=AtivacaoOut)
def intel_ativacao(user: CurrentUser) -> dict[str, Any]:
    with db.get_conn() as conn:
        return intel.ativacao(conn, user["id"])

PERIODOS = {"30d": 30, "90d": 90, "180d": 180, "365d": 365}

class RecorteOut(BaseModel):
    rotulo: str
    total: int
    ganhos: int
    perdidos: int
    abertos: int
    conversao: float
    valor_ganho: float
    ticket_medio: float

class ComparacaoOut(BaseModel):
    rotulo: str
    atual: float
    anterior: float
    variacao: float | None

class RelatorioAvancadoOut(BaseModel):
    periodo: str
    dias: int
    inicio: str
    fim: str
    por_origem: list[RecorteOut]
    por_segmento: list[RecorteOut]
    por_responsavel: list[RecorteOut]
    tempo_medio_fechamento: float | None
    tempo_por_etapa: list[dict[str, Any]]
    comparacao: list[ComparacaoOut]
    tem_dados: bool

def _recorte(linhas: list[db.Row], campo: str) -> list[dict[str, Any]]:
    grupos: dict[str, dict[str, Any]] = {}
    for linha in linhas:
        chave = (linha[campo] or "").strip() or "Não informado"
        g = grupos.setdefault(
            chave,
            {"rotulo": chave, "total": 0, "ganhos": 0, "perdidos": 0, "abertos": 0,
             "valor_ganho": 0.0},
        )
        g["total"] += 1
        valor = float(linha["value"] or 0)
        if linha["status"] == "Ganho":
            g["ganhos"] += 1
            g["valor_ganho"] += valor
        elif linha["status"] == "Perdido":
            g["perdidos"] += 1
        else:
            g["abertos"] += 1

    saida = []
    for g in grupos.values():
        decididos = g["ganhos"] + g["perdidos"]
        saida.append(
            {
                **g,
                "valor_ganho": round(g["valor_ganho"], 2),

                "conversao": round(g["ganhos"] / decididos * 100, 1) if decididos else 0.0,
                "ticket_medio": round(g["valor_ganho"] / g["ganhos"], 2) if g["ganhos"] else 0.0,
            }
        )
    saida.sort(key=lambda g: (-g["valor_ganho"], -g["total"], g["rotulo"]))
    return saida

@router.get("/reports/advanced", response_model=RelatorioAvancadoOut)
def relatorio_avancado(
    user: CurrentUser,
    periodo: Literal["30d", "90d", "180d", "365d"] = "90d",
) -> dict[str, Any]:
    dias = PERIODOS[periodo]
    agora = db.utcnow()
    inicio = agora - timedelta(days=dias)
    inicio_anterior = agora - timedelta(days=dias * 2)

    user_id = user["id"]

    scope = orgs.escopo_owner(user)
    vis, vp = orgs.clausula_visibilidade(scope)
    if scope is None:
        vis_stage, vp_stage = "", []
    else:
        vis_stage = (
            " AND lead_id IN (SELECT id FROM leads WHERE user_id = ? "
            "AND (owner_user_id = ? OR owner_user_id IS NULL))"
        )
        vp_stage = [user_id, scope]
    with db.get_conn() as conn:
        atuais = conn.execute(
            f"""SELECT value, status, source, segment, owner, created_at, closed_at
                 FROM leads WHERE user_id = ? AND created_at >= ?{vis}""",
            (user_id, db.iso(inicio), *vp),
        ).fetchall()

        anteriores = conn.execute(
            f"""SELECT value, status, created_at, closed_at
                 FROM leads
                WHERE user_id = ? AND created_at >= ? AND created_at < ?{vis}""",
            (user_id, db.iso(inicio_anterior), db.iso(inicio), *vp),
        ).fetchall()

        fechados = conn.execute(
            f"""SELECT created_at, closed_at FROM leads
                WHERE user_id = ? AND status = 'Ganho'
                  AND closed_at IS NOT NULL AND closed_at >= ?{vis}""",
            (user_id, db.iso(inicio), *vp),
        ).fetchall()

        etapas = conn.execute(
            f"""SELECT de AS etapa, COUNT(*) AS n, AVG(dias_na_etapa) AS media
                 FROM stage_events
                WHERE user_id = ? AND created_at >= ?{vis_stage}
             GROUP BY de""",
            (user_id, db.iso(inicio), *vp_stage),
        ).fetchall()

    duracoes = []
    for linha in fechados:
        nasceu = db.try_parse_iso(linha["created_at"])
        fechou = db.try_parse_iso(linha["closed_at"])
        if nasceu and fechou and fechou >= nasceu:
            duracoes.append((fechou - nasceu).days)

    def _resumo(linhas: list[db.Row]) -> dict[str, float]:
        ganhos = [l for l in linhas if l["status"] == "Ganho"]
        perdidos = [l for l in linhas if l["status"] == "Perdido"]
        decididos = len(ganhos) + len(perdidos)
        valor = sum(float(l["value"] or 0) for l in ganhos)
        return {
            "leads": float(len(linhas)),
            "ganhos": float(len(ganhos)),
            "receita": round(valor, 2),
            "conversao": round(len(ganhos) / decididos * 100, 1) if decididos else 0.0,
            "ticket": round(valor / len(ganhos), 2) if ganhos else 0.0,
        }

    a, b = _resumo(atuais), _resumo(anteriores)

    def _var(chave: str, rotulo: str) -> dict[str, Any]:
        antes, agora_ = b[chave], a[chave]

        variacao = round((agora_ - antes) / antes * 100, 1) if antes else None
        return {"rotulo": rotulo, "atual": agora_, "anterior": antes, "variacao": variacao}

    return {
        "periodo": periodo,
        "dias": dias,
        "inicio": db.iso(inicio),
        "fim": db.iso(agora),
        "por_origem": _recorte(atuais, "source"),
        "por_segmento": _recorte(atuais, "segment"),
        "por_responsavel": _recorte(atuais, "owner"),
        "tempo_medio_fechamento": round(sum(duracoes) / len(duracoes), 1) if duracoes else None,
        "tempo_por_etapa": [
            {
                "etapa": linha["etapa"],
                "transicoes": int(linha["n"]),
                "dias_medios": round(float(linha["media"] or 0), 1),
            }
            for linha in etapas
            if linha["etapa"] in db.OPEN_STATUSES
        ],
        "comparacao": [
            _var("leads", "Leads criados"),
            _var("ganhos", "Negócios ganhos"),
            _var("receita", "Receita ganha"),
            _var("conversao", "Conversão (%)"),
            _var("ticket", "Ticket médio"),
        ],
        "tem_dados": bool(atuais),
    }

class IAStatusOut(BaseModel):
    disponivel: bool
    modelo: str
    provedor: str
    limite_hora: int
    limite_dia: int
    usadas_hora: int
    usadas_dia: int
    aviso_dados: str

@router.get("/ai/status", response_model=IAStatusOut)
def ia_status(user: CurrentUser) -> dict[str, Any]:
    with db.get_conn() as conn:
        return ai.status(conn, user["id"])

class IAPerguntaIn(BaseModel):
    tarefa: Literal["pergunta", "resumo_lead", "resumo_desempenho", "mensagem", "explicar_risco"] = (
        "pergunta"
    )
    pergunta: str = Field(default="", max_length=1000)
    lead_id: int | None = None

    @field_validator("pergunta")
    @classmethod
    def _v_pergunta(cls, v: str) -> str:
        return (v or "").strip()

class IARespostaOut(BaseModel):
    texto: str
    modelo: str
    tokens: int
    base: str

@router.post("/ai/ask", response_model=IARespostaOut)
async def ia_perguntar(dados: IAPerguntaIn, user: CurrentUser) -> dict[str, Any]:
    if dados.tarefa in ("pergunta",) and not dados.pergunta:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Escreva a sua pergunta."
        )
    if dados.tarefa in ("resumo_lead", "mensagem", "explicar_risco") and dados.lead_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Esta tarefa precisa de um negócio selecionado.",
        )

    escopo = orgs.escopo_owner(user)

    def trabalho() -> dict[str, Any]:
        with db.get_conn() as conn:
            return ai.executar(
                conn,
                user["id"],
                dados.tarefa,
                pergunta=dados.pergunta,
                lead_id=dados.lead_id,
                owner_scope=escopo,
            )

    try:
        return await run_in_threadpool(trabalho)
    except ai.IAIndisponivel:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="O assistente não está configurado neste servidor.",
        ) from None
    except ai.LimiteExcedido as erro:
        quando = "nesta hora" if erro.quando == "hora" else "hoje"
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Limite de perguntas ao assistente atingido {quando}. Tente mais tarde.",
        ) from None
    except ai.FalhaNaIA as erro:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=str(erro)
        ) from None
