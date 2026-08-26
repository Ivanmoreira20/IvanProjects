from __future__ import annotations

from typing import Any

from fastapi import HTTPException, status

import avatars
import db

def _linhas(conn: db.Connection, sql: str, params: tuple) -> list[dict[str, Any]]:
    return [dict(linha) for linha in conn.execute(sql, params).fetchall()]

def exportar(conn: db.Connection, user_id: int) -> dict[str, Any]:
    usuario = conn.execute(
        "SELECT name, email, created_at, email_verified, auth_provider FROM users WHERE id = ?",
        (user_id,),
    ).fetchone()
    if usuario is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conta não encontrada")

    return {
        "gerado_em": db.now_iso(),
        "aviso": (
            "Exportação dos seus dados no Vertex CRM (LGPD). Não inclui senha "
            "nem segredos do sistema."
        ),
        "perfil": {
            "nome": usuario["name"],
            "email": usuario["email"],
            "criada_em": usuario["created_at"],
            "email_verificado": bool(usuario["email_verified"]),
            "login": usuario["auth_provider"],
        },
        "leads": _linhas(conn, "SELECT * FROM leads WHERE user_id = ? ORDER BY id", (user_id,)),
        "atividades": _linhas(conn, "SELECT * FROM activities WHERE user_id = ? ORDER BY id", (user_id,)),
        "propostas": _linhas(conn, "SELECT * FROM proposals WHERE user_id = ? ORDER BY id", (user_id,)),
        "itens_de_proposta": _linhas(
            conn,
            "SELECT pi.* FROM proposal_items pi JOIN proposals p ON p.id = pi.proposal_id "
            "WHERE p.user_id = ? ORDER BY pi.id",
            (user_id,),
        ),
        "historico_de_valor": _linhas(conn, "SELECT * FROM deal_value_events WHERE user_id = ? ORDER BY id", (user_id,)),
        "campos_personalizados": _linhas(conn, "SELECT * FROM custom_fields WHERE user_id = ? ORDER BY id", (user_id,)),
        "valores_personalizados": _linhas(conn, "SELECT * FROM custom_values WHERE user_id = ? ORDER BY entity_id, field_id", (user_id,)),
        "automacoes": _linhas(conn, "SELECT * FROM automations WHERE user_id = ? ORDER BY id", (user_id,)),
        "notificacoes": _linhas(conn, "SELECT * FROM notifications WHERE user_id = ? ORDER BY id", (user_id,)),
        "motivos_de_perda": _linhas(conn, "SELECT * FROM loss_reasons WHERE user_id = ? ORDER BY id", (user_id,)),
        "mudancas_de_etapa": _linhas(conn, "SELECT * FROM stage_events WHERE user_id = ? ORDER BY id", (user_id,)),
        "whatsapp_config": _linhas(conn, "SELECT * FROM wa_config WHERE user_id = ?", (user_id,)),
        "whatsapp_mensagens": _linhas(conn, "SELECT * FROM wa_messages WHERE user_id = ? ORDER BY id", (user_id,)),
        "faturas": _linhas(conn, "SELECT * FROM invoices WHERE user_id = ? ORDER BY id", (user_id,)),
        "pedidos_de_plano": _linhas(conn, "SELECT * FROM plan_interests WHERE user_id = ? ORDER BY id", (user_id,)),
    }

def excluir(conn: db.Connection, user_id: int) -> None:
    conn.execute("DELETE FROM plan_interests WHERE user_id = ?", (user_id,))
    conn.execute("DELETE FROM users WHERE id = ?", (user_id,))

    avatars.remover_tudo(user_id)
