from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import billing
import config
import db
import plans

_OPEN_PH = ", ".join("?" for _ in db.OPEN_STATUSES)

def _max_iso(*valores: Any) -> str | None:
    presentes = [str(v) for v in valores if v]
    return max(presentes) if presentes else None

def _estado(row: Any) -> dict[str, Any]:
    if row is None or row["status"] is None:
        return {
            "plano": plans.INICIAL,
            "status": "gratuito",
            "vigente": False,
            "em_trial": False,
            "centavos": 0,
            "current_period_end": None,
        }
    return billing.estado_efetivo(row)

def overview(conn: db.Connection) -> dict[str, Any]:
    agora = db.utcnow()
    mes_atual = db.now_iso()[:7]
    ha_30 = db.iso(agora - timedelta(days=30))
    ha_7 = db.iso(agora - timedelta(days=7))

    total_contas = conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"]
    novas_30d = conn.execute(
        "SELECT COUNT(*) AS c FROM users WHERE created_at > ?", (ha_30,)
    ).fetchone()["c"]

    subs = conn.execute(
        """SELECT s.plan, s.status, s.current_period_end, s.trial_ends_at,
                  s.modo, s.provider, s.cancel_at_period_end, s.centavos
             FROM users u
             LEFT JOIN subscriptions s ON s.user_id = u.id"""
    ).fetchall()

    por_status: dict[str, int] = {}
    por_plano: dict[str, int] = {}
    pagantes = 0
    em_trial = 0
    mrr = 0
    for row in subs:
        est = _estado(row)
        por_status[est["status"]] = por_status.get(est["status"], 0) + 1
        por_plano[est["plano"]] = por_plano.get(est["plano"], 0) + 1
        if est.get("em_trial"):
            em_trial += 1

        if est["vigente"] and est["plano"] != plans.INICIAL:
            pagantes += 1
            mrr += int(est.get("centavos") or 0)

    rec_total = conn.execute(
        "SELECT COALESCE(SUM(centavos), 0) AS s FROM invoices WHERE paid_at IS NOT NULL"
    ).fetchone()["s"]
    rec_mes = conn.execute(
        "SELECT COALESCE(SUM(centavos), 0) AS s FROM invoices "
        "WHERE paid_at IS NOT NULL AND substr(paid_at, 1, 7) = ?",
        (mes_atual,),
    ).fetchone()["s"]

    total_leads = conn.execute("SELECT COUNT(*) AS c FROM leads").fetchone()["c"]

    ia = conn.execute(
        "SELECT COUNT(*) AS c, COALESCE(SUM(tokens_in + tokens_out), 0) AS t "
        "FROM ai_usage WHERE created_at > ?",
        (ha_30,),
    ).fetchone()

    pedidos = conn.execute("SELECT COUNT(*) AS c FROM plan_interests").fetchone()["c"]
    pedidos_30d = conn.execute(
        "SELECT COUNT(*) AS c FROM plan_interests WHERE created_at > ?", (ha_30,)
    ).fetchone()["c"]

    ativas_30d = conn.execute(
        """SELECT COUNT(*) AS c FROM users u WHERE
               EXISTS (SELECT 1 FROM activities a WHERE a.user_id = u.id AND a.created_at > ?)
            OR EXISTS (SELECT 1 FROM leads l     WHERE l.user_id = u.id AND l.updated_at > ?)
            OR EXISTS (SELECT 1 FROM sessions se WHERE se.user_id = u.id AND se.created_at > ?)""",
        (ha_30, ha_30, ha_30),
    ).fetchone()["c"]

    alertas_7d = conn.execute(
        "SELECT COUNT(*) AS c FROM security_events WHERE created_at > ?", (ha_7,)
    ).fetchone()["c"]

    return {
        "total_contas": int(total_contas),
        "novas_30d": int(novas_30d),
        "ativas_30d": int(ativas_30d),
        "pagantes": int(pagantes),
        "em_trial": int(em_trial),
        "mrr_centavos": int(mrr),
        "receita_mes_centavos": int(rec_mes),
        "receita_total_centavos": int(rec_total),
        "total_leads": int(total_leads),
        "ia_chamadas_30d": int(ia["c"]),
        "ia_tokens_30d": int(ia["t"]),
        "pedidos_plano": int(pedidos),
        "pedidos_plano_30d": int(pedidos_30d),
        "alertas_seguranca_7d": int(alertas_7d),
        "por_status": por_status,
        "por_plano": por_plano,
    }

