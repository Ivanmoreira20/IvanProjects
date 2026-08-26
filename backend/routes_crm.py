from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field, field_validator

import activities
import auth
import crm
import customfields
import db
import importacao
import intel
import orgs
from auth import CurrentUser

logger = logging.getLogger("vertex.routes.crm")

router = APIRouter(prefix="/api", tags=["crm"])

def _texto(valor: str, rotulo: str, maximo: int) -> str:
    limpo = (valor or "").strip()
    if not limpo:
        raise ValueError(f"{rotulo} não pode ficar em branco")
    if len(limpo) > maximo:
        raise ValueError(f"{rotulo} deve ter no máximo {maximo} caracteres")
    return limpo

class ActivityIn(BaseModel):
    kind: Literal["nota", "ligacao", "reuniao", "email", "tarefa"] = "nota"
    title: str = Field(min_length=1, max_length=160)
    detail: str = Field(default="", max_length=4000)

    due_date: str = Field(default="", max_length=10)

    @field_validator("title")
    @classmethod
    def _v_title(cls, v: str) -> str:
        return _texto(v, "Título", 160)

    @field_validator("due_date")
    @classmethod
    def _v_due(cls, v: str) -> str:
        limpo = (v or "").strip()
        if not limpo:
            return ""
        try:
            date.fromisoformat(limpo)
        except ValueError:
            raise ValueError("Data no formato AAAA-MM-DD") from None
        return limpo

class ActivityOut(BaseModel):
    id: int
    lead_id: int | None
    kind: str
    title: str
    detail: str
    source: str
    ref_type: str
    ref_id: int | None
    due_at: str | None
    done_at: str | None
    created_at: str
    pendente: bool

class TaskOut(ActivityOut):
    lead_name: str

@router.get("/leads/{lead_id}/activities", response_model=list[ActivityOut])
def lead_activities(lead_id: int, user: CurrentUser) -> list[dict]:
    with db.get_conn() as conn:

        crm.fetch_lead(conn, lead_id, user["id"], orgs.escopo_owner(user))
        return activities.list_for_lead(conn, user["id"], lead_id)

ATIVIDADE_BUCKET = "atividade"
ATIVIDADE_LIMIT = 120
ATIVIDADE_WINDOW = 60

@router.post("/leads/{lead_id}/activities", response_model=ActivityOut, status_code=201)
def create_activity(lead_id: int, payload: ActivityIn, user: CurrentUser) -> dict:
    if auth._register_hit(ATIVIDADE_BUCKET, str(user["actor_id"]), ATIVIDADE_LIMIT, ATIVIDADE_WINDOW):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Muitos registros em sequência. Espere um minuto.",
        )
    with db.get_conn() as conn:

        crm.fetch_lead(conn, lead_id, user["id"], orgs.escopo_owner(user))

        vence = None
        if payload.due_date:

            dia = date.fromisoformat(payload.due_date)
            vence = db.iso(
                db.utcnow().replace(
                    year=dia.year, month=dia.month, day=dia.day,
                    hour=23, minute=59, second=59, microsecond=0,
                )
            )

        novo_id = activities.log(
            conn, user["id"], lead_id=lead_id, kind=payload.kind,
            title=payload.title, detail=payload.detail, source="user", due_at=vence,
        )
        intel.marcar(conn, user["id"], "primeira_atividade")
        if vence:
            intel.marcar(conn, user["id"], "primeiro_followup")

        linha = conn.execute("SELECT * FROM activities WHERE id = ?", (novo_id,)).fetchone()
        return activities.row_to_dict(linha)

