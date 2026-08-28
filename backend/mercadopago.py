from __future__ import annotations

import hashlib
import hmac
import logging
from typing import Any
from urllib.parse import urlparse

import httpx

import config

logger = logging.getLogger("vertex.mp")

API = "https://api.mercadopago.com"
TIMEOUT = 25.0

PROVEDOR = "mercadopago"

PAGO = "approved"

PREAPPROVAL_VIVA = frozenset({"authorized"})

class MPIndisponivel(Exception):
    pass

class MPFalhou(Exception):
    pass

def _headers(idempotencia: str = "") -> dict[str, str]:
    token = config.mp_access_token()
    if not token:
        raise MPIndisponivel("MP_ACCESS_TOKEN não está configurado no .env do servidor.")
    h = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    if idempotencia:

        h["X-Idempotency-Key"] = idempotencia
    return h

def _pedir(metodo: str, caminho: str, corpo: dict | None = None, idem: str = "") -> dict[str, Any]:
    url = f"{API}{caminho}"
    try:
        with httpx.Client(timeout=TIMEOUT) as cliente:
            resp = cliente.request(metodo, url, json=corpo, headers=_headers(idem))
    except httpx.HTTPError as erro:
        logger.error("Mercado Pago inacessível (%s): %s", type(erro).__name__, erro)
        raise MPFalhou("Não foi possível falar com o Mercado Pago.") from erro

    if resp.status_code >= 400:

        logger.error(
            "Mercado Pago recusou %s %s: HTTP %s (%s)",
            metodo, caminho, resp.status_code, resp.text[:180],
        )
        raise MPFalhou(f"O Mercado Pago respondeu {resp.status_code}.")

    try:
        return resp.json()
    except ValueError as erro:
        raise MPFalhou("Resposta do Mercado Pago não era JSON.") from erro

def verificar_assinatura(
    x_signature: str, x_request_id: str, data_id: str, segredo: str = ""
) -> bool:
    segredo = segredo or config.mp_webhook_secret()
    if not segredo:

        logger.error("Webhook recusado: MP_WEBHOOK_SECRET não está configurado.")
        return False
    if not x_signature or not data_id:
        return False

    ts = ""
    v1 = ""
    for parte in str(x_signature).split(","):
        chave, _, valor = parte.strip().partition("=")
        chave = chave.strip()
        if chave == "ts":
            ts = valor.strip()
        elif chave == "v1":
            v1 = valor.strip()
    if not ts or not v1:
        return False

    manifesto = f"id:{data_id};request-id:{x_request_id};ts:{ts};"
    esperado = hmac.new(
        segredo.encode("utf-8"), manifesto.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(esperado, v1):
        logger.warning("Webhook com assinatura inválida (data.id=%s).", data_id)
        return False
    return True

def consultar_preapproval(preapproval_id: str) -> dict[str, Any]:
    return _pedir("GET", f"/preapproval/{preapproval_id}")

def consultar_pagamento(payment_id: str) -> dict[str, Any]:
    return _pedir("GET", f"/v1/payments/{payment_id}")

def _url_de_retorno(caminho: str) -> str:
    base = config.app_base_url()
    if not base:
        raise MPFalhou("APP_BASE_URL não está configurado; o retorno do pagamento não teria para onde voltar.")
    return f"{base.rstrip('/')}{caminho}"

def _base_e_publica() -> bool:
    url = config.app_base_url()
    host = urlparse(url).hostname or ""
    return url.startswith("https://") and host not in {"127.0.0.1", "localhost", "::1"}

def _url_do_webhook() -> str:
    return _url_de_retorno("/api/billing/webhook") if _base_e_publica() else ""

def criar_assinatura_cartao(
    email: str, plano_nome: str, centavos: int, referencia: str
) -> dict[str, Any]:
    corpo: dict[str, Any] = {
        "reason": f"Vertex CRM — plano {plano_nome}",
        "external_reference": referencia,
        "payer_email": email,
        "back_url": _url_de_retorno("/app#/cobranca"),
        "auto_recurring": {
            "frequency": 1,
            "frequency_type": "months",
            "transaction_amount": round(centavos / 100, 2),
            "currency_id": "BRL",
        },
        "status": "pending",
    }
    webhook = _url_do_webhook()
    if webhook:
        corpo["notification_url"] = webhook

    dados = _pedir("POST", "/preapproval", corpo, idem=referencia)
    return {
        "id": str(dados.get("id") or ""),
        "link": dados.get("init_point") or dados.get("sandbox_init_point") or "",
        "status": str(dados.get("status") or ""),
    }

def criar_cobranca_avulsa(
    email: str, plano_nome: str, centavos: int, referencia: str
) -> dict[str, Any]:
    corpo: dict[str, Any] = {
        "items": [
            {
                "title": f"Vertex CRM — plano {plano_nome} (1 mês)",
                "quantity": 1,
                "currency_id": "BRL",
                "unit_price": round(centavos / 100, 2),
            }
        ],
        "payer": {"email": email},
        "external_reference": referencia,
        "back_urls": {
            "success": _url_de_retorno("/app#/cobranca"),
            "pending": _url_de_retorno("/app#/cobranca"),
            "failure": _url_de_retorno("/app#/cobranca"),
        },
        "payment_methods": {"installments": 1},
    }
    if _base_e_publica():

        corpo["auto_return"] = "approved"
    webhook = _url_do_webhook()
    if webhook:
        corpo["notification_url"] = webhook

    dados = _pedir("POST", "/checkout/preferences", corpo, idem=referencia)
    return {
        "id": str(dados.get("id") or ""),
        "link": dados.get("init_point") or dados.get("sandbox_init_point") or "",
        "status": "pendente",
    }

def cancelar_assinatura(preapproval_id: str, idem: str = "") -> None:
    _pedir("PUT", f"/preapproval/{preapproval_id}", {"status": "cancelled"}, idem=idem)
    logger.info("Assinatura %s cancelada no Mercado Pago.", preapproval_id)
