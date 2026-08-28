from __future__ import annotations

import hashlib
import secrets
from datetime import timedelta
from typing import Any

from fastapi import HTTPException, status

import db

ADMIN = "admin"
GESTOR = "gestor"
VENDEDOR = "vendedor"
ROLES: tuple[str, ...] = (ADMIN, GESTOR, VENDEDOR)

ROLE_LABELS: dict[str, str] = {
    ADMIN: "Admin",
    GESTOR: "Gestor",
    VENDEDOR: "Vendedor",
}

VER_TODOS_LEADS = "ver_todos_leads"
ATRIBUIR_LEAD = "atribuir_lead"
GERIR_EQUIPE = "gerir_equipe"
GERIR_COBRANCA = "gerir_cobranca"

_PERMISSOES: dict[str, frozenset[str]] = {
    ADMIN: frozenset({VER_TODOS_LEADS, ATRIBUIR_LEAD, GERIR_EQUIPE, GERIR_COBRANCA}),
    GESTOR: frozenset({VER_TODOS_LEADS, ATRIBUIR_LEAD, GERIR_EQUIPE}),
    VENDEDOR: frozenset(),
}

def pode(role: str, acao: str) -> bool:
    return acao in _PERMISSOES.get(role or "", frozenset())

def escopo_owner(user: dict[str, Any]) -> int | None:
    return None if pode(user.get("role", ""), VER_TODOS_LEADS) else int(user["actor_id"])

_COLUNAS_VISIBILIDADE = frozenset(
    {"owner_user_id", "l.owner_user_id", "a.owner_user_id"}
)

def clausula_visibilidade(
    owner_scope: int | None, coluna: str = "owner_user_id"
) -> tuple[str, list[Any]]:
    if coluna not in _COLUNAS_VISIBILIDADE:
        raise ValueError(f"coluna de visibilidade não permitida: {coluna!r}")
    if owner_scope is None:
        return "", []
    return f" AND ({coluna} = ? OR {coluna} IS NULL)", [owner_scope]

def exigir(ctx: dict[str, Any], acao: str) -> None:
    if not pode(ctx.get("role", ""), acao):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Seu papel não permite esta ação.",
        )

def _nome_org(nome_pessoa: str | None) -> str:
    nome = (nome_pessoa or "").strip()
    return f"Equipe de {nome.split()[0]}" if nome else "Minha empresa"

def _create_org(conn: db.Connection, owner_user_id: int, name: str) -> int:
    conn.execute(
        "INSERT OR IGNORE INTO organizations (owner_user_id, name, created_at) VALUES (?, ?, ?)",
        (owner_user_id, name, db.now_iso()),
    )
    row = conn.execute(
        "SELECT id FROM organizations WHERE owner_user_id = ?", (owner_user_id,)
    ).fetchone()
    return int(row["id"])

def ensure_org_for_user(conn: db.Connection, user_id: int, nome_pessoa: str | None = None) -> int:
    existing = conn.execute(
        "SELECT org_id FROM memberships WHERE user_id = ?", (user_id,)
    ).fetchone()
    if existing is not None:
        return int(existing["org_id"])

    org_id = _create_org(conn, user_id, _nome_org(nome_pessoa))
    conn.execute(
        "INSERT OR IGNORE INTO memberships (org_id, user_id, role, created_at) VALUES (?, ?, ?, ?)",
        (org_id, user_id, ADMIN, db.now_iso()),
    )

    linha = conn.execute(
        "SELECT org_id FROM memberships WHERE user_id = ?", (user_id,)
    ).fetchone()
    return int(linha["org_id"]) if linha else org_id

def ensure_backfill() -> int:
    with db.get_conn() as conn:
        pendentes = conn.execute(
            """SELECT u.id, u.name FROM users u
               WHERE NOT EXISTS (SELECT 1 FROM memberships m WHERE m.user_id = u.id)"""
        ).fetchall()
        for r in pendentes:
            ensure_org_for_user(conn, int(r["id"]), r["name"])
    return len(pendentes)