def security_events(conn: db.Connection, limit: int = 50) -> dict[str, Any]:
    total = conn.execute("SELECT COUNT(*) AS c FROM security_events").fetchone()["c"]
    linhas = conn.execute(
        "SELECT id, kind, path, ip, user_agent, detail, created_at "
        "FROM security_events ORDER BY created_at DESC LIMIT ?",
        (limit,),
    ).fetchall()
    items = [
        {
            "id": int(r["id"]),
            "kind": r["kind"],
            "path": r["path"] or "",
            "ip": r["ip"] or "",
            "user_agent": r["user_agent"] or "",
            "detail": r["detail"] or "",
            "created_at": r["created_at"],
        }
        for r in linhas
    ]
    return {"total": int(total), "items": items}

_BACKUP_OK_H = 26
_BACKUP_ALARME_H = 50

def saude(conn: db.Connection) -> dict[str, Any]:
    agora = db.utcnow()

    backup: dict[str, Any] = {
        "estado": "desconhecido", "ultimo_em": None, "horas": None,
        "tamanho": None, "arquivos": 0,
    }
    try:
        raiz = Path(os.environ.get("VERTEX_BACKUP_DIR", "/var/backups/vertex-crm"))
        arquivos = list(raiz.glob("*/vertex-*.db.gz"))
        if arquivos:
            mais_novo = max(arquivos, key=lambda p: p.stat().st_mtime)
            st = mais_novo.stat()
            idade_h = (agora.timestamp() - st.st_mtime) / 3600
            backup.update(
                arquivos=len(arquivos),
                ultimo_em=datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat(),
                horas=round(idade_h, 1),
                tamanho=int(st.st_size),
                estado=(
                    "ok" if idade_h <= _BACKUP_OK_H
                    else "atrasado" if idade_h <= _BACKUP_ALARME_H
                    else "critico"
                ),
            )
        else:
            backup["estado"] = "sem_backup"
    except OSError:
        pass

    alertas: list[str] = []
    try:
        log = Path(db.db_path()).resolve().parent / "alertas.log"
        if log.is_file():
            linhas = [ln for ln in log.read_text("utf-8", "replace").splitlines() if ln.strip()]
            alertas = linhas[-8:]
    except OSError:
        pass

    total_contas = conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"]
    total_leads = conn.execute("SELECT COUNT(*) AS c FROM leads").fetchone()["c"]
    ha_7 = db.iso(agora - timedelta(days=7))
    alertas_seg_7d = conn.execute(
        "SELECT COUNT(*) AS c FROM security_events WHERE created_at > ?", (ha_7,)
    ).fetchone()["c"]

    problemas: list[str] = []
    if backup["estado"] == "critico":
        problemas.append("Backup sem rodar há mais de 50h.")
    elif backup["estado"] == "sem_backup":
        problemas.append("Nenhum backup encontrado.")
    elif backup["estado"] == "atrasado":
        problemas.append("Último backup atrasado (mais de 26h).")
    if any(("FALHA" in a) or ("não consegui" in a) for a in alertas):
        problemas.append("Há registro de falha no log de alertas.")

    if backup["estado"] in {"critico", "sem_backup"}:
        estado = "critico"
    elif problemas:
        estado = "atencao"
    else:
        estado = "ok"

    return {
        "estado": estado,
        "gerado_em": db.iso(agora),
        "problemas": problemas,
        "backup": backup,
        "alertas_recentes": alertas,
        "uso": {
            "contas": int(total_contas),
            "leads": int(total_leads),
            "alertas_seguranca_7d": int(alertas_seg_7d),
        },
    }

