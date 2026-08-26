from __future__ import annotations

import os
import subprocess
import sys
import venv
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
VENV_DIR = BASE_DIR / ".venv"
BACKEND_DIR = BASE_DIR / "backend"
FRONTEND_DIR = BASE_DIR / "frontend"
REQUIREMENTS = BACKEND_DIR / "requirements.txt"

HOST = "127.0.0.1"
PORT = "8000"

def _make_console_utf8() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
            except (ValueError, OSError):
                pass

def say(message: str = "") -> None:
    print(message, flush=True)

def venv_python() -> Path:
    if os.name == "nt":
        return VENV_DIR / "Scripts" / "python.exe"
    return VENV_DIR / "bin" / "python"

def ensure_venv() -> None:
    if venv_python().exists():
        say(f"[1/3] Ambiente virtual já existe em {VENV_DIR}")
        return
    say(f"[1/3] Criando ambiente virtual em {VENV_DIR} ...")
    venv.EnvBuilder(with_pip=True, upgrade_deps=False).create(VENV_DIR)

def install_requirements() -> None:
    say(f"[2/3] Instalando dependências de {REQUIREMENTS.name} ...")
    subprocess.check_call(
        [
            str(venv_python()),
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "--quiet",
            "--requirement",
            str(REQUIREMENTS),
        ]
    )

def print_banner() -> None:
    line = "=" * 66
    say()
    say(line)
    say("  VERTEX CRM")
    say(line)
    say(f"  Acesse:  http://{HOST}:{PORT}")
    say(f"  API:     http://{HOST}:{PORT}/api")
    say(f"  Docs:    http://{HOST}:{PORT}/docs")
    say()
    say()
    say('  Dica: entre sem marcar "Lembrar de mim", feche o navegador por')
    say("  completo e reabra -- o login deve ser exigido de novo.")
    say()
    say("  Para encerrar o servidor: Ctrl+C")
    say(line)
    say()

def run_server() -> int:
    say("[3/3] Subindo o servidor ...")
    if not FRONTEND_DIR.is_dir():
        say(f"  AVISO: a pasta {FRONTEND_DIR} não existe; a interface não será servida.")
    elif not (FRONTEND_DIR / "index.html").exists():
        say(f"  AVISO: {FRONTEND_DIR} ainda não tem index.html.")
    print_banner()
    try:
        return subprocess.call(
            [str(venv_python()), "-m", "uvicorn", "app:app", "--host", HOST, "--port", PORT],
            cwd=str(BACKEND_DIR),
        )
    except KeyboardInterrupt:
        return 0

def main() -> int:
    _make_console_utf8()
    if not REQUIREMENTS.exists():
        print(f"ERRO: não encontrei {REQUIREMENTS}. Rode este script de dentro de Dashboard/.")
        return 1
    try:
        ensure_venv()
        install_requirements()
    except subprocess.CalledProcessError as error:
        print(f"ERRO ao preparar o ambiente (código {error.returncode}).")
        return error.returncode
    return run_server()

if __name__ == "__main__":
    raise SystemExit(main())