def resolve_context(conn: db.Connection, session_user_id: int) -> dict[str, Any]:
    row = _context_row(conn, session_user_id)
    if row is None:
        u = conn.execute("SELECT name FROM users WHERE id = ?", (session_user_id,)).fetchone()
        ensure_org_for_user(conn, session_user_id, u["name"] if u else None)
        row = _context_row(conn, session_user_id)
    assert row is not None
    return {
        "actor_id": int(session_user_id),
        "org_id": int(row["org_id"]),
        "tenant_id": int(row["owner_user_id"]),
        "role": row["role"],
        "org_name": row["org_name"],
    }

def _context_row(conn: db.Connection, user_id: int) -> db.Row | None:
    return conn.execute(
        """SELECT m.org_id, m.role, o.owner_user_id, o.name AS org_name
             FROM memberships m
             JOIN organizations o ON o.id = m.org_id
            WHERE m.user_id = ?""",
        (user_id,),
    ).fetchone()

def get_org(conn: db.Connection, org_id: int) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT id, owner_user_id, name, created_at FROM organizations WHERE id = ?",
        (org_id,),
    ).fetchone()
    if row is None:
        return None
    return {
        "id": int(row["id"]),
        "owner_user_id": int(row["owner_user_id"]),
        "name": row["name"],
        "created_at": row["created_at"],
    }

def list_members(conn: db.Connection, org_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        """SELECT m.user_id, m.role, m.created_at, u.name, u.email, u.avatar_key
             FROM memberships m
             JOIN users u ON u.id = m.user_id
            WHERE m.org_id = ?
            ORDER BY (m.role = 'admin') DESC, (m.role = 'gestor') DESC, m.created_at ASC""",
        (org_id,),
    ).fetchall()
    return [
        {
            "user_id": int(r["user_id"]),
            "name": r["name"],
            "email": r["email"],
            "role": r["role"],
            "created_at": r["created_at"],
            "avatar": r["avatar_key"] or "",
        }
        for r in rows
    ]

def count_membros(conn: db.Connection, org_id: int) -> int:
    return int(
        conn.execute(
            "SELECT COUNT(*) AS c FROM memberships WHERE org_id = ?", (org_id,)
        ).fetchone()["c"]
    )

INVITE_TTL = timedelta(days=7)

def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()

