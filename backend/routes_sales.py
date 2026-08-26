from __future__ import annotations

import logging
import secrets
from datetime import date, timedelta
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, Field, field_validator

import activities
import auth
import automations
import config
import crm
import db
import intel
import orgs
import whatsapp
from auth import CurrentUser

logger = logging.getLogger("vertex.routes.sales")

router = APIRouter(prefix="/api", tags=["vendas"])

MAX_ITENS = 40

def _texto(valor: str, rotulo: str, maximo: int) -> str:
    limpo = (valor or "").strip()
    if not limpo:
        raise ValueError(f"{rotulo} não pode ficar em branco")
    if len(limpo) > maximo:
        raise ValueError(f"{rotulo} deve ter no máximo {maximo} caracteres")
    return limpo

def _brl(valor: float) -> str:
    return f"R$ {valor:,.2f}".replace(",", "@").replace(".", ",").replace("@", ".")

class ProposalItemIn(BaseModel):
    description: str = Field(min_length=1, max_length=200)
    qty: float = Field(default=1, ge=0, le=1e6)
    unit_price: float = Field(default=0, ge=0, le=1e9)

    @field_validator("description")
    @classmethod
    def _v(cls, v: str) -> str:
        return _texto(v, "Descrição do item", 200)

class ProposalIn(BaseModel):
    lead_id: int
    title: str = Field(min_length=1, max_length=120)
    items: list[ProposalItemIn] = Field(default_factory=list)
    discount: float = Field(default=0, ge=0, le=1e9)
    terms: str = Field(default="", max_length=2000)
    delivery: str = Field(default="", max_length=200)
    notes: str = Field(default="", max_length=2000)
    validity_days: int = Field(default=15, ge=0, le=365)

    @field_validator("title")
    @classmethod
    def _v_title(cls, v: str) -> str:
        return _texto(v, "Título da proposta", 120)

    @field_validator("items")
    @classmethod
    def _v_items(cls, v: list[ProposalItemIn]) -> list[ProposalItemIn]:
        if len(v) > MAX_ITENS:
            raise ValueError(f"no máximo {MAX_ITENS} itens por proposta")
        return v

class ProposalPatch(ProposalIn):
    lead_id: int | None = None
    title: str | None = Field(default=None, min_length=1, max_length=120)
    items: list[ProposalItemIn] | None = None
    discount: float | None = Field(default=None, ge=0, le=1e9)
    terms: str | None = Field(default=None, max_length=2000)
    delivery: str | None = Field(default=None, max_length=200)
    notes: str | None = Field(default=None, max_length=2000)
    validity_days: int | None = Field(default=None, ge=0, le=365)

class ProposalItemOut(BaseModel):
    id: int
    position: int
    description: str
    qty: float
    unit_price: float
    total: float

class ProposalOut(BaseModel):
    id: int
    lead_id: int
    lead_name: str
    number: str
    title: str
    status: str
    client_name: str
    client_company: str
    client_email: str
    client_phone: str
    owner_name: str
    discount: float
    subtotal: float
    total: float
    terms: str
    delivery: str
    notes: str
    valid_until: str | None
    public_url: str
    sent_at: str | None
    viewed_at: str | None
    decided_at: str | None
    decided_by: str
    items: list[ProposalItemOut]
    created_at: str
    updated_at: str

def _public_url(token: str) -> str:
    base = config.app_base_url() or ""
    return f"{base}/proposta/{token}"

def _totais(itens: list[ProposalItemIn], desconto: float) -> tuple[float, float, list[dict]]:
    linhas: list[dict] = []
    subtotal = 0.0
    for posicao, item in enumerate(itens):
        total_item = round(float(item.qty) * float(item.unit_price), 2)
        subtotal += total_item
        linhas.append({
            "position": posicao,
            "description": item.description,
            "qty": float(item.qty),
            "unit_price": float(item.unit_price),
            "total": total_item,
        })
    subtotal = round(subtotal, 2)
    total = round(max(0.0, subtotal - float(desconto or 0)), 2)
    return subtotal, total, linhas

def _proximo_numero(conn: db.Connection, user_id: int) -> str:
    linha = conn.execute(
        "SELECT COUNT(*) AS t FROM proposals WHERE user_id = ?", (user_id,)
    ).fetchone()
    return f"PROP-{int(linha['t']) + 1:04d}"

