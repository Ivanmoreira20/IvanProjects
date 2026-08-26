from __future__ import annotations

from typing import Any

import db
import orgs

PASSOS: tuple[dict[str, str], ...] = (
    {
        "id": "lead",
        "titulo": "Cadastre o primeiro cliente",
        "porque": "Um CRM começa por quem você atende. Nome e contato já bastam — o resto entra depois.",
        "acao": "Novo lead",
        "rota": "leads",
    },
    {
        "id": "valor",
        "titulo": "Diga quanto a oportunidade vale",
        "porque": "É o valor que transforma uma lista de contatos num funil com dinheiro dentro.",
        "acao": "Abrir o negócio",
        "rota": "leads",
    },
    {
        "id": "etapa",
        "titulo": "Mova o negócio no funil",
        "porque": "Arrastar o card de uma etapa para a outra é o registro de que a conversa andou — e é dele que saem a conversão, a previsão e os avisos.",
        "acao": "Ir para o funil",
        "rota": "negocios",
    },
    {
        "id": "contato",
        "titulo": "Registre uma conversa com o cliente",
        "porque": "Anote a ligação, a reunião ou o e-mail que aconteceu — não o número dele, mas o registro de que vocês falaram. É esse histórico que mostra há quanto tempo o cliente está sem resposta.",
        "acao": "Abrir o negócio",
        "rota": "leads",
    },
    {
        "id": "proxima",
        "titulo": "Marque a próxima ação (com data)",
        "porque": "Agende o próximo passo com uma data — dentro do negócio, no histórico. É assim que nenhuma oportunidade fica esperando, e o Vertex avisa quando a data chega.",
        "acao": "Abrir o negócio",
        "rota": "leads",
    },
    {
        "id": "fechou",
        "titulo": "Feche o primeiro negócio",
        "porque": "Ganho ou perdido, é o fechamento que produz a conversão e o motivo de perda — os dois números que mostram onde melhorar.",
        "acao": "Ir para o funil",
        "rota": "negocios",
    },
)

def _tem(conn: db.Connection, sql: str, params: tuple[Any, ...]) -> bool:
    return conn.execute(sql, params).fetchone() is not None

def calcular(conn: db.Connection, user: dict[str, Any]) -> dict[str, Any]:
    tenant = int(user["id"])
    escopo = orgs.escopo_owner(user)
    vis, vp = orgs.clausula_visibilidade(escopo)

    vis_l, vp_l = orgs.clausula_visibilidade(escopo, "l.owner_user_id")

    total_leads = int(
        conn.execute(
            f"SELECT COUNT(*) FROM leads WHERE user_id = ?{vis}", (tenant, *vp)
        ).fetchone()[0]
    )

    tem_valor = _tem(
        conn,
        f"SELECT 1 FROM leads WHERE user_id = ? AND value > 0{vis} LIMIT 1",
        (tenant, *vp),
    )

    tem_etapa = _tem(
        conn,
        f"""SELECT 1 FROM stage_events s
             JOIN leads l ON l.id = s.lead_id AND l.user_id = s.user_id
            WHERE s.user_id = ?
              {vis_l}
            LIMIT 1""",
        (tenant, *vp_l),
    )

    marcas = ",".join("?" for _ in db.CONTACT_KINDS)
    tem_contato = _tem(
        conn,
        f"""SELECT 1 FROM activities a
             JOIN leads l ON l.id = a.lead_id AND l.user_id = a.user_id
            WHERE a.user_id = ? AND a.kind IN ({marcas}) AND a.source = 'user'
              {vis_l}
            LIMIT 1""",
        (tenant, *sorted(db.CONTACT_KINDS), *vp_l),
    )

    tem_proxima = _tem(
        conn,
        f"""SELECT 1 FROM activities a
             JOIN leads l ON l.id = a.lead_id AND l.user_id = a.user_id
            WHERE a.user_id = ? AND a.due_at IS NOT NULL
              {vis_l}
            LIMIT 1""",
        (tenant, *vp_l),
    )

    fechou = _tem(
        conn,
        f"""SELECT 1 FROM leads
             WHERE user_id = ? AND status IN ('Ganho', 'Perdido'){vis} LIMIT 1""",
        (tenant, *vp),
    )

    feitos = {
        "lead": total_leads > 0,
        "valor": tem_valor,
        "etapa": tem_etapa,
        "contato": tem_contato,
        "proxima": tem_proxima,
        "fechou": fechou,
    }

    passos = [
        {
            **passo,
            "feito": bool(feitos[passo["id"]]),
        }
        for passo in PASSOS
    ]
    concluidos = sum(1 for p in passos if p["feito"])

    foco = conn.execute(
        f"SELECT id FROM leads WHERE user_id = ?{vis} ORDER BY id DESC LIMIT 1",
        (tenant, *vp),
    ).fetchone()

    tem_lead_real = _tem(
        conn,
        f"""SELECT 1 FROM leads
             WHERE user_id = ? AND value > 0
               AND (COALESCE(phone, '') <> '' OR COALESCE(whatsapp, '') <> ''
                    OR COALESCE(email, '') <> '')
               {vis}
             LIMIT 1""",
        (tenant, *vp),
    )

    return {
        "passos": passos,
        "total": len(passos),
        "concluidos": concluidos,
        "completo": concluidos == len(passos),
        "leads": total_leads,
        "dispensado": dispensado(conn, user),
        "foco_lead_id": int(foco["id"]) if foco else None,
        "oculto_auto": tem_lead_real,
    }

def dispensado(conn: db.Connection, user: dict[str, Any]) -> bool:
    linha = conn.execute(
        "SELECT onboarding_off FROM users WHERE id = ?", (int(user["actor_id"]),)
    ).fetchone()
    return bool(linha and linha["onboarding_off"])

def dispensar(conn: db.Connection, user: dict[str, Any], valor: bool = True) -> None:
    conn.execute(
        "UPDATE users SET onboarding_off = ? WHERE id = ?",
        (1 if valor else 0, int(user["actor_id"])),
    )
