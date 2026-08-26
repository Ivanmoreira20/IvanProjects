from __future__ import annotations

import hashlib
import hmac
import logging
import re
from typing import Any

import activities
import config
import db

logger = logging.getLogger("vertex.whatsapp")

GRAPH_VERSION = "v21.0"
GRAPH_BASE = "https://graph.facebook.com"
TIMEOUT = 15.0

MAX_BODY = 4000

def token() -> str:
    return config.get("WHATSAPP_TOKEN")

def app_secret() -> str:
    return config.get("WHATSAPP_APP_SECRET")

def verify_token() -> str:
    return config.get("WHATSAPP_VERIFY_TOKEN")

def is_configured() -> bool:
    return bool(token())

def get_config(conn: db.Connection, user_id: int) -> dict[str, Any]:
    linha = conn.execute("SELECT * FROM wa_config WHERE user_id = ?", (user_id,)).fetchone()
    base = {
        "provider": "cloud_api",
        "phone_number_id": "",
        "waba_id": "",
        "display_phone": "",
        "status": "desconectado",
        "last_error": "",
        "last_check_at": None,
        "connected_at": None,
    }
    if linha is not None:
        base.update(
            {
                "provider": linha["provider"],
                "phone_number_id": linha["phone_number_id"],
                "waba_id": linha["waba_id"],
                "display_phone": linha["display_phone"],
                "status": linha["status"],
                "last_error": linha["last_error"],
                "last_check_at": linha["last_check_at"],
                "connected_at": linha["connected_at"],
            }
        )

    base["server_token"] = is_configured()
    if not is_configured() and base["status"] == "conectado":
        base["status"] = "desconectado"
        base["last_error"] = (
            "O token do servidor não está preenchido. Peça para preencher "
            "WHATSAPP_TOKEN no arquivo .env do servidor."
        )
    base["ready"] = bool(base["server_token"] and base["phone_number_id"])
    return base

def save_config(
    conn: db.Connection,
    user_id: int,
    *,
    phone_number_id: str,
    waba_id: str,
    display_phone: str,
) -> None:
    agora = db.now_iso()
    conn.execute(
        """INSERT INTO wa_config
               (user_id, provider, phone_number_id, waba_id, display_phone,
                status, last_error, updated_at)
           VALUES (?, 'cloud_api', ?, ?, ?, 'desconectado', '', ?)
           ON CONFLICT(user_id) DO UPDATE SET
               phone_number_id = excluded.phone_number_id,
               waba_id         = excluded.waba_id,
               display_phone   = excluded.display_phone,
               updated_at      = excluded.updated_at""",
        (user_id, phone_number_id, waba_id, display_phone, agora),
    )

def set_status(conn: db.Connection, user_id: int, status: str, erro: str = "") -> None:
    agora = db.now_iso()
    conectado = agora if status == "conectado" else None
    conn.execute(
        """UPDATE wa_config
              SET status = ?, last_error = ?, last_check_at = ?,
                  connected_at = COALESCE(?, connected_at), updated_at = ?
            WHERE user_id = ?""",
        (status, erro[:500], agora, conectado, agora, user_id),
    )

def disconnect(conn: db.Connection, user_id: int) -> None:
    conn.execute(
        """UPDATE wa_config
              SET status = 'desconectado', last_error = '', connected_at = NULL, updated_at = ?
            WHERE user_id = ?""",
        (db.now_iso(), user_id),
    )

_SO_DIGITO = re.compile(r"\D+")

def normalize_phone(raw: str) -> str:
    digitos = _SO_DIGITO.sub("", str(raw or ""))
    if not digitos:
        return ""
    if len(digitos) in (10, 11):
        return "55" + digitos
    return digitos

def phone_is_plausible(numero: str) -> bool:
    return 10 <= len(normalize_phone(numero)) <= 15

