from __future__ import annotations

from typing import Any

import db

CONVITE_CRIADO = "convite.criado"
CONVITE_REVOGADO = "convite.revogado"
MEMBRO_ENTROU = "membro.entrou"
PAPEL_MUDADO = "membro.papel"
MEMBRO_REMOVIDO = "membro.removido"
LEAD_ATRIBUIDO = "lead.atribuido"
LEAD_SEM_DONO = "lead.sem_dono"
FOTO_TROCADA = "perfil.foto_trocada"
FOTO_REMOVIDA = "perfil.foto_removida"
NOME_ALTERADO = "perfil.nome_alterado"
SENHA_ALTERADA = "seguranca.senha_alterada"
EMAIL_ALTERADO = "seguranca.email_alterado"
SESSOES_ENCERRADAS = "seguranca.sessoes_encerradas"

def log(
    conn: db.Connection,
    user: dict[str, Any],
    action: str,
    *,
    target_type: str = "",
    target_id: int | None = None,
    target_label: str = "",
    detail: str = "",
) -> None:
    conn.execute(
        """INSERT INTO audit_events
             (org_id, actor_user_id, actor_name, action,
              target_type, target_id, target_label, detail, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            int(user["org_id"]),
            int(user["actor_id"]),
            (user.get("name") or "").strip(),
            action,
            target_type,
            target_id,
            target_label,
            detail,
            db.now_iso(),
        ),
    )

def list_for_org(conn: db.Connection, org_id: int, limit: int = 50) -> list[dict[str, Any]]:
    limit = max(1, min(int(limit), 200))
    rows = conn.execute(
        """SELECT actor_name, action, target_type, target_id, target_label, detail, created_at
             FROM audit_events
            WHERE org_id = ?
            ORDER BY created_at DESC, id DESC
            LIMIT ?""",
        (org_id, limit),
    ).fetchall()
    return [
        {
            "actor_name": r["actor_name"] or "Alguém",
            "action": r["action"],
            "created_at": r["created_at"],
            "texto": descrever(r),
        }
        for r in rows
    ]

def descrever(row: Any) -> str:
    ator = (row["actor_name"] or "Alguém").strip() or "Alguém"
    acao = row["action"]
    alvo = (row["target_label"] or "").strip()
    detalhe = (row["detail"] or "").strip()

    if acao == CONVITE_CRIADO:
        quem = alvo or "por link"
        return f"{ator} criou um convite ({quem}) como {detalhe or 'membro'}."
    if acao == CONVITE_REVOGADO:
        return f"{ator} revogou um convite" + (f" ({alvo})." if alvo else ".")
    if acao == MEMBRO_ENTROU:
        return f"{ator} entrou na equipe como {detalhe or 'membro'}."
    if acao == PAPEL_MUDADO:
        return f"{ator} mudou o papel de {alvo or 'um membro'} para {detalhe or 'outro'}."
    if acao == MEMBRO_REMOVIDO:
        return f"{ator} removeu {alvo or 'um membro'} da equipe."
    if acao == LEAD_ATRIBUIDO:
        lead = f'"{alvo}"' if alvo else "um lead"
        return f"{ator} atribuiu o lead {lead} a {detalhe or 'um membro'}."
    if acao == FOTO_TROCADA:
        return f"{ator} atualizou a foto de perfil."
    if acao == FOTO_REMOVIDA:
        return f"{ator} removeu a foto de perfil."
    if acao == NOME_ALTERADO:
        return f"{ator} alterou o próprio nome" + (f" para {detalhe}." if detalhe else ".")
    if acao == SENHA_ALTERADA:
        return f"{ator} alterou a própria senha."
    if acao == EMAIL_ALTERADO:
        return f"{ator} alterou o e-mail de acesso."
    if acao == SESSOES_ENCERRADAS:
        return f"{ator} encerrou as outras sessões da conta."
    if acao == LEAD_SEM_DONO:
        lead = f'"{alvo}"' if alvo else "um lead"
        return f"{ator} deixou o lead {lead} sem dono."
    return f"{ator}: {acao}"
