from __future__ import annotations

import logging
import os
import smtplib
import ssl
import threading
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path

import config

logger = logging.getLogger("vertex.mailer")

SMTP_TIMEOUT = 20.0
CODE_TTL_MINUTES = 15

VIOLET = "#7C3AED"
VIOLET_DARK = "#5B21B6"
INK = "#1E1B2E"
MUTED = "#6B7280"

def is_configured() -> bool:
    return config.smtp_configured()

def _safe_print(line: str) -> None:
    try:
        print(line, flush=True)
    except UnicodeEncodeError:
        print(line.encode("ascii", "replace").decode("ascii"), flush=True)

CODE_FILE_NAME = "CODIGO-DE-VERIFICACAO.txt"

def _fallback_dir() -> Path:
    home = os.environ.get("VERTEX_HOME")
    if home:
        candidato = Path(home)
        if candidato.is_dir():
            return candidato
    return Path(__file__).resolve().parent.parent

def write_code_to_file(email: str, code: str, reason: str) -> Path | None:
    try:
        destino = _fallback_dir() / CODE_FILE_NAME
        destino.write_text(
            "CÓDIGO DE VERIFICAÇÃO — Vertex CRM\n"
            "===================================\n\n"
            f"E-mail : {email}\n"
            f"Código : {code}\n"
            f"Gerado : {datetime.now().strftime('%d/%m/%Y às %H:%M:%S')}\n"
            f"Validade: {CODE_TTL_MINUTES} minutos\n\n"
            f"{reason}\n\n"
            "Este arquivo só existe porque o envio por e-mail ainda não foi\n"
            "configurado. Preencha SMTP_USER e SMTP_PASS no arquivo .env\n"
            "(veja CONFIGURACAO.md) e o código passará a chegar na caixa de\n"
            "entrada — este arquivo deixa de ser criado.\n",
            encoding="utf-8",
        )
        return destino
    except OSError as err:
        logger.warning("Não consegui gravar %s: %s", CODE_FILE_NAME, err)
        return None

def print_code_to_console(email: str, code: str, reason: str) -> None:
    border = "=" * 72
    caminho = write_code_to_file(email, code, reason)
    _safe_print("")
    _safe_print(border)
    _safe_print(f"  >>> CÓDIGO DE VERIFICAÇÃO PARA {email}: {code} <<<")
    _safe_print(f"  Válido por {CODE_TTL_MINUTES} minutos. {reason}")
    if caminho is not None:
        _safe_print(f"  Também salvo em: {caminho}")
    _safe_print(border)
    _safe_print("")

def _plain_text(name: str, code: str) -> str:
    return (
        f"Olá, {name}!\n\n"
        "Use o código abaixo para confirmar seu e-mail no Vertex CRM:\n\n"
        f"    {code}\n\n"
        f"O código vale por {CODE_TTL_MINUTES} minutos e só pode ser usado uma vez.\n"
        "Se não foi você que criou esta conta, ignore esta mensagem — nada será feito.\n\n"
        "— Equipe Vertex CRM\n"
    )

def _html(name: str, code: str) -> str:
    spaced_code = " ".join(code)
    return f"""\
<!doctype html>
<html lang="pt-BR">
  <body style="margin:0;padding:32px 16px;background:#F5F3FF;font-family:'Segoe UI',Arial,sans-serif;color:{INK};">
    <table role="presentation" cellpadding="0" cellspacing="0" width="100%" style="max-width:520px;margin:0 auto;background:#FFFFFF;border-radius:20px;overflow:hidden;box-shadow:0 12px 40px rgba(91,33,182,.14);">
      <tr>
        <td style="background:{VIOLET_DARK};padding:24px 32px;">
          <span style="color:#FFFFFF;font-size:18px;font-weight:700;letter-spacing:.5px;">Vertex CRM</span>
        </td>
      </tr>
      <tr>
        <td style="padding:32px;">
          <p style="margin:0 0 8px;font-size:20px;font-weight:700;">Olá, {name}!</p>
          <p style="margin:0 0 24px;font-size:15px;line-height:1.6;color:{MUTED};">
            Use o código abaixo para confirmar seu e-mail e liberar o acesso à sua conta.
          </p>
          <div style="margin:0 0 24px;padding:20px;text-align:center;background:#F5F3FF;border:1px solid #DDD6FE;border-radius:16px;">
            <span style="display:inline-block;font-size:34px;font-weight:700;letter-spacing:10px;color:{VIOLET};">{spaced_code}</span>
          </div>
          <p style="margin:0 0 8px;font-size:14px;line-height:1.6;color:{MUTED};">
            O código vale por <strong style="color:{INK};">{CODE_TTL_MINUTES} minutos</strong> e só pode ser usado uma vez.
          </p>
          <p style="margin:0;font-size:14px;line-height:1.6;color:{MUTED};">
            Se não foi você que criou esta conta, é só ignorar esta mensagem.
          </p>
        </td>
      </tr>
      <tr>
        <td style="padding:18px 32px;background:#FAFAFA;border-top:1px solid #EEE;font-size:12px;color:{MUTED};">
          Mensagem automática do Vertex CRM — não responda a este e-mail.
        </td>
      </tr>
    </table>
  </body>
</html>
"""

ASSUNTOS = {
    "verify_email": "Seu código de verificação: {code}",
    "reset_password": "Código para redefinir sua senha: {code}",
    "change_email": "Confirme seu novo e-mail: {code}",
}