@router.post("/activities/{activity_id}/done", response_model=ActivityOut)
def finish_activity(activity_id: int, user: CurrentUser) -> dict:
    with db.get_conn() as conn:
        linha = conn.execute(
            "SELECT * FROM activities WHERE id = ? AND user_id = ?", (activity_id, user["id"])
        ).fetchone()
        if linha is None:
            raise HTTPException(status_code=404, detail="Atividade não encontrada")

        scope = orgs.escopo_owner(user)
        if scope is not None and linha["lead_id"]:
            crm.fetch_lead(conn, int(linha["lead_id"]), user["id"], scope)
        if linha["done_at"]:
            return activities.row_to_dict(linha)

        agora = db.now_iso()
        conn.execute("UPDATE activities SET done_at = ? WHERE id = ? AND user_id = ?",
                     (agora, activity_id, user["id"]))

        if linha["lead_id"]:
            activities.touch_last_activity(conn, user["id"], int(linha["lead_id"]), agora)
            eventos = [("atividade.concluida", {"id": activity_id})]
            crm.dispatch_events(conn, user["id"], int(linha["lead_id"]), eventos)

        atualizada = conn.execute("SELECT * FROM activities WHERE id = ?", (activity_id,)).fetchone()
        return activities.row_to_dict(atualizada)

@router.delete("/activities/{activity_id}", status_code=204)
def delete_activity(activity_id: int, user: CurrentUser):
    from fastapi import Response

    with db.get_conn() as conn:
        linha = conn.execute(
            "SELECT kind, lead_id FROM activities WHERE id = ? AND user_id = ?",
            (activity_id, user["id"]),
        ).fetchone()
        if linha is None:
            raise HTTPException(status_code=404, detail="Atividade não encontrada")

        scope = orgs.escopo_owner(user)
        if scope is not None and linha["lead_id"]:
            crm.fetch_lead(conn, int(linha["lead_id"]), user["id"], scope)
        if linha["kind"] not in db.USER_EDITABLE_KINDS:

            raise HTTPException(
                status_code=400,
                detail="Este registro faz parte do histórico do negócio e não pode ser apagado.",
            )
        conn.execute("DELETE FROM activities WHERE id = ? AND user_id = ?", (activity_id, user["id"]))
    return Response(status_code=status.HTTP_204_NO_CONTENT)

@router.get("/tasks", response_model=list[TaskOut])
def list_tasks(user: CurrentUser) -> list[dict]:
    with db.get_conn() as conn:

        return activities.pending_tasks(conn, user["id"], owner_scope=orgs.escopo_owner(user))

class NotificationOut(BaseModel):
    id: int
    type: str
    title: str
    body: str
    severity: str
    ref_type: str
    ref_id: int | None
    read_at: str | None
    created_at: str

class NotificationsOut(BaseModel):
    unread: int
    items: list[NotificationOut]

@router.get("/notifications", response_model=NotificationsOut)
def list_notifications(
    user: CurrentUser,
    only_unread: bool = Query(default=False),
    limit: int = Query(default=50, ge=1, le=200),
) -> dict:
    with db.get_conn() as conn:
        sql = "SELECT * FROM notifications WHERE user_id = ?"
        if only_unread:
            sql += " AND read_at IS NULL"
        sql += " ORDER BY created_at DESC, id DESC LIMIT ?"
        linhas = conn.execute(sql, (user["id"], limit)).fetchall()
        return {
            "unread": activities.unread_count(conn, user["id"]),
            "items": [activities.notification_to_dict(linha) for linha in linhas],
        }

@router.post("/notifications/{notification_id}/read", response_model=NotificationsOut)
def read_notification(notification_id: int, user: CurrentUser) -> dict:
    with db.get_conn() as conn:
        cur = conn.execute(
            "UPDATE notifications SET read_at = ? WHERE id = ? AND user_id = ? AND read_at IS NULL",
            (db.now_iso(), notification_id, user["id"]),
        )
        if not cur.rowcount:
            existe = conn.execute(
                "SELECT 1 FROM notifications WHERE id = ? AND user_id = ?",
                (notification_id, user["id"]),
            ).fetchone()
            if existe is None:
                raise HTTPException(status_code=404, detail="Notificação não encontrada")
        linhas = conn.execute(
            "SELECT * FROM notifications WHERE user_id = ? ORDER BY created_at DESC LIMIT 50",
            (user["id"],),
        ).fetchall()
        return {
            "unread": activities.unread_count(conn, user["id"]),
            "items": [activities.notification_to_dict(linha) for linha in linhas],
        }

