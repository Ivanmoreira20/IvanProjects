from __future__ import annotations

import html as _html
import json
import logging
import re
import secrets
from html.parser import HTMLParser
from typing import Any

import config
import db
import mailer

logger = logging.getLogger("vertex.marketing")

MAX_TENTATIVAS = 3
_VARIAVEIS = ("nome", "empresa", "email")

def norm_email(valor: str) -> str:
    return (valor or "").strip().lower()

_TAGS_OK = frozenset({
    "p", "br", "hr", "a", "strong", "b", "em", "i", "u", "s", "small",
    "h1", "h2", "h3", "h4", "ul", "ol", "li", "blockquote", "div", "span",
    "img", "table", "thead", "tbody", "tr", "td", "th", "center", "font",
})
_TAGS_CONTEUDO_FORA = frozenset({
    "script", "style", "iframe", "object", "embed", "link", "meta",
    "head", "title", "noscript", "svg", "math", "base",
})
_VOID = frozenset({"br", "hr", "img"})
_ATTRS_OK = {
    "a": frozenset({"href", "title"}),
    "img": frozenset({"src", "alt", "width", "height"}),
    "font": frozenset({"color", "face"}),
    "td": frozenset({"align", "valign", "width", "colspan", "rowspan"}),
    "th": frozenset({"align", "valign", "width", "colspan", "rowspan"}),
    "table": frozenset({"width", "align", "cellpadding", "cellspacing", "border"}),
    "*": frozenset({"style"}),
}
_URL_OK = re.compile(r"^\s*(https?:|mailto:)", re.IGNORECASE)
_CSS_PERIGO = re.compile(r"(javascript:|expression|@import|url\s*\(|behavior\s*:|<|>)", re.IGNORECASE)

def _limpar_style(valor: str) -> str:
    if not valor or _CSS_PERIGO.search(valor):
        return ""
    return valor.strip()

def _url_segura(valor: str) -> str:
    return valor.strip() if valor and _URL_OK.match(valor) else ""