def build_message(email: str, name: str, code: str, tipo: str = "verify_email") -> EmailMessage:
    message = EmailMessage()
    modelo = ASSUNTOS.get(tipo, ASSUNTOS["verify_email"])
    message["Subject"] = modelo.format(code=code)
    message["From"] = config.smtp_from()
    message["To"] = email
    message.set_content(_plain_text(name, code))
    message.add_alternative(_html(name, code), subtype="html")
    return message

def _deliver(email: str, name: str, code: str, tipo: str = "verify_email") -> None:
    host = config.smtp_host()
    port = config.smtp_port()
    context = ssl.create_default_context()
    try:
        if port == 465:
            with smtplib.SMTP_SSL(host, port, timeout=SMTP_TIMEOUT, context=context) as smtp:
                smtp.login(config.smtp_user(), config.smtp_password())
                smtp.send_message(build_message(email, name, code, tipo))
        else:
            with smtplib.SMTP(host, port, timeout=SMTP_TIMEOUT) as smtp:
                smtp.ehlo()
                smtp.starttls(context=context)
                smtp.ehlo()
                smtp.login(config.smtp_user(), config.smtp_password())
                smtp.send_message(build_message(email, name, code, tipo))
    except Exception as error:  # noqa: BLE001 -- thread daemon: nada pode escapar
        logger.error(
            "Falha ao enviar o e-mail de verificação para %s via %s:%s (%s: %s).",
            email, host, port, type(error).__name__, error,
        )
        print_code_to_console(
            email, code, "O envio por SMTP falhou — use o código acima para concluir o cadastro."
        )
        return
    logger.info("Código de verificação enviado para %s.", email)

def send_verification_code(email: str, name: str, code: str, tipo: str = "verify_email") -> None:
    if not is_configured():
        logger.warning(
            "SMTP não configurado (SMTP_HOST/SMTP_USER/SMTP_PASS no .env). "
            "O código de %s vai para o console do servidor em vez do e-mail.",
            email,
        )
        print_code_to_console(
            email, code, "Configure o SMTP no arquivo .env para enviar por e-mail."
        )
        return

    threading.Thread(
        target=_deliver,
        args=(email, name, code, tipo),
        name="vertex-mailer",
        daemon=True,
    ).start()

def send_html(
    to: str,
    subject: str,
    html: str,
    text: str = "",
    reply_to: str = "",
    headers: dict[str, str] | None = None,
) -> None:
    if not is_configured():
        raise RuntimeError("SMTP não configurado no servidor.")
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = config.smtp_from()
    msg["To"] = to
    if reply_to:
        msg["Reply-To"] = reply_to
    for chave, valor in (headers or {}).items():
        msg[chave] = valor
    msg.set_content(text or "Este e-mail precisa de um leitor compatível com HTML.")
    msg.add_alternative(html, subtype="html")

    host, port = config.smtp_host(), config.smtp_port()
    contexto = ssl.create_default_context()
    if port == 465:
        with smtplib.SMTP_SSL(host, port, timeout=SMTP_TIMEOUT, context=contexto) as smtp:
            smtp.login(config.smtp_user(), config.smtp_password())
            smtp.send_message(msg)
    else:
        with smtplib.SMTP(host, port, timeout=SMTP_TIMEOUT) as smtp:
            smtp.ehlo()
            smtp.starttls(context=contexto)
            smtp.ehlo()
            smtp.login(config.smtp_user(), config.smtp_password())
            smtp.send_message(msg)

def _entregar_aviso(assunto: str, corpo: str) -> None:
    host, port = config.smtp_host(), config.smtp_port()
    destino = config.smtp_user()
    contexto = ssl.create_default_context()

    mensagem = EmailMessage()
    mensagem["Subject"] = assunto
    mensagem["From"] = config.smtp_from()
    mensagem["To"] = destino
    mensagem.set_content(corpo)

    try:
        if port == 465:
            with smtplib.SMTP_SSL(host, port, timeout=SMTP_TIMEOUT, context=contexto) as smtp:
                smtp.login(destino, config.smtp_password())
                smtp.send_message(mensagem)
        else:
            with smtplib.SMTP(host, port, timeout=SMTP_TIMEOUT) as smtp:
                smtp.ehlo()
                smtp.starttls(context=contexto)
                smtp.ehlo()
                smtp.login(destino, config.smtp_password())
                smtp.send_message(mensagem)
    except Exception as erro:  # noqa: BLE001
        logger.error("Falha ao avisar sobre o pedido de plano (%s: %s).", type(erro).__name__, erro)
        _safe_print(f"[vertex] AVISO NAO ENVIADO -- {assunto}\n{corpo}")
        return
    logger.info("Aviso enviado para %s: %s", destino, assunto)

def send_plan_interest(dados: dict) -> None:
    plano = str(dados.get("plan", "")).upper()
    assunto = f"[Vertex] Pedido do plano {plano}: {dados.get('company') or dados.get('name')}"
    corpo = "\n".join(
        [
            f"Plano.....: {plano}",
            f"Nome......: {dados.get('name', '')}",
            f"E-mail....: {dados.get('email', '')}",
            f"Empresa...: {dados.get('company') or '(não informou)'}",
            f"Telefone..: {dados.get('phone') or '(não informou)'}",
            f"Usuários..: {dados.get('seats', 1)}",
            f"Já tem conta: {'sim' if dados.get('user_id') else 'não'}",
            "",
            "Mensagem:",
            dados.get("message") or "(sem mensagem)",
        ]
    )

    if not is_configured():
        logger.warning("SMTP não configurado: o pedido de plano ficou só no banco de dados.")
        _safe_print(f"[vertex] PEDIDO DE PLANO\n{corpo}")
        return

    threading.Thread(
        target=_entregar_aviso, args=(assunto, corpo), name="vertex-mailer-plan", daemon=True
    ).start()