@router.post("/notifications/read-all", response_model=NotificationsOut)
def read_all_notifications(user: CurrentUser) -> dict:
    with db.get_conn() as conn:
        conn.execute(
            "UPDATE notifications SET read_at = ? WHERE user_id = ? AND read_at IS NULL",
            (db.now_iso(), user["id"]),
        )
        linhas = conn.execute(
            "SELECT * FROM notifications WHERE user_id = ? ORDER BY created_at DESC LIMIT 50",
            (user["id"],),
        ).fetchall()
        return {"unread": 0, "items": [activities.notification_to_dict(l) for l in linhas]}

class LossReasonIn(BaseModel):
    label: str = Field(min_length=1, max_length=60)

    @field_validator("label")
    @classmethod
    def _v(cls, v: str) -> str:
        return _texto(v, "Motivo", 60)

class LossReasonPatch(BaseModel):
    label: str | None = Field(default=None, min_length=1, max_length=60)
    active: bool | None = None
    position: int | None = Field(default=None, ge=0, le=999)

class LossReasonOut(BaseModel):
    id: int
    label: str
    position: int
    active: bool
    used: int
    """Quantos negocios perdidos usam este motivo -- a tela precisa avisar
    antes de desativar algo que ja explica 40 perdas."""

def _loss_rows(conn: db.Connection, user_id: int) -> list[dict]:
    activities.ensure_loss_reasons(conn, user_id)
    linhas = conn.execute(
        """SELECT r.id, r.label, r.position, r.active,
                  (SELECT COUNT(*) FROM leads l
                    WHERE l.user_id = r.user_id AND l.status = 'Perdido'
                      AND l.lost_reason = r.label) AS used
             FROM loss_reasons r
            WHERE r.user_id = ?
         ORDER BY r.position, r.id""",
        (user_id,),
    ).fetchall()
    return [
        {"id": l["id"], "label": l["label"], "position": l["position"],
         "active": bool(l["active"]), "used": int(l["used"])}
        for l in linhas
    ]

@router.get("/loss-reasons", response_model=list[LossReasonOut])
def list_loss_reasons(user: CurrentUser) -> list[dict]:
    with db.get_conn() as conn:
        return _loss_rows(conn, user["id"])

@router.post("/loss-reasons", response_model=list[LossReasonOut], status_code=201)
def create_loss_reason(payload: LossReasonIn, user: CurrentUser) -> list[dict]:
    with db.get_conn() as conn:
        activities.ensure_loss_reasons(conn, user["id"])
        existe = conn.execute(
            "SELECT 1 FROM loss_reasons WHERE user_id = ? AND label = ?",
            (user["id"], payload.label),
        ).fetchone()
        if existe:
            raise HTTPException(status_code=400, detail="Já existe um motivo com esse nome")
        proxima = conn.execute(
            "SELECT COALESCE(MAX(position), -1) + 1 AS p FROM loss_reasons WHERE user_id = ?",
            (user["id"],),
        ).fetchone()["p"]
        conn.execute(
            "INSERT INTO loss_reasons (user_id, label, position, active, created_at) VALUES (?, ?, ?, 1, ?)",
            (user["id"], payload.label, proxima, db.now_iso()),
        )
        return _loss_rows(conn, user["id"])

@router.patch("/loss-reasons/{reason_id}", response_model=list[LossReasonOut])
def update_loss_reason(reason_id: int, payload: LossReasonPatch, user: CurrentUser) -> list[dict]:
    mudancas = payload.model_dump(exclude_unset=True, exclude_none=True)
    if not mudancas:
        raise HTTPException(status_code=400, detail="Informe ao menos um campo")
    with db.get_conn() as conn:
        atual = conn.execute(
            "SELECT label FROM loss_reasons WHERE id = ? AND user_id = ?", (reason_id, user["id"])
        ).fetchone()
        if atual is None:
            raise HTTPException(status_code=404, detail="Motivo não encontrado")

        if "label" in mudancas:
            novo = _texto(mudancas["label"], "Motivo", 60)

            conn.execute(
                "UPDATE loss_reasons SET label = ? WHERE id = ? AND user_id = ?",
                (novo, reason_id, user["id"]),
            )
        if "active" in mudancas:
            conn.execute(
                "UPDATE loss_reasons SET active = ? WHERE id = ? AND user_id = ?",
                (1 if mudancas["active"] else 0, reason_id, user["id"]),
            )
        if "position" in mudancas:
            conn.execute(
                "UPDATE loss_reasons SET position = ? WHERE id = ? AND user_id = ?",
                (int(mudancas["position"]), reason_id, user["id"]),
            )
        return _loss_rows(conn, user["id"])

