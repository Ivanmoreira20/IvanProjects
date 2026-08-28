from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, HTTPException, Path, Response, status
from pydantic import BaseModel, Field

import audit
import config
import db
import orgs
from auth import CurrentUser

router = APIRouter(prefix="/api/org", tags=["org"])

class MemberOut(BaseModel):
    user_id: int
    name: str
    email: str
    role: str
    created_at: str
    is_me: bool

    avatar: str = ""

class OrgOut(BaseModel):
    id: int
    name: str
    my_role: str

    is_account_owner: bool
    can_manage_team: bool
    can_manage_billing: bool
    members: list[MemberOut]

class InviteCreateIn(BaseModel):
    role: Literal["admin", "gestor", "vendedor"] = "vendedor"
    email: str = Field(default="", max_length=254)

class InviteCreatedOut(BaseModel):
    token: str
    link: str
    role: str

class InviteRow(BaseModel):
    id: int
    role: str
    email: str
    expires_at: str
    created_at: str
    criado_por: str

class InvitesOut(BaseModel):
    items: list[InviteRow]

class InviteAcceptIn(BaseModel):
    token: str = Field(min_length=8, max_length=200)

class InviteAcceptedOut(BaseModel):
    ok: bool
    org_name: str
    role: str

class MemberRoleIn(BaseModel):
    role: Literal["admin", "gestor", "vendedor"]

class AuditRow(BaseModel):
    actor_name: str
    action: str
    created_at: str
    texto: str

class AuditOut(BaseModel):
    items: list[AuditRow]

def _montar_org(user: dict[str, Any], membros: list[dict[str, Any]]) -> dict[str, Any]:
    actor = user["actor_id"]
    return {
        "id": user["org_id"],
        "name": user["org_name"],
        "my_role": user["role"],
        "is_account_owner": actor == user["id"],
        "can_manage_team": orgs.pode(user["role"], orgs.GERIR_EQUIPE),
        "can_manage_billing": orgs.pode(user["role"], orgs.GERIR_COBRANCA),
        "members": [{**m, "is_me": m["user_id"] == actor} for m in membros],
    }

@router.get("", response_model=OrgOut)
def get_my_org(user: CurrentUser) -> dict[str, Any]:
    with db.get_conn() as conn:
        membros = orgs.list_members(conn, user["org_id"])
    return _montar_org(user, membros)

@router.get("/audit", response_model=AuditOut)
def listar_auditoria(user: CurrentUser) -> dict[str, Any]:
    orgs.exigir(user, orgs.GERIR_EQUIPE)
    with db.get_conn() as conn:
        return {"items": audit.list_for_org(conn, user["org_id"], limit=50)}

@router.post("/invites", response_model=InviteCreatedOut, status_code=status.HTTP_201_CREATED)
def criar_convite(payload: InviteCreateIn, user: CurrentUser) -> dict[str, Any]:
    orgs.exigir(user, orgs.GERIR_EQUIPE)

    if payload.role == orgs.ADMIN and user["role"] != orgs.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Só um Admin pode convidar outro Admin.",
        )
    with db.get_conn() as conn:
        token = orgs.criar_convite(
            conn, user["org_id"], payload.role, payload.email, user["actor_id"]
        )

        audit.log(
            conn, user, audit.CONVITE_CRIADO,
            target_type="invite",
            target_label=(payload.email or "").strip(),
            detail=orgs.ROLE_LABELS.get(payload.role, payload.role),
        )
    link = f"{config.app_base_url()}/app#/convite/{token}"
    return {"token": token, "link": link, "role": payload.role}

@router.get("/invites", response_model=InvitesOut)
def listar_convites(user: CurrentUser) -> dict[str, Any]:
    orgs.exigir(user, orgs.GERIR_EQUIPE)
    with db.get_conn() as conn:
        return {"items": orgs.listar_convites(conn, user["org_id"])}

