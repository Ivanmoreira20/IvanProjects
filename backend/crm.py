from __future__ import annotations

from typing import Any

from fastapi import HTTPException, status

import activities
import db
import intel
import orgs

LEAD_FIELDS = (
    "id", "name", "company", "value", "status", "segment",
    "email", "phone", "whatsapp", "source", "notes", "tags", "owner",
    "owner_user_id",
    "lost_reason", "lost_note", "closed_at", "last_activity_at",
    "stage_changed_at", "score", "score_band", "created_at", "updated_at",
)

_SELECT_LEAD = f"SELECT {', '.join(LEAD_FIELDS)} FROM leads"

def lead_to_dict(row: db.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "company": row["company"],
        "value": float(row["value"]),
        "status": row["status"],
        "segment": row["segment"],
        "email": row["email"],
        "phone": row["phone"],
        "whatsapp": row["whatsapp"],
        "source": row["source"],
        "notes": row["notes"],
        "tags": [str(t) for t in db.json_load(row["tags"], [])],
        "owner": row["owner"],

        "owner_user_id": row["owner_user_id"],
        "lost_reason": row["lost_reason"],
        "lost_note": row["lost_note"],
        "closed_at": row["closed_at"],
        "last_activity_at": row["last_activity_at"],
        "stage_changed_at": row["stage_changed_at"],

        "score": row["score"],
        "score_band": row["score_band"] or "",
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }

def fetch_lead(
    conn: db.Connection, lead_id: int, user_id: int, owner_scope: int | None = None
) -> db.Row:
    vis, vparams = orgs.clausula_visibilidade(owner_scope)
    row = conn.execute(
        f"{_SELECT_LEAD} WHERE id = ? AND user_id = ?{vis}",
        (lead_id, user_id, *vparams),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lead não encontrado")
    return row

TETO_LISTA = 5_000

TETO_LEADS_CONTA = 50_000

def contar_leads(
    conn: db.Connection, user_id: int, owner_scope: int | None = None
) -> int:
    vis, vparams = orgs.clausula_visibilidade(owner_scope)
    return int(
        conn.execute(
            f"SELECT COUNT(*) FROM leads WHERE user_id = ?{vis}", (user_id, *vparams)
        ).fetchone()[0]
    )

def contar_leads_da_conta(conn: db.Connection, user_id: int) -> int:
    return int(
        conn.execute(
            "SELECT COUNT(*) FROM leads WHERE user_id = ?", (user_id,)
        ).fetchone()[0]
    )

def espaco_para_leads(conn: db.Connection, user_id: int, quantos: int = 1) -> int:
    usados = contar_leads_da_conta(conn, user_id)
    return max(0, TETO_LEADS_CONTA - usados)

def list_leads(
    conn: db.Connection,
    user_id: int,
    owner_scope: int | None = None,
    limite: int = TETO_LISTA,
) -> list[db.Row]:
    vis, vparams = orgs.clausula_visibilidade(owner_scope)

    teto = max(1, min(int(limite), TETO_LISTA))
    return conn.execute(
        f"{_SELECT_LEAD} WHERE user_id = ?{vis} ORDER BY created_at DESC, id DESC LIMIT ?",
        (user_id, *vparams, teto),
    ).fetchall()

def full_row(conn: db.Connection, lead_id: int, user_id: int) -> dict | None:
    linha = conn.execute(
        "SELECT * FROM leads WHERE id = ? AND user_id = ?", (lead_id, user_id)
    ).fetchone()
    return dict(linha) if linha else None

class LossReasonRequired(Exception):

    def __init__(self, opcoes: list[str]) -> None:
        super().__init__("Informe o motivo da perda")
        self.opcoes = opcoes

def change_status(
    conn: db.Connection,
    user_id: int,
    lead_id: int,
    novo: str,
    *,
    lost_reason: str = "",
    lost_note: str = "",
    origem: str = "user",
    detalhe: str = "",
) -> list[tuple[str, dict]]:
    atual = conn.execute(
        "SELECT status, name, value, stage_changed_at, created_at "
        "FROM leads WHERE id = ? AND user_id = ?",
        (lead_id, user_id),
    ).fetchone()
    if atual is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lead não encontrado")

    anterior = atual["status"]

    if anterior == novo:

        if novo == "Perdido" and (lost_reason or "").strip():
            motivo = lost_reason.strip()
            opcoes = activities.loss_reason_labels(conn, user_id)
            if motivo not in opcoes:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Motivo de perda desconhecido: {motivo}",
                )
            anterior_motivo = conn.execute(
                "SELECT lost_reason FROM leads WHERE id = ? AND user_id = ?", (lead_id, user_id)
            ).fetchone()["lost_reason"]
            if anterior_motivo != motivo:
                conn.execute(
                    "UPDATE leads SET lost_reason = ?, lost_note = ?, updated_at = ? "
                    "WHERE id = ? AND user_id = ?",
                    (motivo, (lost_note or "").strip()[:500], db.now_iso(), lead_id, user_id),
                )
                activities.log(
                    conn, user_id, lead_id=lead_id, kind="perda",
                    title=f"Motivo da perda corrigido: {anterior_motivo or '—'} → {motivo}",
                    detail=lost_note or "", source=origem,
                )
        return []

    if novo == "Perdido":
        motivo = (lost_reason or "").strip()
        opcoes = activities.loss_reason_labels(conn, user_id)
        if not motivo:
            raise LossReasonRequired(opcoes)
        if motivo not in opcoes:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Motivo de perda desconhecido: {motivo}",
            )
    else:
        motivo = ""
        lost_note = ""

    agora = db.now_iso()
    fechado = agora if novo in db.CLOSED_STATUSES else None
    conn.execute(
        """UPDATE leads
              SET status = ?, updated_at = ?, stage_changed_at = ?, closed_at = ?,
                  lost_reason = ?, lost_note = ?
            WHERE id = ? AND user_id = ?""",
        (novo, agora, agora, fechado, motivo, (lost_note or "").strip()[:500], lead_id, user_id),
    )

    if novo == "Perdido":
        titulo = f"Negócio perdido — {motivo}"
        tipo = "perda"
    elif novo == "Ganho":
        titulo = "Negócio ganho"
        tipo = "ganho"
    else:
        titulo = f"{anterior} → {novo}"
        tipo = "etapa"

    activities.log(
        conn, user_id, lead_id=lead_id, kind=tipo, title=titulo,
        detail=detalhe or (lost_note or ""), source=origem,
    )

    inicio_da_etapa = atual["stage_changed_at"] or atual["created_at"]
    anterior_em = db.try_parse_iso(inicio_da_etapa)
    dias_na_etapa = max(0, (db.utcnow() - anterior_em).days) if anterior_em else 0
    conn.execute(
        "INSERT INTO stage_events (user_id, lead_id, de, para, dias_na_etapa, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (user_id, lead_id, anterior, novo, dias_na_etapa, agora),
    )

    if novo == "Ganho":
        intel.marcar(conn, user_id, "primeiro_ganho")

    eventos: list[tuple[str, dict]] = [("lead.etapa", {"de": anterior, "para": novo})]
    if novo == "Ganho":
        eventos.append(("lead.ganho", {}))
        activities.notify(
            conn, user_id, type="ganho", title=f"Negócio ganho: {atual['name']}",
            body=f"Valor: R$ {float(atual['value']):,.2f}".replace(",", "@").replace(".", ",").replace("@", "."),
            severity="sucesso", ref_type="lead", ref_id=lead_id,
            dedup_key=f"ganho:{lead_id}",
        )
    elif novo == "Perdido":
        eventos.append(("lead.perdido", {"motivo": motivo}))
        activities.notify(
            conn, user_id, type="perda", title=f"Negócio perdido: {atual['name']}",
            body=f"Motivo: {motivo}", severity="alerta",
            ref_type="lead", ref_id=lead_id, dedup_key=f"perda:{lead_id}",
        )
    return eventos