@router.delete("/loss-reasons/{reason_id}", response_model=list[LossReasonOut])
def delete_loss_reason(reason_id: int, user: CurrentUser) -> list[dict]:
    with db.get_conn() as conn:
        linha = conn.execute(
            "SELECT label FROM loss_reasons WHERE id = ? AND user_id = ?", (reason_id, user["id"])
        ).fetchone()
        if linha is None:
            raise HTTPException(status_code=404, detail="Motivo não encontrado")
        usados = conn.execute(
            "SELECT COUNT(*) AS t FROM leads WHERE user_id = ? AND lost_reason = ?",
            (user["id"], linha["label"]),
        ).fetchone()["t"]
        if int(usados) > 0:

            raise HTTPException(
                status_code=400,
                detail=f"{usados} negócio(s) perdido(s) usam este motivo. Desative-o em vez de apagar.",
            )
        conn.execute("DELETE FROM loss_reasons WHERE id = ? AND user_id = ?", (reason_id, user["id"]))
        return _loss_rows(conn, user["id"])

class LossSlice(BaseModel):
    label: str
    count: int
    value: float
    percent: float

class LossTrendPoint(BaseModel):
    label: str
    total: int
    por_motivo: dict[str, int]

class LossReportOut(BaseModel):
    has_data: bool
    total_perdido: int
    valor_perdido: float
    total_ganho: int
    valor_ganho: float
    taxa_perda: float
    motivos: list[LossSlice]
    evolucao: list[LossTrendPoint]

MONTH_ABBR = ("Jan", "Fev", "Mar", "Abr", "Mai", "Jun",
              "Jul", "Ago", "Set", "Out", "Nov", "Dez")

@router.get("/reports/losses", response_model=LossReportOut)
def loss_report(user: CurrentUser) -> dict:
    vis, vp = orgs.clausula_visibilidade(orgs.escopo_owner(user))
    with db.get_conn() as conn:
        perdidos = conn.execute(
            f"""SELECT lost_reason, COUNT(*) AS quantidade, COALESCE(SUM(value), 0) AS valor
                 FROM leads WHERE user_id = ? AND status = 'Perdido'{vis}
             GROUP BY lost_reason ORDER BY quantidade DESC, valor DESC""",
            (user["id"], *vp),
        ).fetchall()
        ganhos = conn.execute(
            f"""SELECT COUNT(*) AS quantidade, COALESCE(SUM(value), 0) AS valor
                 FROM leads WHERE user_id = ? AND status = 'Ganho'{vis}""",
            (user["id"], *vp),
        ).fetchone()
        linha_tempo = conn.execute(
            f"""SELECT lost_reason, COALESCE(closed_at, updated_at) AS quando
                 FROM leads WHERE user_id = ? AND status = 'Perdido'{vis}""",
            (user["id"], *vp),
        ).fetchall()

    total_perdido = sum(int(l["quantidade"]) for l in perdidos)
    valor_perdido = round(sum(float(l["valor"]) for l in perdidos), 2)
    total_ganho = int(ganhos["quantidade"])
    valor_ganho = round(float(ganhos["valor"]), 2)

    if total_perdido == 0:
        return {
            "has_data": False, "total_perdido": 0, "valor_perdido": 0.0,
            "total_ganho": total_ganho, "valor_ganho": valor_ganho,
            "taxa_perda": 0.0, "motivos": [], "evolucao": [],
        }

    motivos = [
        {
            "label": l["lost_reason"] or "Sem motivo informado",
            "count": int(l["quantidade"]),
            "value": round(float(l["valor"]), 2),
            "percent": round(int(l["quantidade"]) / total_perdido * 100, 1),
        }
        for l in perdidos
    ]

    agora = db.utcnow()
    meses: list[tuple[int, int]] = []
    for tras in range(5, -1, -1):
        mes, ano = agora.month - tras, agora.year
        while mes <= 0:
            mes += 12
            ano -= 1
        meses.append((ano, mes))

    baldes: dict[tuple[int, int], dict[str, int]] = {chave: {} for chave in meses}
    for linha in linha_tempo:
        quando = db.try_parse_iso(linha["quando"])
        if quando is None:
            continue
        chave = (quando.year, quando.month)
        if chave in baldes:
            rotulo = linha["lost_reason"] or "Sem motivo informado"
            baldes[chave][rotulo] = baldes[chave].get(rotulo, 0) + 1

    evolucao = [
        {
            "label": f"{MONTH_ABBR[mes - 1]}/{ano % 100:02d}",
            "total": sum(baldes[(ano, mes)].values()),
            "por_motivo": baldes[(ano, mes)],
        }
        for ano, mes in meses
    ]

    fechados = total_perdido + total_ganho
    return {
        "has_data": True,
        "total_perdido": total_perdido,
        "valor_perdido": valor_perdido,
        "total_ganho": total_ganho,
        "valor_ganho": valor_ganho,
        "taxa_perda": round(total_perdido / fechados * 100, 1) if fechados else 0.0,
        "motivos": motivos,
        "evolucao": evolucao,
    }

