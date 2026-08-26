#!/usr/bin/env python3
from __future__ import annotations

import datetime
import os
import smtplib
import socket
import ssl
import subprocess
import sys
from email.message import EmailMessage
from pathlib import Path

ENV = Path(os.environ.get("VERTEX_ENV", "/etc/vertex-crm/.env"))
LOG = Path("/var/lib/vertex-crm/alertas.log")

def carregar_env(caminho: Path) -> dict[str, str]:
    dados: dict[str, str] = {}
    try:
        for linha in caminho.read_text(encoding="utf-8").splitlines():
            linha = linha.strip()
            if not linha or linha.startswith("#") or "=" not in linha:
                continue
            chave, _, valor = linha.partition("=")
            dados[chave.strip()] = valor.strip().strip('"').strip("'")
    except OSError:
        pass
    return dados

def registrar(msg: str) -> None:
    carimbo = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    linha = f"{carimbo} {msg}\n"
    sys.stderr.write(linha)
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with LOG.open("a", encoding="utf-8") as arquivo:
            arquivo.write(linha)
    except OSError:
        pass

def ultimas_linhas(unidade: str) -> str:
    try:
        saida = subprocess.run(
            ["journalctl", "-u", unidade, "-n", "20", "--no-pager"],
            capture_output=True, text=True, timeout=15, check=False,
        )
        return (saida.stdout or "(sem saída)")[-3000:]
    except Exception:  # noqa: BLE001 -- diagnóstico é secundário ao alerta
        return "(não consegui ler o journal)"

def main() -> int:
    unidade = sys.argv[1] if len(sys.argv) > 1 else "desconhecida"
    host = socket.gethostname()
    teste = "TESTE" in unidade.upper()
    registrar(f"{'TESTE de alerta' if teste else 'ALERTA'}: unidade '{unidade}' em {host}")

    env = carregar_env(ENV)
    smtp_host = env.get("SMTP_HOST")
    smtp_user = env.get("SMTP_USER")
    smtp_pass = env.get("SMTP_PASS")
    smtp_from = env.get("SMTP_FROM") or smtp_user
    destinatarios = [
        e.strip()
        for e in env.get("VERTEX_OWNER_EMAILS", "").replace(";", ",").split(",")
        if e.strip()
    ]

    if not (smtp_host and smtp_user and smtp_pass and destinatarios):
        registrar("SMTP ou destinatário ausente -- alerta ficou só no log, sem e-mail.")
        return 0

    agora = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    if teste:
        assunto = "[Vertex] Teste de alerta (pode ignorar)"
        corpo = (
            f"Este é um teste do sistema de alertas do Vertex, disparado à mão.\n"
            f"Servidor: {host}\nHorário (UTC): {agora}\n\n"
            "Se você recebeu isto, os alertas de falha (backup, app) chegam ao seu e-mail.\n"
        )
    else:
        assunto = f"[Vertex] FALHA: {unidade}"
        corpo = (
            f"A unidade '{unidade}' FALHOU no servidor {host}.\n"
            f"Horário (UTC): {agora}\n\n"
            "Isto é automático (systemd OnFailure). Verifique o serviço.\n\n"
            f"Últimas linhas do log:\n\n{ultimas_linhas(unidade)}\n"
        )

    mensagem = EmailMessage()
    mensagem["Subject"] = assunto
    mensagem["From"] = smtp_from
    mensagem["To"] = ", ".join(destinatarios)
    mensagem.set_content(corpo)

    porta = int(env.get("SMTP_PORT", "587") or "587")
    try:
        with smtplib.SMTP(smtp_host, porta, timeout=30) as servidor:
            servidor.starttls(context=ssl.create_default_context())
            servidor.login(smtp_user, smtp_pass)
            servidor.send_message(mensagem)
    except Exception as erro:  # noqa: BLE001 -- reportar a causa sem vazar segredo
        registrar(f"FALHA ao enviar e-mail de alerta: {type(erro).__name__}: {erro}")
        return 1

    registrar(f"e-mail de alerta enviado para {len(destinatarios)} destinatário(s).")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