def dispatch_events(
    conn: db.Connection, user_id: int, lead_id: int, eventos: list[tuple[str, dict]]
) -> None:
    if not eventos:
        return
    import automations

    lead = full_row(conn, lead_id, user_id)
    if lead is None:
        return
    for evento, _dados in eventos:
        try:
            automations.dispatch(conn, user_id, evento, lead=lead)
        except Exception:  # noqa: BLE001
            import logging

            logging.getLogger("vertex.crm").exception(
                "Falha ao disparar automações do evento %s", evento
            )

def _brl(valor: float) -> str:
    return f"R$ {float(valor):,.2f}".replace(",", "@").replace(".", ",").replace("@", ".")

def log_value_change(
    conn: db.Connection, user_id: int, lead_id: int, de: float, para: float,
    *, note: str = "", origem: str = "system",
) -> None:
    de, para = float(de), float(para)
    if de == para:
        return
    agora = db.now_iso()
    conn.execute(
        "INSERT INTO deal_value_events (user_id, lead_id, de, para, note, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (user_id, lead_id, de, para, (note or "").strip()[:500], agora),
    )
    subiu = para > de
    delta = abs(para - de)
    detalhe = f"{'↑' if subiu else '↓'} {_brl(delta)}"
    if note and note.strip():
        detalhe += f" · {note.strip()}"

    activities.log(
        conn, user_id, lead_id=lead_id, kind="nota", source="system",
        title=f"Valor {'aumentou' if subiu else 'reduziu'}: {_brl(de)} → {_brl(para)}",
        detail=detalhe, created_at=agora,
    )

def negociacao(conn: db.Connection, user_id: int, lead_id: int) -> dict[str, Any]:
    lead = conn.execute(
        "SELECT value FROM leads WHERE id = ? AND user_id = ?", (lead_id, user_id)
    ).fetchone()
    if lead is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lead não encontrado")
    atual = float(lead["value"])

    eventos = conn.execute(
        "SELECT de, para, note, created_at FROM deal_value_events "
        "WHERE lead_id = ? AND user_id = ? ORDER BY created_at ASC, id ASC",
        (lead_id, user_id),
    ).fetchall()

    inicial = float(eventos[0]["de"]) if eventos else atual
    variacao = round(atual - inicial, 2)
    return {
        "valor_inicial": round(inicial, 2),
        "valor_atual": round(atual, 2),
        "variacao": variacao,
        "variacao_pct": round(variacao / inicial * 100, 1) if inicial else 0.0,
        "eventos": [
            {
                "de": round(float(e["de"]), 2),
                "para": round(float(e["para"]), 2),
                "note": e["note"],
                "created_at": e["created_at"],
            }
            for e in eventos
        ],
    }
