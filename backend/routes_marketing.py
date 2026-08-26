from __future__ import annotations

import json
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Path, Request, status
from pydantic import BaseModel, Field

import audit
import auth
import config
import db
import marketing
from auth import CurrentUser

TEST_BUCKET, TEST_LIMIT, TEST_WINDOW = "mkt_test", 10, 3600
SEND_BUCKET, SEND_LIMIT, SEND_WINDOW = "mkt_send", 8, 3600
UNSUB_BUCKET, UNSUB_LIMIT, UNSUB_WINDOW = "mkt_unsub", 60, 3600

def exigir_marketing() -> None:
    if not config.marketing_enabled():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Não encontrado")

def _exige_gestao(user: dict[str, Any]) -> None:
    if user.get("role") not in ("admin", "gestor"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Só Admin ou Gestor gerenciam marketing.")

router = APIRouter(
    prefix="/api/marketing",
    tags=["marketing"],
    dependencies=[Depends(exigir_marketing)],
)
router_pub = APIRouter(prefix="/api/marketing", tags=["marketing-público"])

class StatusOut(BaseModel):
    enabled: bool
    provider: str
    smtp_cap: int

class ConsentIn(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    status: str = Field(pattern="^(subscribed|unsubscribed|pending)$")

class SuppressIn(BaseModel):
    email: str = Field(min_length=3, max_length=254)

class CampaignIn(BaseModel):
    name: str = Field(min_length=1, max_length=140)
    subject: str = Field(default="", max_length=200)
    body_html: str = Field(default="", max_length=100_000)
    from_name: str = Field(default="", max_length=120)
    from_email: str = Field(default="", max_length=254)
    preheader: str = Field(default="", max_length=200)
    segmento: dict[str, Any] = Field(default_factory=dict)

class CampaignOut(BaseModel):
    id: int
    name: str
    subject: str
    status: str
    total_dest: int
    created_at: str

class TestIn(BaseModel):
    email: str = Field(default="", max_length=254)

class UnsubIn(BaseModel):
    token: str = Field(min_length=8, max_length=128)

@router.get("/status", response_model=StatusOut)
def status_modulo() -> dict[str, Any]:
    return {
        "enabled": True,
        "provider": config.mkt_provider(),
        "smtp_cap": config.mkt_smtp_daily_cap(),
    }

@router.get("/contatos")
def listar_contatos(user: CurrentUser) -> dict[str, Any]:
    uid = int(user["id"])
    with db.get_conn() as conn:
        linhas = conn.execute(
            """SELECT l.id, l.name, COALESCE(l.company,'') AS company, l.email,
                      COALESCE(c.status,'pending') AS consent,
                      CASE WHEN s.email IS NOT NULL THEN 1 ELSE 0 END AS suprimido
                 FROM leads l
                 LEFT JOIN mkt_consent c ON c.user_id = l.user_id AND c.email = lower(l.email)
                 LEFT JOIN mkt_suppression s ON s.user_id = l.user_id AND s.email = lower(l.email)
                WHERE l.user_id = ? AND l.email != ''
                ORDER BY l.id DESC LIMIT 500""",
            (uid,),
        ).fetchall()
    return {"items": [dict(r) for r in linhas]}

@router.post("/consent")
def definir_consent(payload: ConsentIn, user: CurrentUser) -> dict[str, str]:
    _exige_gestao(user)
    with db.get_conn() as conn:
        marketing.definir_consentimento(conn, int(user["id"]), payload.email, payload.status, source="painel")
        audit.log(conn, user, "mkt_consent", target_type="email", detail=f"{payload.email}={payload.status}")
    return {"status": payload.status}

@router.post("/suppress")
def suprimir_email(payload: SuppressIn, user: CurrentUser) -> dict[str, str]:
    _exige_gestao(user)
    with db.get_conn() as conn:
        marketing.suprimir(conn, int(user["id"]), payload.email, reason="manual")
    return {"ok": "1"}

@router.get("/campaigns")
def listar_campanhas(user: CurrentUser) -> dict[str, Any]:
    uid = int(user["id"])
    with db.get_conn() as conn:
        linhas = conn.execute(
            "SELECT id, name, subject, status, total_dest, created_at "
            "FROM mkt_campaigns WHERE user_id = ? ORDER BY id DESC LIMIT 200",
            (uid,),
        ).fetchall()
    return {"items": [dict(r) for r in linhas]}

@router.post("/campaigns", response_model=CampaignOut)
def criar_campanha(payload: CampaignIn, user: CurrentUser) -> dict[str, Any]:
    _exige_gestao(user)
    uid = int(user["id"])
    corpo = marketing.sanitizar(payload.body_html)
    agora = db.now_iso()
    with db.get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO mkt_campaigns
                   (user_id, name, subject, preheader, from_name, from_email,
                    body_html, segmento, status, created_by, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'draft', ?, ?, ?)""",
            (uid, payload.name.strip(), payload.subject.strip(), payload.preheader.strip(),
             payload.from_name.strip(), payload.from_email.strip(), corpo,
             json.dumps(payload.segmento), int(user["actor_id"]), agora, agora),
        )
        cid = cur.lastrowid
        row = conn.execute("SELECT * FROM mkt_campaigns WHERE id = ?", (cid,)).fetchone()
    return dict(row)

def _campanha(conn: db.Connection, uid: int, cid: int) -> Any:
    row = conn.execute("SELECT * FROM mkt_campaigns WHERE id = ? AND user_id = ?", (cid, uid)).fetchone()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Campanha não encontrada.")
    return row

@router.get("/campaigns/{cid}")
def detalhe_campanha(cid: Annotated[int, Path(ge=1)], user: CurrentUser) -> dict[str, Any]:
    uid = int(user["id"])
    with db.get_conn() as conn:
        row = _campanha(conn, uid, cid)
        segmento = marketing._carregar_segmento(row["segmento"])
        elegiveis = marketing.contar_elegiveis(conn, uid, segmento)
        stats = dict(conn.execute(
            """SELECT
                 SUM(status='sent') AS enviados,
                 SUM(status='failed') AS falhas,
                 SUM(status='skipped') AS pulados,
                 SUM(status IN ('queued','sending')) AS na_fila
               FROM mkt_messages WHERE campaign_id = ?""",
            (cid,),
        ).fetchone())
    d = dict(row)
    d["elegiveis"] = elegiveis
    d["stats"] = {k: int(v or 0) for k, v in stats.items()}
    return d

@router.patch("/campaigns/{cid}", response_model=CampaignOut)
def editar_campanha(cid: Annotated[int, Path(ge=1)], payload: CampaignIn, user: CurrentUser) -> dict[str, Any]:
    _exige_gestao(user)
    uid = int(user["id"])
    corpo = marketing.sanitizar(payload.body_html)
    with db.get_conn() as conn:
        row = _campanha(conn, uid, cid)
        if row["status"] != "draft":
            raise HTTPException(status.HTTP_409_CONFLICT, "Só rascunho pode ser editado.")
        conn.execute(
            """UPDATE mkt_campaigns SET name=?, subject=?, preheader=?, from_name=?,
                   from_email=?, body_html=?, segmento=?, updated_at=?
                 WHERE id=? AND user_id=?""",
            (payload.name.strip(), payload.subject.strip(), payload.preheader.strip(),
             payload.from_name.strip(), payload.from_email.strip(), corpo,
             json.dumps(payload.segmento), db.now_iso(), cid, uid),
        )
        row = _campanha(conn, uid, cid)
    return dict(row)

@router.get("/campaigns/{cid}/preview")
def preview_publico(cid: Annotated[int, Path(ge=1)], user: CurrentUser) -> dict[str, int]:
    uid = int(user["id"])
    with db.get_conn() as conn:
        row = _campanha(conn, uid, cid)
        n = marketing.contar_elegiveis(conn, uid, marketing._carregar_segmento(row["segmento"]))
    return {"elegiveis": n}

@router.post("/campaigns/{cid}/test")
def enviar_teste(cid: Annotated[int, Path(ge=1)], payload: TestIn, user: CurrentUser) -> dict[str, str]:
    _exige_gestao(user)
    if auth._register_hit(TEST_BUCKET, str(user["actor_id"]), TEST_LIMIT, TEST_WINDOW):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Muitos testes seguidos. Tente mais tarde.")
    uid = int(user["id"])
    with db.get_conn() as conn:
        row = _campanha(conn, uid, cid)
        destino = conn.execute(
            "SELECT email FROM users WHERE id = ?", (int(user["actor_id"]),)
        ).fetchone()["email"]
        token = marketing.token_para(conn, uid, destino)
        contato = {"email": destino, "nome": "Você (teste)", "empresa": ""}
        assunto = "[TESTE] " + marketing.renderizar(row["subject"] or "(sem assunto)", contato)
        unsub = f"{config.app_base_url()}/descadastro?t={token}"
        html_final = marketing.montar_html(dict(row), contato, unsub)
    try:
        marketing.enviar_um(destino, assunto, html_final, marketing._texto_de(html_final), row["from_email"] or "")
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Falha no envio de teste: {type(e).__name__}") from e
    return {"enviado_para": destino}

@router.post("/campaigns/{cid}/send")
def disparar(cid: Annotated[int, Path(ge=1)], user: CurrentUser) -> dict[str, Any]:
    _exige_gestao(user)
    if auth._register_hit(SEND_BUCKET, str(user["id"]), SEND_LIMIT, SEND_WINDOW):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Muitos disparos seguidos. Tente mais tarde.")
    uid = int(user["id"])
    with db.get_conn() as conn:
        row = _campanha(conn, uid, cid)
        if row["status"] not in ("draft", "scheduled"):
            raise HTTPException(status.HTTP_409_CONFLICT, f"Campanha já está '{row['status']}'.")
        n = marketing.enfileirar(conn, uid, cid)
        audit.log(conn, user, "mkt_send", target_type="campaign", target_id=cid, detail=f"{n} destinatários")
    return {"enfileirados": n}

@router.post("/campaigns/{cid}/pause")
def pausar(cid: Annotated[int, Path(ge=1)], user: CurrentUser) -> dict[str, str]:
    _exige_gestao(user)
    uid = int(user["id"])
    with db.get_conn() as conn:
        _campanha(conn, uid, cid)
        conn.execute(
            "UPDATE mkt_campaigns SET status='paused', updated_at=? WHERE id=? AND user_id=? AND status IN ('queued','sending')",
            (db.now_iso(), cid, uid),
        )
    return {"status": "paused"}

@router_pub.post("/unsubscribe")
def descadastrar(payload: UnsubIn, request: Request) -> dict[str, str]:
    ip = (request.client.host if request.client else "?") or "?"
    if auth._register_hit(UNSUB_BUCKET, ip, UNSUB_LIMIT, UNSUB_WINDOW):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Muitas tentativas. Tente mais tarde.")
    with db.get_conn() as conn:
        marketing.descadastrar_por_token(conn, payload.token)

    return {"status": "ok"}