@router.delete("/invites/{invite_id}", status_code=status.HTTP_204_NO_CONTENT)
def revogar_convite(invite_id: Annotated[int, Path(ge=1)], user: CurrentUser) -> Response:
    orgs.exigir(user, orgs.GERIR_EQUIPE)
    with db.get_conn() as conn:
        alvo = conn.execute(
            "SELECT email FROM org_invites WHERE id = ? AND org_id = ?",
            (invite_id, user["org_id"]),
        ).fetchone()
        ok = orgs.revogar_convite(conn, user["org_id"], invite_id)
        if ok:
            audit.log(
                conn, user, audit.CONVITE_REVOGADO,
                target_type="invite", target_id=invite_id,
                target_label=(alvo["email"] if alvo else "") or "",
            )
    if not ok:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Convite não encontrado.")
    return Response(status_code=status.HTTP_204_NO_CONTENT)

@router.post("/invites/accept", response_model=InviteAcceptedOut)
def aceitar_convite(payload: InviteAcceptIn, user: CurrentUser) -> dict[str, Any]:
    with db.get_conn() as conn:
        ctx = orgs.aceitar_convite(conn, payload.token.strip(), user["actor_id"])

        audit.log(
            conn,
            {"org_id": ctx["org_id"], "actor_id": user["actor_id"], "name": user["name"]},
            audit.MEMBRO_ENTROU,
            target_type="member", target_id=user["actor_id"],
            detail=orgs.ROLE_LABELS.get(ctx["role"], ctx["role"]),
        )
    return {"ok": True, "org_name": ctx["org_name"], "role": ctx["role"]}

@router.patch("/members/{member_id}", response_model=OrgOut)
def mudar_papel_membro(
    member_id: Annotated[int, Path(ge=1)], payload: MemberRoleIn, user: CurrentUser
) -> dict[str, Any]:
    orgs.exigir(user, orgs.GERIR_EQUIPE)
    if member_id == user["actor_id"]:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Você não pode mudar o seu próprio papel.",
        )
    with db.get_conn() as conn:
        alvo = orgs._membership(conn, user["org_id"], member_id)
        if alvo is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Membro não encontrado.")

        if (alvo["role"] == orgs.ADMIN or payload.role == orgs.ADMIN) and user["role"] != orgs.ADMIN:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Só um Admin gerencia o papel de outro Admin.",
            )
        orgs.mudar_papel(conn, user["org_id"], user["id"], member_id, payload.role)
        membros = orgs.list_members(conn, user["org_id"])
        nome_alvo = next((m["name"] for m in membros if m["user_id"] == member_id), "")
        audit.log(
            conn, user, audit.PAPEL_MUDADO,
            target_type="member", target_id=member_id, target_label=nome_alvo,
            detail=orgs.ROLE_LABELS.get(payload.role, payload.role),
        )
    return _montar_org(user, membros)

@router.delete("/members/{member_id}", response_model=OrgOut)
def remover_membro(member_id: Annotated[int, Path(ge=1)], user: CurrentUser) -> dict[str, Any]:
    orgs.exigir(user, orgs.GERIR_EQUIPE)
    if member_id == user["actor_id"]:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Você não pode remover a si mesmo aqui.",
        )
    with db.get_conn() as conn:
        alvo = orgs._membership(conn, user["org_id"], member_id)
        if alvo is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Membro não encontrado.")
        if alvo["role"] == orgs.ADMIN and user["role"] != orgs.ADMIN:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Só um Admin remove outro Admin.",
            )

        nome_alvo = conn.execute(
            "SELECT name FROM users WHERE id = ?", (member_id,)
        ).fetchone()
        orgs.remover_membro(conn, user["org_id"], user["id"], user["id"], member_id)
        audit.log(
            conn, user, audit.MEMBRO_REMOVIDO,
            target_type="member", target_id=member_id,
            target_label=(nome_alvo["name"] if nome_alvo else "") or "",
        )
        membros = orgs.list_members(conn, user["org_id"])
    return _montar_org(user, membros)