def _monta(conn: db.Connection, linha: db.Row, lead_name: str = "") -> dict[str, Any]:
    itens = conn.execute(
        "SELECT id, position, description, qty, unit_price, total FROM proposal_items "
        "WHERE proposal_id = ? ORDER BY position, id",
        (linha["id"],),
    ).fetchall()
    if not lead_name:
        alvo = conn.execute("SELECT name FROM leads WHERE id = ?", (linha["lead_id"],)).fetchone()
        lead_name = alvo["name"] if alvo else ""
    return {
        "id": linha["id"], "lead_id": linha["lead_id"], "lead_name": lead_name,
        "number": linha["number"], "title": linha["title"], "status": linha["status"],
        "client_name": linha["client_name"], "client_company": linha["client_company"],
        "client_email": linha["client_email"], "client_phone": linha["client_phone"],
        "owner_name": linha["owner_name"], "discount": float(linha["discount"]),
        "subtotal": float(linha["subtotal"]), "total": float(linha["total"]),
        "terms": linha["terms"], "delivery": linha["delivery"], "notes": linha["notes"],
        "valid_until": linha["valid_until"], "public_url": _public_url(linha["public_token"]),
        "sent_at": linha["sent_at"], "viewed_at": linha["viewed_at"],
        "decided_at": linha["decided_at"], "decided_by": linha["decided_by"],
        "items": [dict(i) for i in itens],
        "created_at": linha["created_at"], "updated_at": linha["updated_at"],
    }

def _busca(
    conn: db.Connection, proposal_id: int, user_id: int, owner_scope: int | None = None
) -> db.Row:
    linha = conn.execute(
        "SELECT * FROM proposals WHERE id = ? AND user_id = ?", (proposal_id, user_id)
    ).fetchone()
    if linha is None:
        raise HTTPException(status_code=404, detail="Proposta não encontrada")

    if owner_scope is not None:
        crm.fetch_lead(conn, int(linha["lead_id"]), user_id, owner_scope)
    return linha

@router.get("/proposals", response_model=list[ProposalOut])
def list_proposals(user: CurrentUser, lead_id: int | None = Query(default=None)) -> list[dict]:
    with db.get_conn() as conn:
        sql = """SELECT p.*, COALESCE(l.name, '') AS lead_name
                   FROM proposals p
              LEFT JOIN leads l ON l.id = p.lead_id AND l.user_id = p.user_id
                  WHERE p.user_id = ?"""
        params: list[Any] = [user["id"]]

        vis, vp = orgs.clausula_visibilidade(orgs.escopo_owner(user), "l.owner_user_id")
        sql += vis
        params.extend(vp)
        if lead_id is not None:
            sql += " AND p.lead_id = ?"
            params.append(lead_id)
        sql += " ORDER BY p.created_at DESC, p.id DESC"
        linhas = conn.execute(sql, params).fetchall()
        return [_monta(conn, l, l["lead_name"]) for l in linhas]

@router.get("/proposals/{proposal_id}", response_model=ProposalOut)
def get_proposal(proposal_id: int, user: CurrentUser) -> dict:
    with db.get_conn() as conn:
        return _monta(conn, _busca(conn, proposal_id, user["id"], orgs.escopo_owner(user)))

PROPOSTA_BUCKET = "proposta"
PROPOSTA_LIMIT = 30
PROPOSTA_WINDOW = 60

@router.post("/proposals", response_model=ProposalOut, status_code=201)
def create_proposal(payload: ProposalIn, user: CurrentUser) -> dict:
    if auth._register_hit(PROPOSTA_BUCKET, str(user["actor_id"]), PROPOSTA_LIMIT, PROPOSTA_WINDOW):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Muitas propostas em sequência. Espere um minuto.",
        )
    with db.get_conn() as conn:

        lead = crm.fetch_lead(conn, payload.lead_id, user["id"], orgs.escopo_owner(user))
        subtotal, total, linhas = _totais(payload.items, payload.discount)
        agora = db.now_iso()
        validade = (
            db.iso(db.utcnow() + timedelta(days=payload.validity_days))
            if payload.validity_days
            else None
        )

        cur = conn.execute(
            """INSERT INTO proposals
                   (user_id, lead_id, number, title, status, client_name, client_company,
                    client_email, client_phone, owner_name, discount, subtotal, total,
                    terms, delivery, notes, valid_until, public_token, created_at, updated_at)
               VALUES (?, ?, ?, ?, 'Rascunho', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                user["id"], payload.lead_id, _proximo_numero(conn, user["id"]), payload.title,
                lead["name"], lead["company"], lead["email"], lead["phone"], user["name"],
                float(payload.discount), subtotal, total, payload.terms.strip(),
                payload.delivery.strip(), payload.notes.strip(), validade,
                secrets.token_urlsafe(32), agora, agora,
            ),
        )
        proposal_id = int(cur.lastrowid)
        for linha in linhas:
            conn.execute(
                """INSERT INTO proposal_items (proposal_id, position, description, qty, unit_price, total)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (proposal_id, linha["position"], linha["description"], linha["qty"],
                 linha["unit_price"], linha["total"]),
            )

        activities.log(
            conn, user["id"], lead_id=payload.lead_id, kind="proposta",
            title=f"Proposta criada: {payload.title}",
            detail=f"Valor: {_brl(total)}", source="user",
            ref_type="proposal", ref_id=proposal_id,
        )
        crm.dispatch_events(conn, user["id"], payload.lead_id, [("proposta.criada", {})])
        intel.marcar(conn, user["id"], "primeira_proposta")
        return _monta(conn, _busca(conn, proposal_id, user["id"], orgs.escopo_owner(user)), lead["name"])