class CustomFieldIn(BaseModel):
    label: str = Field(min_length=1, max_length=60)
    type: Literal[
        "texto", "numero", "moeda", "data", "lista", "multipla", "sim_nao", "email", "telefone"
    ] = "texto"
    description: str = Field(default="", max_length=200)
    required: bool = False
    options: list[str] = Field(default_factory=list)

    @field_validator("label")
    @classmethod
    def _v_label(cls, v: str) -> str:
        return _texto(v, "Nome do campo", 60)

    @field_validator("options")
    @classmethod
    def _v_options(cls, v: list[str]) -> list[str]:
        limpas = [str(o).strip()[:60] for o in v if str(o).strip()]
        if len(limpas) > customfields.MAX_OPTIONS:
            raise ValueError(f"no máximo {customfields.MAX_OPTIONS} opções")
        return limpas

class CustomFieldPatch(BaseModel):
    label: str | None = Field(default=None, min_length=1, max_length=60)
    description: str | None = Field(default=None, max_length=200)
    required: bool | None = None
    active: bool | None = None
    position: int | None = Field(default=None, ge=0, le=999)
    options: list[str] | None = None

class CustomFieldOut(BaseModel):
    id: int
    entity: str
    key: str
    label: str
    type: str
    options: list[str]
    description: str
    required: bool
    position: int
    active: bool

@router.get("/custom-fields", response_model=list[CustomFieldOut])
def list_custom_fields(user: CurrentUser) -> list[dict]:
    with db.get_conn() as conn:
        return customfields.list_fields(conn, user["id"], "lead")

@router.post("/custom-fields", response_model=list[CustomFieldOut], status_code=201)
def create_custom_field(payload: CustomFieldIn, user: CurrentUser) -> list[dict]:
    if payload.type in ("lista", "multipla") and not payload.options:
        raise HTTPException(
            status_code=400, detail="Um campo de lista precisa de pelo menos uma opção"
        )
    with db.get_conn() as conn:
        if customfields.count_fields(conn, user["id"], "lead") >= customfields.MAX_FIELDS_PER_ENTITY:
            raise HTTPException(
                status_code=400,
                detail=f"Limite de {customfields.MAX_FIELDS_PER_ENTITY} campos personalizados atingido",
            )
        chave = customfields.unique_key(conn, user["id"], "lead", payload.label)
        proxima = conn.execute(
            "SELECT COALESCE(MAX(position), -1) + 1 AS p FROM custom_fields WHERE user_id = ? AND entity = 'lead'",
            (user["id"],),
        ).fetchone()["p"]
        conn.execute(
            """INSERT INTO custom_fields
                   (user_id, entity, key, label, type, options, description,
                    required, position, active, created_at)
               VALUES (?, 'lead', ?, ?, ?, ?, ?, ?, ?, 1, ?)""",
            (user["id"], chave, payload.label, payload.type, db.json_dump(payload.options),
             payload.description.strip(), 1 if payload.required else 0, proxima, db.now_iso()),
        )
        return customfields.list_fields(conn, user["id"], "lead")