def _conta_row(row: Any) -> dict[str, Any]:
    est = _estado(row)
    return {
        "id": int(row["id"]),
        "name": row["name"],
        "email": row["email"],
        "created_at": row["created_at"],
        "auth_provider": row["auth_provider"] or "password",
        "plano": est["plano"],
        "status": est["status"],
        "vigente": bool(est["vigente"]),
        "em_trial": bool(est.get("em_trial")),
        "centavos": int(est.get("centavos") or 0),
        "n_leads": int(row["n_leads"] or 0),
        "n_abertos": int(row["n_abertos"] or 0),
        "pipeline": float(row["pipeline"] or 0),
        "ia_chamadas": int(row["ia_calls"] or 0),
        "ultimo_visto": _max_iso(row["ult_ativ"], row["ult_sessao"], row["ult_lead"]),
        "is_owner": config.is_owner(row["email"]),
    }

def accounts(conn: db.Connection, q: str = "", limit: int = 50, offset: int = 0) -> dict[str, Any]:
    termo = (q or "").strip()
    like = f"%{db.deburr(termo)}%"

    total = conn.execute(
        "SELECT COUNT(*) AS c FROM users u "
        "WHERE (? = '' OR unaccent(u.name) LIKE ? OR unaccent(u.email) LIKE ?)",
        (termo, like, like),
    ).fetchone()["c"]

    sql = f"""
        SELECT u.id, u.name, u.email, u.created_at, u.auth_provider,
               s.plan, s.status, s.current_period_end, s.trial_ends_at,
               s.modo, s.provider, s.cancel_at_period_end, s.centavos,
               (SELECT COUNT(*) FROM leads l WHERE l.user_id = u.id) AS n_leads,
               (SELECT COUNT(*) FROM leads l WHERE l.user_id = u.id
                       AND l.status IN ({_OPEN_PH})) AS n_abertos,
               (SELECT COALESCE(SUM(value), 0) FROM leads l WHERE l.user_id = u.id
                       AND l.status IN ({_OPEN_PH})) AS pipeline,
               (SELECT MAX(created_at) FROM activities a WHERE a.user_id = u.id) AS ult_ativ,
               (SELECT MAX(created_at) FROM sessions se WHERE se.user_id = u.id) AS ult_sessao,
               (SELECT MAX(updated_at) FROM leads l WHERE l.user_id = u.id) AS ult_lead,
               (SELECT COUNT(*) FROM ai_usage ai WHERE ai.user_id = u.id) AS ia_calls
          FROM users u
          LEFT JOIN subscriptions s ON s.user_id = u.id
         WHERE (? = '' OR unaccent(u.name) LIKE ? OR unaccent(u.email) LIKE ?)
         ORDER BY u.created_at DESC
         LIMIT ? OFFSET ?
    """
    params: list[Any] = [
        *db.OPEN_STATUSES,
        *db.OPEN_STATUSES,
        termo, like, like,
        limit, offset,
    ]
    linhas = conn.execute(sql, params).fetchall()
    return {"total": int(total), "items": [_conta_row(r) for r in linhas]}