@router.patch("/proposals/{proposal_id}", response_model=ProposalOut)
def update_proposal(proposal_id: int, payload: ProposalPatch, user: CurrentUser) -> dict:
    mudancas = payload.model_dump(exclude_unset=True, exclude_none=True)
    mudancas.pop("lead_id", None)
    if not mudancas:
        raise HTTPException(status_code=400, detail="Informe ao menos um campo")

    with db.get_conn() as conn:
        atual = _busca(conn, proposal_id, user["id"], orgs.escopo_owner(user))
        if atual["status"] in ("Aceita", "Recusada"):

            raise HTTPException(
                status_code=400,
                detail="Esta proposta já foi respondida pelo cliente e não pode mais ser alterada.",
            )

        agora = db.now_iso()
        for coluna, valor in (
            ("title", mudancas.get("title")),
            ("terms", (mudancas.get("terms") or "").strip() if "terms" in mudancas else None),
            ("delivery", (mudancas.get("delivery") or "").strip() if "delivery" in mudancas else None),
            ("notes", (mudancas.get("notes") or "").strip() if "notes" in mudancas else None),
        ):
            if valor is not None:
                conn.execute(
                    f"UPDATE proposals SET {coluna} = ? WHERE id = ? AND user_id = ?",
                    (valor, proposal_id, user["id"]),
                )

        if "validity_days" in mudancas:
            dias = int(mudancas["validity_days"])
            validade = db.iso(db.utcnow() + timedelta(days=dias)) if dias else None
            conn.execute("UPDATE proposals SET valid_until = ? WHERE id = ? AND user_id = ?",
                         (validade, proposal_id, user["id"]))

        if "items" in mudancas or "discount" in mudancas:
            itens = (
                [ProposalItemIn(**i) for i in mudancas["items"]]
                if "items" in mudancas
                else [
                    ProposalItemIn(description=i["description"], qty=i["qty"], unit_price=i["unit_price"])
                    for i in conn.execute(
                        "SELECT description, qty, unit_price FROM proposal_items "
                        "WHERE proposal_id = ? ORDER BY position", (proposal_id,)
                    )
                ]
            )
            desconto = float(mudancas.get("discount", atual["discount"]))
            subtotal, total, linhas = _totais(itens, desconto)
            conn.execute(
                "UPDATE proposals SET discount = ?, subtotal = ?, total = ? WHERE id = ? AND user_id = ?",
                (desconto, subtotal, total, proposal_id, user["id"]),
            )
            conn.execute("DELETE FROM proposal_items WHERE proposal_id = ?", (proposal_id,))
            for linha in linhas:
                conn.execute(
                    """INSERT INTO proposal_items (proposal_id, position, description, qty, unit_price, total)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (proposal_id, linha["position"], linha["description"], linha["qty"],
                     linha["unit_price"], linha["total"]),
                )

        conn.execute("UPDATE proposals SET updated_at = ? WHERE id = ? AND user_id = ?",
                     (agora, proposal_id, user["id"]))
        return _monta(conn, _busca(conn, proposal_id, user["id"], orgs.escopo_owner(user)))

class SendIn(BaseModel):
    channel: Literal["link", "whatsapp"] = "link"
    message: str = Field(default="", max_length=1000)

@router.post("/proposals/{proposal_id}/send", response_model=ProposalOut)
def send_proposal(proposal_id: int, payload: SendIn, user: CurrentUser) -> dict:
    with db.get_conn() as conn:
        atual = _busca(conn, proposal_id, user["id"], orgs.escopo_owner(user))
        if atual["status"] in ("Aceita", "Recusada"):
            raise HTTPException(status_code=400, detail="Esta proposta já foi respondida.")

        agora = db.now_iso()
        link = _public_url(atual["public_token"])
        erro_envio = ""

        if payload.channel == "whatsapp":
            lead = crm.full_row(conn, int(atual["lead_id"]), user["id"])
            numero = str((lead or {}).get("whatsapp") or (lead or {}).get("phone") or "")
            texto = payload.message.strip() or (
                f"Olá! Segue a proposta “{atual['title']}” "
                f"no valor de {_brl(float(atual['total']))}: {link}"
            )
            resultado = whatsapp.send_message(
                conn, user["id"], lead_id=int(atual["lead_id"]),
                phone=numero, body=texto, source="user",
            )
            if not resultado["ok"]:

                raise HTTPException(
                    status_code=400,
                    detail=f"A proposta não foi enviada pelo WhatsApp: {resultado['error']}",
                )

        conn.execute(
            "UPDATE proposals SET status = 'Enviada', sent_at = ?, updated_at = ? "
            "WHERE id = ? AND user_id = ?",
            (agora, agora, proposal_id, user["id"]),
        )
        activities.log(
            conn, user["id"], lead_id=int(atual["lead_id"]), kind="proposta",
            title=f"Proposta enviada: {atual['title']}",
            detail=f"Canal: {payload.channel}. Link: {link}" + (f" ({erro_envio})" if erro_envio else ""),
            source="user", ref_type="proposal", ref_id=proposal_id,
        )
        crm.dispatch_events(conn, user["id"], int(atual["lead_id"]), [("proposta.enviada", {})])
        return _monta(conn, _busca(conn, proposal_id, user["id"], orgs.escopo_owner(user)))

@router.delete("/proposals/{proposal_id}", status_code=204)
def delete_proposal(proposal_id: int, user: CurrentUser) -> Response:
    with db.get_conn() as conn:
        atual = _busca(conn, proposal_id, user["id"], orgs.escopo_owner(user))
        if atual["status"] == "Aceita":
            raise HTTPException(
                status_code=400,
                detail="Uma proposta aceita faz parte do histórico do negócio e não pode ser apagada.",
            )
        conn.execute("DELETE FROM proposals WHERE id = ? AND user_id = ?", (proposal_id, user["id"]))
        activities.log(
            conn, user["id"], lead_id=int(atual["lead_id"]), kind="proposta",
            title=f"Proposta removida: {atual['title']}", source="system",
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)

public_router = APIRouter(prefix="/api/public", tags=["publico"])

class PublicProposalOut(BaseModel):
    number: str
    title: str
    status: str
    client_name: str
    client_company: str
    owner_name: str
    company_name: str
    subtotal: float
    discount: float
    total: float
    terms: str
    delivery: str
    notes: str
    valid_until: str | None
    expired: bool
    decided_at: str | None
    decided_by: str
    items: list[ProposalItemOut]
    created_at: str

def _proposta_por_token(conn: db.Connection, token: str) -> db.Row:
    if not token or len(token) < 20:
        raise HTTPException(status_code=404, detail="Proposta não encontrada")
    linha = conn.execute("SELECT * FROM proposals WHERE public_token = ?", (token,)).fetchone()
    if linha is None:
        raise HTTPException(status_code=404, detail="Proposta não encontrada")
    return linha

def _publica(conn: db.Connection, linha: db.Row) -> dict:
    itens = conn.execute(
        "SELECT id, position, description, qty, unit_price, total FROM proposal_items "
        "WHERE proposal_id = ? ORDER BY position, id",
        (linha["id"],),
    ).fetchall()
    vence = db.try_parse_iso(linha["valid_until"])
    dono = conn.execute("SELECT name FROM users WHERE id = ?", (linha["user_id"],)).fetchone()
    return {
        "number": linha["number"], "title": linha["title"], "status": linha["status"],
        "client_name": linha["client_name"], "client_company": linha["client_company"],
        "owner_name": linha["owner_name"] or (dono["name"] if dono else ""),
        "company_name": dono["name"] if dono else "",
        "subtotal": float(linha["subtotal"]), "discount": float(linha["discount"]),
        "total": float(linha["total"]), "terms": linha["terms"],
        "delivery": linha["delivery"], "notes": linha["notes"],
        "valid_until": linha["valid_until"],
        "expired": bool(vence and vence < db.utcnow()),
        "decided_at": linha["decided_at"], "decided_by": linha["decided_by"],
        "items": [dict(i) for i in itens], "created_at": linha["created_at"],
    }

@public_router.get("/proposal/{token}", response_model=PublicProposalOut)
def public_proposal(token: str) -> dict:
    with db.get_conn() as conn:
        linha = _proposta_por_token(conn, token)
        user_id = int(linha["user_id"])

        if linha["status"] == "Enviada" and not linha["viewed_at"]:
            agora = db.now_iso()
            conn.execute(
                "UPDATE proposals SET status = 'Visualizada', viewed_at = ?, updated_at = ? WHERE id = ?",
                (agora, agora, linha["id"]),
            )
            activities.log(
                conn, user_id, lead_id=int(linha["lead_id"]), kind="proposta",
                title=f"Cliente abriu a proposta “{linha['title']}”",
                source="system", ref_type="proposal", ref_id=int(linha["id"]),
            )
            activities.notify(
                conn, user_id, type="proposta_vista",
                title=f"Proposta visualizada: {linha['title']}",
                body=f"{linha['client_name']} abriu a proposta.", severity="info",
                ref_type="proposal", ref_id=int(linha["id"]),
                dedup_key=f"propvista:{linha['id']}",
            )
            crm.dispatch_events(conn, user_id, int(linha["lead_id"]), [("proposta.visualizada", {})])
            linha = conn.execute("SELECT * FROM proposals WHERE id = ?", (linha["id"],)).fetchone()

        return _publica(conn, linha)

class DecisionIn(BaseModel):
    decision: Literal["aceita", "recusada"]
    name: str = Field(min_length=2, max_length=80)
    note: str = Field(default="", max_length=500)

    @field_validator("name")
    @classmethod
    def _v(cls, v: str) -> str:
        return _texto(v, "Nome", 80)

@public_router.post("/proposal/{token}/decision", response_model=PublicProposalOut)
def public_decision(token: str, payload: DecisionIn, request: Request) -> dict:
    auth.enforce_public_action_rate_limit(request)

    with db.get_conn() as conn:
        linha = _proposta_por_token(conn, token)
        user_id = int(linha["user_id"])

        if linha["status"] in ("Aceita", "Recusada"):
            raise HTTPException(status_code=400, detail="Esta proposta já foi respondida.")
        if linha["status"] == "Rascunho":
            raise HTTPException(status_code=404, detail="Proposta não encontrada")

        vence = db.try_parse_iso(linha["valid_until"])
        if vence and vence < db.utcnow():
            raise HTTPException(status_code=400, detail="Esta proposta está fora do prazo de validade.")

        agora = db.now_iso()
        novo = "Aceita" if payload.decision == "aceita" else "Recusada"
        ip = auth.client_ip(request)
        conn.execute(
            "UPDATE proposals SET status = ?, decided_at = ?, decided_by = ?, updated_at = ? WHERE id = ?",
            (novo, agora, payload.name[:80], agora, linha["id"]),
        )
        activities.log(
            conn, user_id, lead_id=int(linha["lead_id"]), kind="proposta",
            title=f"Proposta {novo.lower()} por {payload.name}",
            detail=f"{payload.note}\nRegistrado em {agora} a partir do IP {ip}.".strip(),
            source="system", ref_type="proposal", ref_id=int(linha["id"]),
        )
        activities.notify(
            conn, user_id, type="proposta_decidida",
            title=f"Proposta {novo.lower()}: {linha['title']}",
            body=f"{payload.name} — {_brl(float(linha['total']))}",
            severity="sucesso" if novo == "Aceita" else "alerta",
            ref_type="proposal", ref_id=int(linha["id"]),
            dedup_key=f"propdec:{linha['id']}",
        )

        evento = "proposta.aceita" if novo == "Aceita" else "proposta.recusada"
        eventos: list[tuple[str, dict]] = [(evento, {})]

        if novo == "Aceita":
            try:
                eventos.extend(
                    crm.change_status(
                        conn, user_id, int(linha["lead_id"]), "Ganho",
                        origem="system", detalhe=f"Proposta {linha['number']} aceita pelo cliente.",
                    )
                )
            except HTTPException:
                logger.warning("Não foi possível mover o lead para Ganho após aceite.")

        crm.dispatch_events(conn, user_id, int(linha["lead_id"]), eventos)
        atualizada = conn.execute("SELECT * FROM proposals WHERE id = ?", (linha["id"],)).fetchone()
        return _publica(conn, atualizada)

class AutomationCondition(BaseModel):
    campo: str = Field(max_length=40)
    operador: str = Field(default="igual", max_length=20)
    valor: str = Field(default="", max_length=120)

class AutomationAction(BaseModel):
    tipo: str = Field(max_length=40)
    titulo: str = Field(default="", max_length=160)
    texto: str = Field(default="", max_length=500)
    valor: str = Field(default="", max_length=120)
    status: str = Field(default="", max_length=40)
    campo: str = Field(default="", max_length=40)
    template: str = Field(default="", max_length=80)
    dias: int = Field(default=1, ge=0, le=365)

class AutomationIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    event: str = Field(max_length=40)
    conditions: list[AutomationCondition] = Field(default_factory=list)
    actions: list[AutomationAction] = Field(default_factory=list)
    active: bool = True

    @field_validator("name")
    @classmethod
    def _v(cls, v: str) -> str:
        return _texto(v, "Nome da automação", 80)

class AutomationPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    event: str | None = Field(default=None, max_length=40)
    conditions: list[AutomationCondition] | None = None
    actions: list[AutomationAction] | None = None
    active: bool | None = None

class AutomationOut(BaseModel):
    id: int
    name: str
    event: str
    event_label: str
    conditions: list[dict]
    actions: list[dict]
    active: bool
    run_count: int
    last_run_at: str | None
    created_at: str
    updated_at: str

class AutomationRunOut(BaseModel):
    id: int
    automation_id: int | None
    automation_name: str
    lead_id: int | None
    lead_name: str
    event: str
    event_label: str
    summary: str
    status: str
    error: str
    created_at: str

def _automacao(linha: db.Row) -> dict:
    return {
        "id": linha["id"], "name": linha["name"], "event": linha["event"],
        "event_label": automations.EVENTS.get(linha["event"], linha["event"]),
        "conditions": db.json_load(linha["conditions"], []),
        "actions": db.json_load(linha["actions"], []),
        "active": bool(linha["active"]), "run_count": int(linha["run_count"]),
        "last_run_at": linha["last_run_at"], "created_at": linha["created_at"],
        "updated_at": linha["updated_at"],
    }

@router.get("/automations/meta")
def automation_meta(user: CurrentUser) -> dict:
    with db.get_conn() as conn:
        modelos = [
            l["name"] for l in conn.execute(
                "SELECT name FROM wa_templates WHERE user_id = ? ORDER BY name", (user["id"],)
            )
        ]
    return {
        "events": [
            {"value": v, "label": r, "tipo": "tempo" if v in automations.TIME_EVENTS else "acao"}
            for v, r in automations.EVENTS.items()
        ],
        "fields": [
            {"value": c, "label": c.replace("_", " ").capitalize(), "tipo": t}
            for c, t in automations.CONDITION_FIELDS.items()
        ],
        "operators": list(automations.OPERATORS),
        "actions": [{"value": v, "label": r} for v, r in automations.ACTION_TYPES.items()],
        "statuses": list(db.STATUSES),
        "segments": list(db.SEGMENTS),
        "updatable_fields": list(automations.UPDATABLE_FIELDS),
        "templates": modelos,
        "max_actions": automations.MAX_ACTIONS,
        "max_conditions": automations.MAX_CONDITIONS,
    }

@router.get("/automations", response_model=list[AutomationOut])
def list_automations(user: CurrentUser) -> list[dict]:
    with db.get_conn() as conn:
        linhas = conn.execute(
            "SELECT * FROM automations WHERE user_id = ? ORDER BY active DESC, id DESC",
            (user["id"],),
        ).fetchall()
        return [_automacao(l) for l in linhas]

@router.post("/automations", response_model=AutomationOut, status_code=201)
def create_automation(payload: AutomationIn, user: CurrentUser) -> dict:
    if payload.event not in automations.EVENTS:
        raise HTTPException(status_code=400, detail=f"Evento desconhecido: {payload.event}")
    try:
        condicoes = automations.validate_conditions([c.model_dump() for c in payload.conditions])
        acoes = automations.validate_actions([a.model_dump() for a in payload.actions])
    except ValueError as erro:
        raise HTTPException(status_code=400, detail=str(erro)) from None

    agora = db.now_iso()
    with db.get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO automations
                   (user_id, name, event, conditions, actions, active, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (user["id"], payload.name, payload.event, db.json_dump(condicoes),
             db.json_dump(acoes), 1 if payload.active else 0, agora, agora),
        )
        intel.marcar(conn, user["id"], "primeira_automacao")
        linha = conn.execute("SELECT * FROM automations WHERE id = ?", (cur.lastrowid,)).fetchone()
        return _automacao(linha)

@router.patch("/automations/{automation_id}", response_model=AutomationOut)
def update_automation(automation_id: int, payload: AutomationPatch, user: CurrentUser) -> dict:
    mudancas = payload.model_dump(exclude_unset=True, exclude_none=True)
    if not mudancas:
        raise HTTPException(status_code=400, detail="Informe ao menos um campo")

    with db.get_conn() as conn:
        atual = conn.execute(
            "SELECT id FROM automations WHERE id = ? AND user_id = ?", (automation_id, user["id"])
        ).fetchone()
        if atual is None:
            raise HTTPException(status_code=404, detail="Automação não encontrada")

        try:
            if "event" in mudancas and mudancas["event"] not in automations.EVENTS:
                raise ValueError(f"Evento desconhecido: {mudancas['event']}")
            if "conditions" in mudancas:
                mudancas["conditions"] = db.json_dump(
                    automations.validate_conditions(mudancas["conditions"])
                )
            if "actions" in mudancas:
                mudancas["actions"] = db.json_dump(automations.validate_actions(mudancas["actions"]))
        except ValueError as erro:
            raise HTTPException(status_code=400, detail=str(erro)) from None

        if "active" in mudancas:
            mudancas["active"] = 1 if mudancas["active"] else 0
        if "name" in mudancas:
            mudancas["name"] = _texto(mudancas["name"], "Nome da automação", 80)

        permitidas = ("name", "event", "conditions", "actions", "active")
        atribuicoes = [f"{c} = ?" for c in permitidas if c in mudancas]
        params: list[Any] = [mudancas[c] for c in permitidas if c in mudancas]
        atribuicoes.append("updated_at = ?")
        params.extend([db.now_iso(), automation_id, user["id"]])
        conn.execute(
            f"UPDATE automations SET {', '.join(atribuicoes)} WHERE id = ? AND user_id = ?", params
        )
        linha = conn.execute("SELECT * FROM automations WHERE id = ?", (automation_id,)).fetchone()
        return _automacao(linha)

@router.delete("/automations/{automation_id}", status_code=204)
def delete_automation(automation_id: int, user: CurrentUser) -> Response:
    with db.get_conn() as conn:
        atual = conn.execute(
            "SELECT id FROM automations WHERE id = ? AND user_id = ?", (automation_id, user["id"])
        ).fetchone()
        if atual is None:
            raise HTTPException(status_code=404, detail="Automação não encontrada")

        conn.execute("DELETE FROM automations WHERE id = ? AND user_id = ?", (automation_id, user["id"]))
    return Response(status_code=status.HTTP_204_NO_CONTENT)

@router.get("/automation-runs", response_model=list[AutomationRunOut])
def list_runs(user: CurrentUser, limit: int = Query(default=60, ge=1, le=200)) -> list[dict]:

    scope = orgs.escopo_owner(user)
    if scope is None:
        vis, vp = "", []
    else:
        vis = (" AND (lead_id IS NULL OR lead_id IN (SELECT id FROM leads "
               "WHERE user_id = ? AND (owner_user_id = ? OR owner_user_id IS NULL)))")
        vp = [user["id"], scope]
    with db.get_conn() as conn:
        linhas = conn.execute(
            f"SELECT * FROM automation_runs WHERE user_id = ?{vis} "
            "ORDER BY created_at DESC, id DESC LIMIT ?",
            (user["id"], *vp, limit),
        ).fetchall()
    return [
        {
            "id": l["id"], "automation_id": l["automation_id"],
            "automation_name": l["automation_name"], "lead_id": l["lead_id"],
            "lead_name": l["lead_name"], "event": l["event"],
            "event_label": automations.EVENTS.get(l["event"], l["event"]),
            "summary": l["summary"], "status": l["status"], "error": l["error"],
            "created_at": l["created_at"],
        }
        for l in linhas
    ]

class WaConfigIn(BaseModel):
    phone_number_id: str = Field(default="", max_length=40)
    waba_id: str = Field(default="", max_length=40)
    display_phone: str = Field(default="", max_length=30)

    @field_validator("phone_number_id", "waba_id")
    @classmethod
    def _v_id(cls, v: str) -> str:
        limpo = (v or "").strip()
        if limpo and not limpo.isdigit():
            raise ValueError("O identificador da Meta é composto apenas por números")
        return limpo

class WaTemplateIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    language: str = Field(default="pt_BR", max_length=10)
    category: Literal["UTILITY", "MARKETING", "AUTHENTICATION"] = "UTILITY"
    body: str = Field(min_length=1, max_length=1024)

    @field_validator("name")
    @classmethod
    def _v_name(cls, v: str) -> str:
        limpo = (v or "").strip().lower()
        if not limpo.replace("_", "").isalnum():
            raise ValueError("O nome do template só aceita letras, números e underline")
        return limpo[:80]

class WaSendIn(BaseModel):
    body: str = Field(default="", max_length=4000)
    template_name: str = Field(default="", max_length=80)

@router.get("/whatsapp/config")
def wa_config(user: CurrentUser) -> dict:
    with db.get_conn() as conn:
        conf = whatsapp.get_config(conn, user["id"])
    conf["webhook_url"] = f"{config.app_base_url()}/api/whatsapp/webhook"
    conf["webhook_pronto"] = bool(whatsapp.app_secret() and whatsapp.verify_token())
    return conf

@router.put("/whatsapp/config")
def save_wa_config(payload: WaConfigIn, user: CurrentUser) -> dict:
    with db.get_conn() as conn:
        whatsapp.save_config(
            conn, user["id"],
            phone_number_id=payload.phone_number_id,
            waba_id=payload.waba_id,
            display_phone=payload.display_phone.strip(),
        )
        conf = whatsapp.get_config(conn, user["id"])
    conf["webhook_url"] = f"{config.app_base_url()}/api/whatsapp/webhook"
    conf["webhook_pronto"] = bool(whatsapp.app_secret() and whatsapp.verify_token())
    return conf

@router.post("/whatsapp/check")
def check_wa(user: CurrentUser) -> dict:
    with db.get_conn() as conn:
        return whatsapp.check_connection(conn, user["id"])

@router.post("/whatsapp/disconnect")
def disconnect_wa(user: CurrentUser) -> dict:
    with db.get_conn() as conn:
        whatsapp.disconnect(conn, user["id"])
        return whatsapp.get_config(conn, user["id"])

@router.get("/whatsapp/templates")
def list_templates(user: CurrentUser) -> list[dict]:
    with db.get_conn() as conn:
        linhas = conn.execute(
            "SELECT * FROM wa_templates WHERE user_id = ? ORDER BY name", (user["id"],)
        ).fetchall()
    return [
        {"id": l["id"], "name": l["name"], "language": l["language"],
         "category": l["category"], "body": l["body"], "created_at": l["created_at"]}
        for l in linhas
    ]

@router.post("/whatsapp/templates", status_code=201)
def create_template(payload: WaTemplateIn, user: CurrentUser) -> dict:
    agora = db.now_iso()
    with db.get_conn() as conn:
        existe = conn.execute(
            "SELECT 1 FROM wa_templates WHERE user_id = ? AND name = ? AND language = ?",
            (user["id"], payload.name, payload.language),
        ).fetchone()
        if existe:
            raise HTTPException(status_code=400, detail="Já existe um template com esse nome")
        cur = conn.execute(
            """INSERT INTO wa_templates (user_id, name, language, category, body, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (user["id"], payload.name, payload.language, payload.category,
             payload.body.strip(), agora, agora),
        )
        return {"id": int(cur.lastrowid), "name": payload.name, "language": payload.language,
                "category": payload.category, "body": payload.body.strip(), "created_at": agora}

@router.delete("/whatsapp/templates/{template_id}", status_code=204)
def delete_template(template_id: int, user: CurrentUser) -> Response:
    with db.get_conn() as conn:
        existe = conn.execute(
            "SELECT 1 FROM wa_templates WHERE id = ? AND user_id = ?", (template_id, user["id"])
        ).fetchone()
        if existe is None:
            raise HTTPException(status_code=404, detail="Template não encontrado")
        conn.execute("DELETE FROM wa_templates WHERE id = ? AND user_id = ?", (template_id, user["id"]))
    return Response(status_code=status.HTTP_204_NO_CONTENT)

@router.get("/leads/{lead_id}/whatsapp")
def lead_conversation(lead_id: int, user: CurrentUser) -> dict:
    with db.get_conn() as conn:

        lead = crm.fetch_lead(conn, lead_id, user["id"], orgs.escopo_owner(user))
        conf = whatsapp.get_config(conn, user["id"])
        return {
            "ready": conf["ready"],
            "status": conf["status"],
            "phone": whatsapp.normalize_phone(lead["whatsapp"] or lead["phone"]),
            "messages": whatsapp.conversation(conn, user["id"], lead_id),
        }

@router.post("/leads/{lead_id}/whatsapp")
def send_whatsapp(lead_id: int, payload: WaSendIn, user: CurrentUser) -> dict:
    with db.get_conn() as conn:

        lead = crm.fetch_lead(conn, lead_id, user["id"], orgs.escopo_owner(user))
        numero = lead["whatsapp"] or lead["phone"]
        if not numero:
            raise HTTPException(
                status_code=400,
                detail="Este lead não tem número de WhatsApp. Preencha o campo no cadastro.",
            )
        resultado = whatsapp.send_message(
            conn, user["id"], lead_id=lead_id, phone=numero,
            body=payload.body, template_name=payload.template_name, source="user",
        )
        if not resultado["ok"]:
            raise HTTPException(status_code=400, detail=resultado["error"])
        return {
            "ok": True,
            "messages": whatsapp.conversation(conn, user["id"], lead_id),
        }

@router.get("/whatsapp/webhook", include_in_schema=False)
def wa_webhook_verify(request: Request) -> Response:
    params = request.query_params
    esperado = whatsapp.verify_token()
    if (
        params.get("hub.mode") == "subscribe"
        and esperado
        and secrets.compare_digest(params.get("hub.verify_token", ""), esperado)
    ):
        return Response(content=params.get("hub.challenge", ""), media_type="text/plain")
    return Response(status_code=status.HTTP_403_FORBIDDEN)

@router.post("/whatsapp/webhook", include_in_schema=False)
async def wa_webhook(request: Request) -> Response:
    bruto = await request.body()
    if not whatsapp.signature_ok(bruto, request.headers.get("X-Hub-Signature-256")):
        logger.warning("Webhook do WhatsApp recusado: assinatura inválida.")
        return Response(status_code=status.HTTP_403_FORBIDDEN)

    import json

    try:
        payload = json.loads(bruto.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return Response(status_code=status.HTTP_400_BAD_REQUEST)

    try:
        gravadas = whatsapp.handle_webhook(payload)
        logger.info("Webhook do WhatsApp processado: %d mensagem(ns).", gravadas)
    except Exception:  # noqa: BLE001

        logger.exception("Falha ao processar o webhook do WhatsApp.")
    return Response(status_code=status.HTTP_200_OK)