def criar_convite(
    conn: db.Connection, org_id: int, role: str, email: str, created_by: int
) -> str:
    if role not in ROLES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Papel inválido.")
    token = secrets.token_urlsafe(32)
    agora = db.utcnow()
    conn.execute(
        """INSERT INTO org_invites (org_id, token_hash, role, email, created_by, expires_at, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (org_id, _hash_token(token), role, (email or "").strip().lower(),
         created_by, db.iso(agora + INVITE_TTL), db.iso(agora)),
    )
    return token

def listar_convites(conn: db.Connection, org_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        """SELECT i.id, i.role, i.email, i.expires_at, i.created_at,
                  COALESCE(u.name, '') AS criado_por
             FROM org_invites i
             LEFT JOIN users u ON u.id = i.created_by
            WHERE i.org_id = ? AND i.accepted_at IS NULL AND i.expires_at > ?
            ORDER BY i.created_at DESC""",
        (org_id, db.now_iso()),
    ).fetchall()
    return [
        {
            "id": int(r["id"]),
            "role": r["role"],
            "email": r["email"],
            "expires_at": r["expires_at"],
            "created_at": r["created_at"],
            "criado_por": r["criado_por"],
        }
        for r in rows
    ]

def revogar_convite(conn: db.Connection, org_id: int, invite_id: int) -> bool:
    cur = conn.execute(
        "DELETE FROM org_invites WHERE id = ? AND org_id = ? AND accepted_at IS NULL",
        (invite_id, org_id),
    )
    return (cur.rowcount or 0) > 0

def aceitar_convite(conn: db.Connection, token: str, actor_id: int) -> dict[str, Any]:
    inv = conn.execute(
        "SELECT id, org_id, role, accepted_at, expires_at FROM org_invites WHERE token_hash = ?",
        (_hash_token(token),),
    ).fetchone()
    if inv is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Convite inválido.")
    if inv["accepted_at"] is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Este convite já foi usado.")
    if db.parse_iso(inv["expires_at"]) <= db.utcnow():
        raise HTTPException(status_code=status.HTTP_410_GONE, detail="Este convite expirou.")

    org_id = int(inv["org_id"])
    atual = conn.execute(
        "SELECT org_id FROM memberships WHERE user_id = ?", (actor_id,)
    ).fetchone()
    if atual is not None and int(atual["org_id"]) == org_id:
        conn.execute(
            "UPDATE org_invites SET accepted_at = ?, accepted_by = ? WHERE id = ?",
            (db.now_iso(), actor_id, inv["id"]),
        )
        return resolve_context(conn, actor_id)

    tem_dados = conn.execute(
        "SELECT COUNT(*) AS c FROM leads WHERE user_id = ?", (actor_id,)
    ).fetchone()["c"]
    if tem_dados:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Você já tem uma conta com dados. Fale com o suporte para juntar as contas.",
        )

    minha_org = conn.execute(
        "SELECT id FROM organizations WHERE owner_user_id = ?", (actor_id,)
    ).fetchone()
    if minha_org is None:

        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Você já faz parte de outra equipe. Saia dela antes de entrar em uma nova.",
        )
    outros = conn.execute(
        "SELECT COUNT(*) AS c FROM memberships WHERE org_id = ? AND user_id <> ?",
        (int(minha_org["id"]), actor_id),
    ).fetchone()["c"]
    if outros:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Você é dono de uma equipe com membros. Não dá para entrar em outra.",
        )

    conn.execute("DELETE FROM organizations WHERE id = ?", (int(minha_org["id"]),))
    conn.execute(
        "INSERT INTO memberships (org_id, user_id, role, created_at) VALUES (?, ?, ?, ?)",
        (org_id, actor_id, inv["role"], db.now_iso()),
    )
    conn.execute(
        "UPDATE org_invites SET accepted_at = ?, accepted_by = ? WHERE id = ?",
        (db.now_iso(), actor_id, inv["id"]),
    )
    return resolve_context(conn, actor_id)

def _membership(conn: db.Connection, org_id: int, user_id: int) -> db.Row | None:
    return conn.execute(
        "SELECT role FROM memberships WHERE org_id = ? AND user_id = ?", (org_id, user_id)
    ).fetchone()

def mudar_papel(
    conn: db.Connection, org_id: int, owner_user_id: int, alvo_id: int, novo_papel: str
) -> None:
    if novo_papel not in ROLES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Papel inválido.")
    if alvo_id == owner_user_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="O dono da conta é sempre Admin.",
        )
    if _membership(conn, org_id, alvo_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Membro não encontrado.")
    conn.execute(
        "UPDATE memberships SET role = ? WHERE org_id = ? AND user_id = ?",
        (novo_papel, org_id, alvo_id),
    )

def remover_membro(
    conn: db.Connection, org_id: int, tenant_id: int, owner_user_id: int, alvo_id: int
) -> None:
    if alvo_id == owner_user_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Não dá para remover o dono da conta.",
        )
    if _membership(conn, org_id, alvo_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Membro não encontrado.")

    conn.execute(
        "UPDATE leads SET owner_user_id = NULL WHERE user_id = ? AND owner_user_id = ?",
        (tenant_id, alvo_id),
    )
    conn.execute(
        "DELETE FROM memberships WHERE org_id = ? AND user_id = ?", (org_id, alvo_id)
    )
