from __future__ import annotations

import logging
from typing import Any

import db
import orgs

logger = logging.getLogger("vertex.activities")

MAX_TITLE = 160
MAX_DETAIL = 4000

def _corta(valor: Any, limite: int) -> str:
    texto = str(valor or "").strip()
    return texto if len(texto) <= limite else texto[: limite - 1] + "…"

def log(
    conn: db.Connection,
    user_id: int,
    *,
    kind: str,
    title: str,
    lead_id: int | None = None,
    detail: str = "",
    source: str = "user",
    ref_type: str = "",
    ref_id: int | None = None,
    due_at: str | None = None,
    done_at: str | None = None,
    created_at: str | None = None,
) -> int:
    if kind not in db.ACTIVITY_KINDS:
        raise ValueError(f"tipo de atividade desconhecido: {kind!r}")

    momento = created_at or db.now_iso()
    cur = conn.execute(
        """INSERT INTO activities
               (user_id, lead_id, kind, title, detail, source,
                ref_type, ref_id, due_at, done_at, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            user_id,
            lead_id,
            kind,
            _corta(title, MAX_TITLE),
            _corta(detail, MAX_DETAIL),
            source,
            ref_type,
            ref_id,
            due_at,
            done_at,
            momento,
        ),
    )

    if lead_id is not None and kind in db.CONTACT_KINDS:

        conn.execute(
            "UPDATE leads SET last_activity_at = ? WHERE id = ? AND user_id = ?",
            (momento, lead_id, user_id),
        )

    return int(cur.lastrowid)

def row_to_dict(row: db.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "lead_id": row["lead_id"],
        "kind": row["kind"],
        "title": row["title"],
        "detail": row["detail"],
        "source": row["source"],
        "ref_type": row["ref_type"],
        "ref_id": row["ref_id"],
        "due_at": row["due_at"],
        "done_at": row["done_at"],
        "created_at": row["created_at"],

        "pendente": bool(row["due_at"]) and not row["done_at"],
    }

def list_for_lead(conn: db.Connection, user_id: int, lead_id: int, limit: int = 200) -> list[dict]:
    linhas = conn.execute(
        """SELECT id, lead_id, kind, title, detail, source, ref_type, ref_id,
                  due_at, done_at, created_at
             FROM activities
            WHERE user_id = ? AND lead_id = ?
         ORDER BY created_at DESC, id DESC
            LIMIT ?""",
        (user_id, lead_id, limit),
    ).fetchall()
    return [row_to_dict(linha) for linha in linhas]

def pending_tasks(
    conn: db.Connection, user_id: int, limit: int = 100, owner_scope: int | None = None
) -> list[dict]:
    if owner_scope is None:
        vis, vp = "", []
    else:
        vis = " AND (a.lead_id IS NULL OR l.owner_user_id = ? OR l.owner_user_id IS NULL)"
        vp = [owner_scope]
    linhas = conn.execute(
        f"""SELECT a.id, a.lead_id, a.kind, a.title, a.detail, a.source,
                  a.ref_type, a.ref_id, a.due_at, a.done_at, a.created_at,
                  COALESCE(l.name, '') AS lead_name
             FROM activities a
        LEFT JOIN leads l ON l.id = a.lead_id AND l.user_id = a.user_id
            WHERE a.user_id = ? AND a.due_at IS NOT NULL AND a.done_at IS NULL{vis}
         ORDER BY a.due_at ASC
            LIMIT ?""",
        (user_id, *vp, limit),
    ).fetchall()
    saida = []
    for linha in linhas:
        item = row_to_dict(linha)
        item["lead_name"] = linha["lead_name"]
        saida.append(item)
    return saida

def _next_action_dict(row: db.Row, agora: str) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "kind": row["kind"],
        "title": row["title"],
        "due_at": row["due_at"],

        "atrasada": bool(row["due_at"]) and str(row["due_at"]) <= agora,
    }

def next_action_for_many(
    conn: db.Connection, user_id: int, lead_ids: list[int]
) -> dict[int, dict[str, Any]]:
    if not lead_ids:
        return {}
    marcadores = ",".join("?" * len(lead_ids))
    linhas = conn.execute(
        f"""SELECT id, lead_id, kind, title, due_at
              FROM activities
             WHERE user_id = ? AND lead_id IN ({marcadores})
               AND due_at IS NOT NULL AND done_at IS NULL
          ORDER BY due_at ASC, id ASC""",
        (user_id, *lead_ids),
    ).fetchall()
    agora = db.now_iso()
    saida: dict[int, dict[str, Any]] = {}
    for linha in linhas:
        lead_id = int(linha["lead_id"])

        if lead_id not in saida:
            saida[lead_id] = _next_action_dict(linha, agora)
    return saida

def next_action_for(conn: db.Connection, user_id: int, lead_id: int) -> dict[str, Any] | None:
    return next_action_for_many(conn, user_id, [lead_id]).get(lead_id)

def leads_without_next_action(
    conn: db.Connection, user_id: int, limit: int = 1000, owner_scope: int | None = None
) -> list[db.Row]:
    vis, vp = orgs.clausula_visibilidade(owner_scope, "l.owner_user_id")
    return conn.execute(
        f"""SELECT l.id, l.name, l.company, l.value, l.status, l.segment,
                  COALESCE(l.last_activity_at, l.created_at) AS ultimo_contato
             FROM leads l
            WHERE l.user_id = ? AND l.status NOT IN ('Ganho', 'Perdido'){vis}
              AND NOT EXISTS (
                    SELECT 1 FROM activities a
                     WHERE a.lead_id = l.id AND a.user_id = l.user_id
                       AND a.due_at IS NOT NULL AND a.done_at IS NULL
                  )
         ORDER BY l.value DESC, l.id DESC
            LIMIT ?""",
        (user_id, *vp, limit),
    ).fetchall()

def touch_last_activity(conn: db.Connection, user_id: int, lead_id: int, momento: str) -> None:
    conn.execute(
        "UPDATE leads SET last_activity_at = ? WHERE id = ? AND user_id = ?",
        (momento, lead_id, user_id),
    )

def notify(
    conn: db.Connection,
    user_id: int,
    *,
    type: str,
    title: str,
    body: str = "",
    severity: str = "info",
    ref_type: str = "",
    ref_id: int | None = None,
    dedup_key: str = "",
) -> int | None:
    cur = conn.execute(
        """INSERT OR IGNORE INTO notifications
               (user_id, type, title, body, severity, ref_type, ref_id, dedup_key, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            user_id,
            type,
            _corta(title, MAX_TITLE),
            _corta(body, MAX_DETAIL),
            severity,
            ref_type,
            ref_id,
            dedup_key,
            db.now_iso(),
        ),
    )
    if not cur.rowcount:
        return None
    return int(cur.lastrowid)