@router.patch("/custom-fields/{field_id}", response_model=list[CustomFieldOut])
def update_custom_field(field_id: int, payload: CustomFieldPatch, user: CurrentUser) -> list[dict]:
    mudancas = payload.model_dump(exclude_unset=True, exclude_none=True)
    if not mudancas:
        raise HTTPException(status_code=400, detail="Informe ao menos um campo")

    with db.get_conn() as conn:
        atual = conn.execute(
            "SELECT * FROM custom_fields WHERE id = ? AND user_id = ?", (field_id, user["id"])
        ).fetchone()
        if atual is None:
            raise HTTPException(status_code=404, detail="Campo não encontrado")

        colunas = {
            "label": lambda v: _texto(v, "Nome do campo", 60),
            "description": lambda v: str(v).strip()[:200],
            "required": lambda v: 1 if v else 0,
            "active": lambda v: 1 if v else 0,
            "position": lambda v: int(v),
            "options": lambda v: db.json_dump([str(o).strip()[:60] for o in v if str(o).strip()]),
        }
        for coluna, converte in colunas.items():
            if coluna in mudancas:
                conn.execute(
                    f"UPDATE custom_fields SET {coluna} = ? WHERE id = ? AND user_id = ?",
                    (converte(mudancas[coluna]), field_id, user["id"]),
                )
        return customfields.list_fields(conn, user["id"], "lead")

@router.delete("/custom-fields/{field_id}", response_model=list[CustomFieldOut])
def delete_custom_field(field_id: int, user: CurrentUser) -> list[dict]:
    with db.get_conn() as conn:
        linha = conn.execute(
            "SELECT id FROM custom_fields WHERE id = ? AND user_id = ?", (field_id, user["id"])
        ).fetchone()
        if linha is None:
            raise HTTPException(status_code=404, detail="Campo não encontrado")

        conn.execute("DELETE FROM custom_fields WHERE id = ? AND user_id = ?", (field_id, user["id"]))
        return customfields.list_fields(conn, user["id"], "lead")

@router.get("/custom-fields/{field_id}/usage")
def custom_field_usage(field_id: int, user: CurrentUser) -> dict:
    with db.get_conn() as conn:
        linha = conn.execute(
            "SELECT id FROM custom_fields WHERE id = ? AND user_id = ?", (field_id, user["id"])
        ).fetchone()
        if linha is None:
            raise HTTPException(status_code=404, detail="Campo não encontrado")
        total = conn.execute(
            "SELECT COUNT(*) AS t FROM custom_values WHERE field_id = ? AND user_id = ?",
            (field_id, user["id"]),
        ).fetchone()["t"]
    return {"preenchidos": int(total)}

class SearchItem(BaseModel):
    id: int
    title: str
    subtitle: str
    meta: str
    route: str
    """Para onde o clique leva -- montado no servidor para que a tela nao
    precise saber a regra de roteamento de cada tipo de registro."""

class SearchGroup(BaseModel):
    kind: str
    label: str
    items: list[SearchItem]

class SearchOut(BaseModel):
    query: str
    total: int
    groups: list[SearchGroup]

LIMITE_POR_GRUPO = 6

def _brl(valor: float) -> str:
    return f"R$ {valor:,.2f}".replace(",", "@").replace(".", ",").replace("@", ".")