def account_detail(conn: db.Connection, user_id: int) -> dict[str, Any] | None:
    u = conn.execute(
        "SELECT id, name, email, created_at, auth_provider, email_verified "
        "FROM users WHERE id = ?",
        (user_id,),
    ).fetchone()
    if u is None:
        return None

    sub = conn.execute(
        "SELECT plan, status, current_period_end, trial_ends_at, modo, provider, "
        "cancel_at_period_end, centavos FROM subscriptions WHERE user_id = ?",
        (user_id,),
    ).fetchone()
    est = _estado(sub)

    por_status = [
        {"status": r["status"], "total": int(r["c"]), "valor": float(r["v"] or 0)}
        for r in conn.execute(
            "SELECT status, COUNT(*) AS c, COALESCE(SUM(value), 0) AS v "
            "FROM leads WHERE user_id = ? GROUP BY status",
            (user_id,),
        ).fetchall()
    ]
    n_leads = sum(item["total"] for item in por_status)
    pipeline = sum(
        item["valor"] for item in por_status if item["status"] in db.OPEN_STATUSES
    )
    ganho_total = sum(item["valor"] for item in por_status if item["status"] == "Ganho")

    ia = conn.execute(
        "SELECT COUNT(*) AS c, COALESCE(SUM(tokens_in + tokens_out), 0) AS t "
        "FROM ai_usage WHERE user_id = ?",
        (user_id,),
    ).fetchone()

    ha_30 = db.iso(db.utcnow() - timedelta(days=30))
    ativ_30 = conn.execute(
        "SELECT COUNT(*) AS c FROM activities WHERE user_id = ? AND created_at > ?",
        (user_id, ha_30),
    ).fetchone()["c"]

    ult_ativ = conn.execute(
        "SELECT MAX(created_at) AS m FROM activities WHERE user_id = ?", (user_id,)
    ).fetchone()["m"]
    ult_sessao = conn.execute(
        "SELECT MAX(created_at) AS m FROM sessions WHERE user_id = ?", (user_id,)
    ).fetchone()["m"]
    ult_lead = conn.execute(
        "SELECT MAX(updated_at) AS m FROM leads WHERE user_id = ?", (user_id,)
    ).fetchone()["m"]

    faturas = [
        {
            "provider": r["provider"],
            "plan": r["plan"],
            "centavos": int(r["centavos"] or 0),
            "currency": r["currency"] or "BRL",
            "status": r["status"],
            "metodo": r["metodo"] or "",
            "periodo_ate": r["periodo_ate"],
            "paid_at": r["paid_at"],
            "created_at": r["created_at"],
        }
        for r in conn.execute(
            "SELECT provider, plan, centavos, currency, status, metodo, periodo_ate, "
            "paid_at, created_at FROM invoices WHERE user_id = ? ORDER BY created_at DESC LIMIT 24",
            (user_id,),
        ).fetchall()
    ]

    return {
        "id": int(u["id"]),
        "name": u["name"],
        "email": u["email"],
        "created_at": u["created_at"],
        "auth_provider": u["auth_provider"] or "password",
        "email_verified": bool(u["email_verified"]),
        "plano": est["plano"],
        "status": est["status"],
        "vigente": bool(est["vigente"]),
        "em_trial": bool(est.get("em_trial")),
        "centavos": int(est.get("centavos") or 0),
        "current_period_end": est.get("current_period_end"),
        "n_leads": int(n_leads),
        "pipeline": float(pipeline),
        "ganho_total": float(ganho_total),
        "por_status": por_status,
        "ia_chamadas": int(ia["c"]),
        "ia_tokens": int(ia["t"]),
        "atividades_30d": int(ativ_30),
        "ultimo_visto": _max_iso(ult_ativ, ult_sessao, ult_lead),
        "faturas": faturas,
        "is_owner": config.is_owner(u["email"]),
    }

def plan_interests(conn: db.Connection, limit: int = 100) -> dict[str, Any]:
    total = conn.execute("SELECT COUNT(*) AS c FROM plan_interests").fetchone()["c"]
    linhas = conn.execute(
        """SELECT pi.id, pi.plan, pi.name, pi.email, pi.company, pi.phone,
                  pi.seats, pi.message, pi.created_at, u.email AS conta_email
             FROM plan_interests pi
             LEFT JOIN users u ON u.id = pi.user_id
            ORDER BY pi.created_at DESC
            LIMIT ?""",
        (limit,),
    ).fetchall()
    items = [
        {
            "id": int(r["id"]),
            "plan": r["plan"],
            "name": r["name"],
            "email": r["email"],
            "company": r["company"] or "",
            "phone": r["phone"] or "",
            "seats": int(r["seats"] or 1),
            "message": r["message"] or "",
            "created_at": r["created_at"],
            "conta_email": r["conta_email"],
        }
        for r in linhas
    ]
    return {"total": int(total), "items": items}

def revenue_series(conn: db.Connection, months: int = 12) -> dict[str, Any]:
    linhas = conn.execute(
        """SELECT substr(paid_at, 1, 7) AS mes,
                  COALESCE(SUM(centavos), 0) AS centavos,
                  COUNT(*) AS n
             FROM invoices
            WHERE paid_at IS NOT NULL
            GROUP BY mes
            ORDER BY mes DESC
            LIMIT ?""",
        (months,),
    ).fetchall()

    pontos = [
        {"mes": r["mes"], "centavos": int(r["centavos"] or 0), "faturas": int(r["n"] or 0)}
        for r in reversed(linhas)
    ]
    total = sum(p["centavos"] for p in pontos)
    return {"total_centavos": int(total), "points": pontos}