class _Sanitizador(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.saida: list[str] = []
        self._pular = 0

    def _attrs_ok(self, tag: str, attrs: list[tuple[str, str | None]]) -> str:
        permitidas = _ATTRS_OK.get(tag, frozenset()) | _ATTRS_OK["*"]
        partes: list[str] = []
        for nome, valor in attrs:
            nome = (nome or "").lower()
            valor = valor or ""
            if nome.startswith("on") or nome not in permitidas:
                continue
            if nome in ("href", "src"):
                valor = _url_segura(valor)
                if not valor:
                    continue
            elif nome == "style":
                valor = _limpar_style(valor)
                if not valor:
                    continue
            partes.append(f'{nome}="{_html.escape(valor, quote=True)}"')
        return (" " + " ".join(partes)) if partes else ""

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag in _TAGS_CONTEUDO_FORA:
            self._pular += 1
            return
        if self._pular or tag not in _TAGS_OK:
            return
        fecha = "/>" if tag in _VOID else ">"
        self.saida.append(f"<{tag}{self._attrs_ok(tag, attrs)}{fecha}")

    def handle_startendtag(self, tag, attrs):
        tag = tag.lower()
        if tag in _TAGS_CONTEUDO_FORA or self._pular or tag not in _TAGS_OK:
            return
        self.saida.append(f"<{tag}{self._attrs_ok(tag, attrs)}/>")

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in _TAGS_CONTEUDO_FORA:
            if self._pular:
                self._pular -= 1
            return
        if self._pular or tag not in _TAGS_OK or tag in _VOID:
            return
        self.saida.append(f"</{tag}>")

    def handle_data(self, data):
        if not self._pular:
            self.saida.append(_html.escape(data, quote=False))

def sanitizar(html_in: str) -> str:
    if not html_in:
        return ""
    p = _Sanitizador()
    try:
        p.feed(html_in)
        p.close()
    except Exception:  # noqa: BLE001 -- entrada torta não pode derrubar nada
        logger.warning("sanitizador falhou; devolvendo texto escapado.")
        return _html.escape(html_in, quote=False)
    return "".join(p.saida)

def renderizar(html_seguro: str, contato: dict[str, Any]) -> str:
    def troca(m: "re.Match[str]") -> str:
        chave = m.group(1).strip().lower()
        if chave not in _VARIAVEIS:
            return ""
        return _html.escape(str(contato.get(chave, "") or ""), quote=False)
    return re.sub(r"\{\{\s*([a-zA-Z_]+)\s*\}\}", troca, html_seguro)

def definir_consentimento(conn: db.Connection, user_id: int, email: str,
                          status: str, source: str = "") -> None:
    email = norm_email(email)
    agora = db.now_iso()
    consent_at = agora if status == "subscribed" else None
    unsub_at = agora if status == "unsubscribed" else None
    conn.execute(
        """INSERT INTO mkt_consent (user_id, email, status, source, consent_at,
                                    unsubscribed_at, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(user_id, email) DO UPDATE SET
                status = excluded.status,
                source = CASE WHEN excluded.source != '' THEN excluded.source ELSE mkt_consent.source END,
                consent_at = COALESCE(mkt_consent.consent_at, excluded.consent_at),
                unsubscribed_at = COALESCE(excluded.unsubscribed_at, mkt_consent.unsubscribed_at),
                updated_at = excluded.updated_at""",
        (user_id, email, status, source, consent_at, unsub_at, agora, agora),
    )

def estado_consentimento(conn: db.Connection, user_id: int, email: str) -> str:
    row = conn.execute(
        "SELECT status FROM mkt_consent WHERE user_id = ? AND email = ?",
        (user_id, norm_email(email)),
    ).fetchone()
    return row["status"] if row else "pending"

def suprimir(conn: db.Connection, user_id: int, email: str,
             reason: str = "manual", detail: str = "") -> None:
    conn.execute(
        """INSERT OR IGNORE INTO mkt_suppression (user_id, email, reason, detail, created_at)
                VALUES (?, ?, ?, ?, ?)""",
        (user_id, norm_email(email), reason, detail[:200], db.now_iso()),
    )

def esta_suprimido(conn: db.Connection, user_id: int, email: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM mkt_suppression WHERE user_id = ? AND email = ?",
        (user_id, norm_email(email)),
    ).fetchone() is not None

def token_para(conn: db.Connection, user_id: int, email: str) -> str:
    email = norm_email(email)
    row = conn.execute(
        "SELECT token FROM mkt_unsub_tokens WHERE user_id = ? AND email = ?",
        (user_id, email),
    ).fetchone()
    if row:
        return row["token"]
    token = secrets.token_urlsafe(24)
    conn.execute(
        "INSERT OR IGNORE INTO mkt_unsub_tokens (token, user_id, email, created_at) VALUES (?, ?, ?, ?)",
        (token, user_id, email, db.now_iso()),
    )
    row = conn.execute(
        "SELECT token FROM mkt_unsub_tokens WHERE user_id = ? AND email = ?",
        (user_id, email),
    ).fetchone()
    return row["token"] if row else token

def descadastrar_por_token(conn: db.Connection, token: str) -> str | None:
    if not token or len(token) > 128:
        return None
    row = conn.execute(
        "SELECT user_id, email FROM mkt_unsub_tokens WHERE token = ?", (token,)
    ).fetchone()
    if row is None:
        return None
    definir_consentimento(conn, row["user_id"], row["email"], "unsubscribed")
    suprimir(conn, row["user_id"], row["email"], reason="unsubscribed")
    return row["email"]

def _where_segmento(segmento: dict[str, Any]) -> tuple[str, list[Any]]:
    sql: list[str] = []
    params: list[Any] = []
    status = segmento.get("status")
    if isinstance(status, list) and status:
        status = [str(s) for s in status][:20]
        sql.append("AND l.status IN (%s)" % ",".join("?" for _ in status))
        params.extend(status)
    minimo = segmento.get("min_value")
    if isinstance(minimo, (int, float)):
        sql.append("AND l.value >= ?")
        params.append(float(minimo))
    return " ".join(sql), params

def _carregar_segmento(bruto: str | None) -> dict[str, Any]:
    try:
        d = json.loads(bruto or "{}")
        return d if isinstance(d, dict) else {}
    except (ValueError, TypeError):
        return {}

def elegiveis(conn: db.Connection, user_id: int, segmento: dict[str, Any]) -> list[dict[str, Any]]:
    extra, params = _where_segmento(segmento)
    linhas = conn.execute(
        f"""SELECT l.id AS lead_id, l.email AS email, l.name AS name,
                   COALESCE(l.company, '') AS company
              FROM leads l
              JOIN mkt_consent c
                ON c.user_id = l.user_id AND c.email = lower(l.email)
             WHERE l.user_id = ?
               AND l.email != ''
               AND c.status = 'subscribed'
               AND NOT EXISTS (
                     SELECT 1 FROM mkt_suppression s
                      WHERE s.user_id = l.user_id AND s.email = lower(l.email))
               {extra}
             ORDER BY l.id""",
        [user_id, *params],
    ).fetchall()
    return [
        {"lead_id": r["lead_id"], "email": r["email"], "name": r["name"], "company": r["company"]}
        for r in linhas
    ]

def contar_elegiveis(conn: db.Connection, user_id: int, segmento: dict[str, Any]) -> int:
    return len(elegiveis(conn, user_id, segmento))

def _rodape_descadastro(unsub_url: str) -> str:
    return (
        '<hr style="margin:28px 0 12px;border:none;border-top:1px solid #ddd">'
        '<p style="font-size:12px;color:#888;line-height:1.5">'
        'Você recebe este e-mail porque optou por receber comunicações. '
        f'<a href="{_html.escape(unsub_url, quote=True)}" style="color:#888">'
        'Descadastrar</a>.</p>'
    )

def montar_html(campanha: dict[str, Any], contato: dict[str, Any], unsub_url: str) -> str:
    corpo = renderizar(campanha["body_html"] or "", contato)
    return f'<div>{corpo}</div>{_rodape_descadastro(unsub_url)}'

def _texto_de(html_final: str) -> str:
    return re.sub(r"<[^>]+>", " ", html_final).replace("&nbsp;", " ").strip()[:5000] or "-"

def _enviar_brevo(destino: str, assunto: str, html_final: str, texto: str, reply_to: str) -> str:
    if not config.brevo_api_key():
        raise RuntimeError("Provedor 'brevo' selecionado, mas BREVO_API_KEY não está no .env (Fase B).")

    raise RuntimeError("Integração Brevo ainda não habilitada (Fase B).")

def enviar_um(destino: str, assunto: str, html_final: str, texto: str, reply_to: str) -> str:
    prov = config.mkt_provider()
    if prov == "brevo":
        return _enviar_brevo(destino, assunto, html_final, texto, reply_to)

    mailer.send_html(destino, assunto, html_final, texto, reply_to=reply_to)
    return "smtp"

def enfileirar(conn: db.Connection, user_id: int, campaign_id: int) -> int:
    camp = conn.execute(
        "SELECT segmento FROM mkt_campaigns WHERE id = ? AND user_id = ?",
        (campaign_id, user_id),
    ).fetchone()
    if camp is None:
        return 0
    destinatarios = elegiveis(conn, user_id, _carregar_segmento(camp["segmento"]))
    agora = db.now_iso()
    n = 0
    for c in destinatarios:
        dedupe = f"{campaign_id}:{norm_email(c['email'])}"
        cur = conn.execute(
            """INSERT OR IGNORE INTO mkt_messages
                   (user_id, campaign_id, email, lead_id, status, dedupe, created_at)
                   VALUES (?, ?, ?, ?, 'queued', ?, ?)""",
            (user_id, campaign_id, norm_email(c["email"]), c["lead_id"], dedupe, agora),
        )
        n += cur.rowcount
    conn.execute(
        "UPDATE mkt_campaigns SET status = 'queued', total_dest = ?, updated_at = ? WHERE id = ? AND user_id = ?",
        (len(destinatarios), agora, campaign_id, user_id),
    )
    return n

def _enviadas_hoje(conn: db.Connection) -> int:
    hoje = db.now_iso()[:10]
    return conn.execute(
        "SELECT COUNT(*) AS c FROM mkt_messages WHERE status = 'sent' AND substr(sent_at,1,10) = ?",
        (hoje,),
    ).fetchone()["c"]

def _contato_da_msg(conn: db.Connection, row: Any) -> dict[str, Any]:
    contato = {"email": row["email"], "nome": "", "empresa": ""}
    if row["lead_id"]:
        lead = conn.execute(
            "SELECT name, COALESCE(company,'') AS company FROM leads WHERE id = ? AND user_id = ?",
            (row["lead_id"], row["user_id"]),
        ).fetchone()
        if lead:
            contato["nome"] = lead["name"]
            contato["empresa"] = lead["company"]
    return contato

def drenar(limite: int = 20) -> dict[str, int]:
    if not config.marketing_enabled():
        return {"enviadas": 0, "falhas": 0, "puladas": 0}

    prov = config.mkt_provider()
    cap = config.mkt_smtp_daily_cap() if prov == "smtp" else 10 ** 9
    base = config.app_base_url()
    enviadas = falhas = puladas = 0

    for _ in range(limite):

        with db.get_conn() as conn:
            if prov == "smtp" and _enviadas_hoje(conn) >= cap:
                break
            row = conn.execute(
                "SELECT * FROM mkt_messages WHERE status = 'queued' ORDER BY id LIMIT 1"
            ).fetchone()
            if row is None:
                break
            claimed = conn.execute(
                "UPDATE mkt_messages SET status = 'sending', tentativas = tentativas + 1 "
                "WHERE id = ? AND status = 'queued'",
                (row["id"],),
            ).rowcount
            if not claimed:
                continue

            uid, email, mid = row["user_id"], row["email"], row["id"]
            camp = conn.execute(
                "SELECT * FROM mkt_campaigns WHERE id = ?", (row["campaign_id"],)
            ).fetchone()

            motivo = ""
            if camp is None or camp["status"] in ("paused", "cancelled"):
                motivo = f"campanha {camp['status'] if camp else 'removida'}"
            elif esta_suprimido(conn, uid, email):
                motivo = "suprimido"
            elif estado_consentimento(conn, uid, email) != "subscribed":
                motivo = "sem consentimento"
            if motivo:
                conn.execute(
                    "UPDATE mkt_messages SET status = 'skipped', erro = ? WHERE id = ?",
                    (motivo, mid),
                )
                puladas += 1
                continue

            token = token_para(conn, uid, email)
            contato = _contato_da_msg(conn, row)
            assunto = renderizar(camp["subject"] or "(sem assunto)", contato)
            reply_to = camp["from_email"] or ""
            unsub_url = f"{base}/descadastro?t={token}"
            html_final = montar_html(dict(camp), contato, unsub_url)
            texto = _texto_de(html_final)

        erro = ""
        provider_ref = ""
        try:
            provider_ref = enviar_um(email, assunto, html_final, texto, reply_to)
        except Exception as e:  # noqa: BLE001 -- falha de envio é esperada e tratada
            erro = f"{type(e).__name__}: {e}"[:200]

        with db.get_conn() as conn:
            if not erro:
                conn.execute(
                    "UPDATE mkt_messages SET status = 'sent', provider_ref = ?, sent_at = ?, erro = '' WHERE id = ?",
                    (provider_ref, db.now_iso(), mid),
                )
                enviadas += 1
            else:
                tent = conn.execute(
                    "SELECT tentativas FROM mkt_messages WHERE id = ?", (mid,)
                ).fetchone()["tentativas"]
                novo = "queued" if tent < MAX_TENTATIVAS else "failed"
                conn.execute(
                    "UPDATE mkt_messages SET status = ?, erro = ? WHERE id = ?",
                    (novo, erro, mid),
                )
                falhas += 1

    _fechar_campanhas_concluidas()
    return {"enviadas": enviadas, "falhas": falhas, "puladas": puladas}

def _fechar_campanhas_concluidas() -> None:
    with db.get_conn() as conn:
        conn.execute(
            """UPDATE mkt_campaigns SET status = 'sent', updated_at = ?
                WHERE status IN ('queued', 'sending')
                  AND NOT EXISTS (
                        SELECT 1 FROM mkt_messages m
                         WHERE m.campaign_id = mkt_campaigns.id
                           AND m.status IN ('queued', 'sending'))
                  AND EXISTS (
                        SELECT 1 FROM mkt_messages m WHERE m.campaign_id = mkt_campaigns.id)""",
            (db.now_iso(),),
        )