@router.get("/search", response_model=SearchOut)
def search(user: CurrentUser, q: str = Query(default="", max_length=80)) -> dict:
    termo = (q or "").strip()
    if len(termo) < 2:
        return {"query": termo, "total": 0, "groups": []}

    padrao = f"%{db.deburr(termo)}%"
    user_id = user["id"]
    grupos: list[dict] = []

    scope = orgs.escopo_owner(user)
    vis_own, vp_own = orgs.clausula_visibilidade(scope)
    vis_l, vp_l = orgs.clausula_visibilidade(scope, "l.owner_user_id")
    if scope is None:
        vis_prop = vis_act = ""
        vp_sub: list = []
    else:
        _sub = "SELECT id FROM leads WHERE user_id = ? AND (owner_user_id = ? OR owner_user_id IS NULL)"
        vis_prop = f" AND p.lead_id IN ({_sub})"
        vis_act = f" AND (a.lead_id IS NULL OR a.lead_id IN ({_sub}))"
        vp_sub = [user_id, scope]

    with db.get_conn() as conn:
        leads = conn.execute(
            f"""SELECT {', '.join(crm.LEAD_FIELDS)} FROM leads
                 WHERE user_id = ?{vis_own}
                   AND (unaccent(name)     LIKE ? OR unaccent(company) LIKE ?
                     OR unaccent(email)    LIKE ? OR unaccent(owner)   LIKE ?
                     OR unaccent(notes)    LIKE ? OR unaccent(tags)    LIKE ?
                     OR unaccent(source)   LIKE ?
                     OR replace(replace(replace(phone,    '-', ''), ' ', ''), '(', '') LIKE ?
                     OR replace(replace(replace(whatsapp, '-', ''), ' ', ''), '(', '') LIKE ?)
              ORDER BY updated_at DESC LIMIT ?""",
            (user_id, *vp_own, *([padrao] * 7), padrao, padrao, LIMITE_POR_GRUPO),
        ).fetchall()
        if leads:
            grupos.append({
                "kind": "leads", "label": "Leads",
                "items": [
                    {
                        "id": int(l["id"]),
                        "title": l["name"],
                        "subtitle": l["company"],
                        "meta": f"{l['status']} · {_brl(float(l['value']))}",
                        "route": f"#/lead/{l['id']}",
                    }
                    for l in leads
                ],
            })

        personalizados = conn.execute(
            f"""SELECT v.entity_id, f.label AS campo, v.value_text,
                      COALESCE(l.name, '') AS lead_name, COALESCE(l.company, '') AS lead_company
                 FROM custom_values v
                 JOIN custom_fields f ON f.id = v.field_id
                 JOIN leads l ON l.id = v.entity_id AND l.user_id = v.user_id
                WHERE v.user_id = ?{vis_l} AND v.entity = 'lead' AND unaccent(v.value_text) LIKE ?
             ORDER BY v.updated_at DESC LIMIT ?""",
            (user_id, *vp_l, padrao, LIMITE_POR_GRUPO),
        ).fetchall()
        if personalizados:
            grupos.append({
                "kind": "campos", "label": "Campos personalizados",
                "items": [
                    {
                        "id": int(c["entity_id"]),
                        "title": c["lead_name"],
                        "subtitle": f"{c['campo']}: {c['value_text']}",
                        "meta": c["lead_company"],
                        "route": f"#/lead/{c['entity_id']}",
                    }
                    for c in personalizados
                ],
            })

        propostas = conn.execute(
            f"""SELECT p.id, p.title, p.number, p.status, p.total, p.lead_id,
                      COALESCE(p.client_company, '') AS empresa
                 FROM proposals p
                WHERE p.user_id = ?{vis_prop}
                  AND (unaccent(p.title) LIKE ? OR unaccent(p.number) LIKE ?
                    OR unaccent(p.client_name) LIKE ? OR unaccent(p.client_company) LIKE ?)
             ORDER BY p.updated_at DESC LIMIT ?""",
            (user_id, *vp_sub, padrao, padrao, padrao, padrao, LIMITE_POR_GRUPO),
        ).fetchall()
        if propostas:
            grupos.append({
                "kind": "propostas", "label": "Propostas",
                "items": [
                    {
                        "id": int(p["id"]),
                        "title": p["title"],
                        "subtitle": f"{p['number']} · {p['empresa']}".strip(" ·"),
                        "meta": f"{p['status']} · {_brl(float(p['total']))}",
                        "route": f"#/proposta/{p['id']}",
                    }
                    for p in propostas
                ],
            })

        atividades = conn.execute(
            f"""SELECT a.id, a.title, a.detail, a.kind, a.created_at, a.lead_id,
                      COALESCE(l.name, '') AS lead_name
                 FROM activities a
            LEFT JOIN leads l ON l.id = a.lead_id AND l.user_id = a.user_id
                WHERE a.user_id = ?{vis_act}
                  AND (unaccent(a.title) LIKE ? OR unaccent(a.detail) LIKE ?)
             ORDER BY a.created_at DESC LIMIT ?""",
            (user_id, *vp_sub, padrao, padrao, LIMITE_POR_GRUPO),
        ).fetchall()
        if atividades:
            grupos.append({
                "kind": "atividades", "label": "Histórico",
                "items": [
                    {
                        "id": int(a["id"]),
                        "title": a["title"],
                        "subtitle": (a["detail"] or "")[:90],
                        "meta": a["lead_name"] or "—",
                        "route": f"#/lead/{a['lead_id']}" if a["lead_id"] else "#/dashboard",
                    }
                    for a in atividades
                ],
            })

    return {
        "query": termo,
        "total": sum(len(g["items"]) for g in grupos),
        "groups": grupos,
    }