def unread_count(conn: db.Connection, user_id: int) -> int:
    linha = conn.execute(
        "SELECT COUNT(*) AS t FROM notifications WHERE user_id = ? AND read_at IS NULL",
        (user_id,),
    ).fetchone()
    return int(linha["t"])

def notification_to_dict(row: db.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "type": row["type"],
        "title": row["title"],
        "body": row["body"],
        "severity": row["severity"],
        "ref_type": row["ref_type"],
        "ref_id": row["ref_id"],
        "read_at": row["read_at"],
        "created_at": row["created_at"],
    }

def ensure_loss_reasons(conn: db.Connection, user_id: int) -> None:
    linha = conn.execute(
        "SELECT COUNT(*) AS t FROM loss_reasons WHERE user_id = ?", (user_id,)
    ).fetchone()
    if int(linha["t"]) > 0:
        return
    agora = db.now_iso()
    conn.executemany(
        """INSERT OR IGNORE INTO loss_reasons (user_id, label, position, active, created_at)
           VALUES (?, ?, ?, 1, ?)""",
        [(user_id, rotulo, pos, agora) for pos, rotulo in enumerate(db.DEFAULT_LOSS_REASONS)],
    )

def loss_reason_labels(conn: db.Connection, user_id: int, *, only_active: bool = True) -> list[str]:
    ensure_loss_reasons(conn, user_id)
    sql = "SELECT label FROM loss_reasons WHERE user_id = ?"
    if only_active:
        sql += " AND active = 1"
    sql += " ORDER BY position, id"
    return [linha["label"] for linha in conn.execute(sql, (user_id,))]