def _registra(
    conn: db.Connection,
    user_id: int,
    *,
    lead_id: int | None,
    direction: str,
    phone: str,
    body: str,
    template_name: str,
    status: str,
    wa_message_id: str = "",
    error: str = "",
) -> int:
    cur = conn.execute(
        """INSERT INTO wa_messages
               (user_id, lead_id, direction, phone, body, template_name,
                wa_message_id, status, error, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (user_id, lead_id, direction, phone, body[:MAX_BODY], template_name,
         wa_message_id, status, error[:500], db.now_iso()),
    )
    return int(cur.lastrowid)

def _payload(numero: str, corpo: str, template: str, idioma: str) -> dict[str, Any]:
    if template:
        return {
            "messaging_product": "whatsapp",
            "to": numero,
            "type": "template",
            "template": {"name": template, "language": {"code": idioma}},
        }
    return {
        "messaging_product": "whatsapp",
        "to": numero,
        "type": "text",
        "text": {"preview_url": False, "body": corpo},
    }

def send_message(
    conn: db.Connection,
    user_id: int,
    *,
    lead_id: int | None,
    phone: str,
    body: str = "",
    template_name: str = "",
    source: str = "user",
) -> dict[str, Any]:
    numero = normalize_phone(phone)
    corpo = str(body or "").strip()[:MAX_BODY]
    idioma = "pt_BR"

    if template_name:
        modelo = conn.execute(
            "SELECT body, language FROM wa_templates WHERE user_id = ? AND name = ?",
            (user_id, template_name),
        ).fetchone()
        if modelo is None:
            return {"ok": False, "error": f"template “{template_name}” não existe", "phone": numero}
        idioma = modelo["language"]
        corpo = corpo or modelo["body"]

    if not numero:
        return {"ok": False, "error": "número de WhatsApp vazio", "phone": ""}
    if not phone_is_plausible(numero):
        return {"ok": False, "error": f"número inválido: {phone}", "phone": numero}
    if not corpo and not template_name:
        return {"ok": False, "error": "mensagem vazia", "phone": numero}

    conf = get_config(conn, user_id)
    if not conf["ready"]:
        motivo = (
            "O token do WhatsApp não está preenchido no servidor."
            if not conf["server_token"]
            else "Falta informar o ID do número (phone_number_id) em Configurações."
        )
        _registra(conn, user_id, lead_id=lead_id, direction="saida", phone=numero,
                  body=corpo, template_name=template_name, status="falhou", error=motivo)
        return {"ok": False, "error": motivo, "phone": numero}

    url = f"{GRAPH_BASE}/{GRAPH_VERSION}/{conf['phone_number_id']}/messages"
    try:
        import httpx

        resposta = httpx.post(
            url,
            json=_payload(numero, corpo, template_name, idioma),
            headers={"Authorization": f"Bearer {token()}"},
            timeout=TIMEOUT,
        )
        dados = resposta.json() if resposta.content else {}
    except Exception as erro:  # noqa: BLE001 - rede e sempre incerta
        logger.warning("Falha de rede ao enviar WhatsApp: %s", erro)
        _registra(conn, user_id, lead_id=lead_id, direction="saida", phone=numero,
                  body=corpo, template_name=template_name, status="falhou",
                  error=f"falha de rede: {erro}")
        set_status(conn, user_id, "erro", f"falha de rede: {erro}")
        return {"ok": False, "error": "não foi possível falar com o WhatsApp agora", "phone": numero}

    if resposta.status_code >= 400:
        detalhe = str(dados.get("error", {}).get("message") or resposta.text)[:300]
        _registra(conn, user_id, lead_id=lead_id, direction="saida", phone=numero,
                  body=corpo, template_name=template_name, status="falhou", error=detalhe)
        set_status(conn, user_id, "erro", detalhe)
        logger.warning("WhatsApp recusou o envio (%s): %s", resposta.status_code, detalhe)
        return {"ok": False, "error": detalhe, "phone": numero}

    wa_id = ""
    try:
        wa_id = str(dados["messages"][0]["id"])
    except (KeyError, IndexError, TypeError):
        pass

    _registra(conn, user_id, lead_id=lead_id, direction="saida", phone=numero,
              body=corpo, template_name=template_name, status="enviada", wa_message_id=wa_id)
    set_status(conn, user_id, "conectado")

    if lead_id is not None:
        activities.log(
            conn, user_id, lead_id=lead_id, kind="whatsapp",
            title="Mensagem enviada no WhatsApp",
            detail=corpo, source="automation" if source == "automation" else "whatsapp",
            ref_type="whatsapp",
        )

    return {"ok": True, "phone": numero, "wa_message_id": wa_id, "body": corpo}

def check_connection(conn: db.Connection, user_id: int) -> dict[str, Any]:
    conf = get_config(conn, user_id)
    if not conf["server_token"]:
        return {"ok": False, "status": "desconectado",
                "error": "Falta WHATSAPP_TOKEN no .env do servidor."}
    if not conf["phone_number_id"]:
        return {"ok": False, "status": "desconectado",
                "error": "Informe o ID do número (phone_number_id)."}

    url = f"{GRAPH_BASE}/{GRAPH_VERSION}/{conf['phone_number_id']}"
    try:
        import httpx

        resposta = httpx.get(
            url,
            params={"fields": "display_phone_number,verified_name,quality_rating"},
            headers={"Authorization": f"Bearer {token()}"},
            timeout=TIMEOUT,
        )
        dados = resposta.json() if resposta.content else {}
    except Exception as erro:  # noqa: BLE001
        set_status(conn, user_id, "erro", f"falha de rede: {erro}")
        return {"ok": False, "status": "erro", "error": f"não foi possível falar com o WhatsApp: {erro}"}

    if resposta.status_code >= 400:
        detalhe = str(dados.get("error", {}).get("message") or resposta.text)[:300]
        set_status(conn, user_id, "erro", detalhe)
        return {"ok": False, "status": "erro", "error": detalhe}

    numero = str(dados.get("display_phone_number") or "")
    if numero:
        conn.execute(
            "UPDATE wa_config SET display_phone = ? WHERE user_id = ?", (numero, user_id)
        )
    set_status(conn, user_id, "conectado")
    return {
        "ok": True,
        "status": "conectado",
        "display_phone": numero,
        "verified_name": str(dados.get("verified_name") or ""),
        "quality": str(dados.get("quality_rating") or ""),
    }

def signature_ok(raw_body: bytes, header: str | None) -> bool:
    segredo = app_secret()
    if not segredo or not header:
        return False
    esperado = hmac.new(segredo.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    recebido = header.split("=", 1)[-1].strip()
    return hmac.compare_digest(esperado, recebido)

def _acha_lead(conn: db.Connection, user_id: int, numero: str) -> db.Row | None:
    if len(numero) < 8:
        return None
    sufixo = numero[-8:]
    return conn.execute(
        """SELECT id, name FROM leads
            WHERE user_id = ?
              AND (replace(replace(replace(replace(whatsapp,'-',''),' ',''),'(',''),')','') LIKE ?
                OR replace(replace(replace(replace(phone,   '-',''),' ',''),'(',''),')','') LIKE ?)
         ORDER BY updated_at DESC LIMIT 1""",
        (user_id, f"%{sufixo}", f"%{sufixo}"),
    ).fetchone()

def handle_webhook(payload: dict) -> int:
    gravadas = 0
    for entrada in payload.get("entry", []) or []:
        for mudanca in entrada.get("changes", []) or []:
            valor = mudanca.get("value") or {}
            metadados = valor.get("metadata") or {}
            phone_number_id = str(metadados.get("phone_number_id") or "")
            if not phone_number_id:
                continue

            with db.get_conn() as conn:
                dono = conn.execute(
                    "SELECT user_id FROM wa_config WHERE phone_number_id = ?",
                    (phone_number_id,),
                ).fetchone()
                if dono is None:
                    logger.info("Webhook para um número desconhecido; ignorado.")
                    continue
                user_id = int(dono["user_id"])

                for mensagem in valor.get("messages", []) or []:
                    numero = normalize_phone(str(mensagem.get("from") or ""))
                    texto = str((mensagem.get("text") or {}).get("body") or "")
                    tipo = str(mensagem.get("type") or "")
                    if not texto and tipo:
                        texto = f"[mensagem de {tipo}]"

                    lead = _acha_lead(conn, user_id, numero)
                    lead_id = int(lead["id"]) if lead else None

                    _registra(conn, user_id, lead_id=lead_id, direction="entrada",
                              phone=numero, body=texto, template_name="",
                              status="recebida", wa_message_id=str(mensagem.get("id") or ""))
                    gravadas += 1

                    if lead_id is not None:
                        activities.log(
                            conn, user_id, lead_id=lead_id, kind="whatsapp",
                            title="Mensagem recebida no WhatsApp", detail=texto,
                            source="whatsapp", ref_type="whatsapp",
                        )
                        activities.notify(
                            conn, user_id, type="whatsapp",
                            title=f"WhatsApp de {lead['name']}",
                            body=texto[:200], severity="info",
                            ref_type="lead", ref_id=lead_id,
                        )
                        import automations

                        automations.dispatch(
                            conn, user_id, "whatsapp.recebido",
                            lead=dict(conn.execute(
                                "SELECT * FROM leads WHERE id = ? AND user_id = ?",
                                (lead_id, user_id)).fetchone()),
                        )
                    else:
                        activities.notify(
                            conn, user_id, type="whatsapp",
                            title=f"WhatsApp de {numero}",
                            body="Número não está em nenhum lead. " + texto[:160],
                            severity="info",
                        )

                for estado in valor.get("statuses", []) or []:
                    wa_id = str(estado.get("id") or "")
                    situacao = {
                        "sent": "enviada", "delivered": "entregue", "read": "lida",
                        "failed": "falhou",
                    }.get(str(estado.get("status") or ""), "")
                    if wa_id and situacao:
                        conn.execute(
                            "UPDATE wa_messages SET status = ? WHERE wa_message_id = ? AND user_id = ?",
                            (situacao, wa_id, user_id),
                        )
    return gravadas

def conversation(conn: db.Connection, user_id: int, lead_id: int, limit: int = 100) -> list[dict]:
    linhas = conn.execute(
        """SELECT id, direction, phone, body, template_name, status, error, created_at
             FROM wa_messages
            WHERE user_id = ? AND lead_id = ?
         ORDER BY created_at ASC, id ASC
            LIMIT ?""",
        (user_id, lead_id, limit),
    ).fetchall()
    return [
        {
            "id": linha["id"],
            "direction": linha["direction"],
            "phone": linha["phone"],
            "body": linha["body"],
            "template_name": linha["template_name"],
            "status": linha["status"],
            "error": linha["error"],
            "created_at": linha["created_at"],
        }
        for linha in linhas
    ]