class ImportIn(BaseModel):
    csv: str = Field(min_length=1, max_length=importacao.MAX_CSV_CHARS)
    mapping: dict[str, str] = Field(default_factory=dict)
    has_header: bool = True

    @field_validator("mapping")
    @classmethod
    def _v_mapping(cls, v: dict) -> dict:

        return {
            str(k)[:40]: str(val)[:120]
            for k, val in list(v.items())[:importacao.MAX_COLUNAS]
            if k in importacao.CAMPOS
        }

class ImportConfirmIn(ImportIn):
    pular_duplicados: bool = True

class ImportAmostraItem(BaseModel):
    linha: int
    estado: str
    motivo: str
    nome: str
    empresa: str
    valor: float

class ImportPreviewOut(BaseModel):
    colunas: list[str]
    mapeamento_sugerido: dict[str, str]
    total: int
    novos: int
    duplicados: int
    com_erro: int
    amostra: list[ImportAmostraItem]

class ImportErroItem(BaseModel):
    linha: int
    motivo: str

class ImportConfirmOut(BaseModel):
    inseridos: int
    pulados_duplicados: int
    com_erro: int

    barrados_limite: int = 0
    erros: list[ImportErroItem]

IMPORT_BUCKET = "import"
IMPORT_LIMIT = 30
IMPORT_WINDOW = 60 * 60

def _teto_da_importacao(user: dict) -> None:
    if auth._register_hit(IMPORT_BUCKET, str(user["actor_id"]), IMPORT_LIMIT, IMPORT_WINDOW):
        raise HTTPException(
            status_code=429,
            detail="Muitas importações seguidas. Tente novamente em uma hora.",
        )

@router.post("/import/preview", response_model=ImportPreviewOut)
def import_preview(payload: ImportIn, user: CurrentUser) -> dict:
    _teto_da_importacao(user)
    with db.get_conn() as conn:
        try:
            return importacao.analisar(
                conn, user["id"], payload.csv, payload.mapping, payload.has_header
            )
        except importacao.ImportacaoInvalida as erro:
            raise HTTPException(status_code=400, detail=str(erro)) from None

@router.post("/import/confirm", response_model=ImportConfirmOut)
def import_confirm(payload: ImportConfirmIn, user: CurrentUser) -> dict:
    _teto_da_importacao(user)
    with db.get_conn() as conn:
        try:
            return importacao.importar(
                conn, user["id"], payload.csv, payload.mapping,
                payload.has_header, payload.pular_duplicados,
            )
        except importacao.ImportacaoInvalida as erro:
            raise HTTPException(status_code=400, detail=str(erro)) from None
