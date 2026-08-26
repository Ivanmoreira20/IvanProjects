# -*- mode: python ; coding: utf-8 -*-
"""Receita do VertexCRM.exe (PyInstaller, --onefile --console).

Construa com:  powershell -ExecutionPolicy Bypass -File build_exe.ps1
Ou direto com: .venv\\Scripts\\pyinstaller.exe --noconfirm VertexCRM.spec

--- Por que o backend viaja como DADO, e nao dentro do PYZ ---------------

O `app.py` calcula a pasta do frontend assim:

    BASE_DIR    = Path(__file__).resolve().parent
    FRONTEND_DIR = BASE_DIR.parent / "frontend"

Se `app` fosse embutido no arquivo PYZ, o `__file__` dele viraria
`<_MEIPASS>/app.pyc` e o `FRONTEND_DIR` apontaria para o PAI do _MEIPASS
(algo como `C:\\Users\\...\\AppData\\Local\\Temp\\frontend`, que nao existe)
-- resultado: pagina em branco / 503 e nenhum CSS ou JS.

Entao os .py do backend sao copiados como dados para `<_MEIPASS>/backend/`
e removidos do PYZ logo abaixo. O `launcher.py` poe `<_MEIPASS>/backend` no
`sys.path` antes de importar, e o `__file__` volta a ter o mesmo formato do
modo dev -- `FRONTEND_DIR` cai certinho em `<_MEIPASS>/frontend`.

Os modulos continuam listados em `hiddenimports` (com `pathex`) para que o
PyInstaller ANALISE as dependencias deles (fastapi, pydantic, sqlite3, ...)
e as embuta normalmente; so o codigo do backend em si e que sai do PYZ.
"""

from pathlib import Path

RAIZ = Path(SPECPATH).resolve()
BACKEND = RAIZ / "backend"
FRONTEND = RAIZ / "frontend"
ICONE = RAIZ / "assets" / "icone.ico"


def _fontes_do_backend():
    """Todo .py do backend, menos testes e __pycache__."""
    return sorted(
        caminho
        for caminho in BACKEND.glob("*.py")
        if not caminho.name.startswith("test_") and caminho.name != "conftest.py"
    )


def _arvore(origem: Path, destino: str):
    """Copia uma pasta inteira preservando a estrutura de diretorios."""
    itens = []
    for caminho in sorted(origem.rglob("*")):
        if not caminho.is_file():
            continue
        partes = caminho.relative_to(origem).parts
        if "__pycache__" in partes or caminho.name.endswith((".pyc", ".db-wal", ".db-shm")):
            continue
        pasta = Path(destino).joinpath(*partes[:-1])
        itens.append((str(caminho), str(pasta)))
    return itens


MODULOS_DO_BACKEND = [caminho.stem for caminho in _fontes_do_backend()]

datas = _arvore(FRONTEND, "frontend")
datas += [(str(caminho), "backend") for caminho in _fontes_do_backend()]

if not datas:
    raise SystemExit("ERRO: nada para empacotar -- confira as pastas frontend/ e backend/.")
if not any(destino == "frontend" and origem.endswith("index.html") for origem, destino in datas):
    raise SystemExit(f"ERRO: nao achei {FRONTEND / 'index.html'}.")

hiddenimports = [
    # Modulos do proprio Vertex (aqui so para a ANALISE de dependencias;
    # sao retirados do PYZ mais abaixo).
    *MODULOS_DO_BACKEND,
    # O uvicorn/starlette resolvem estes por string, em tempo de execucao.
    "uvicorn.logging",
    "uvicorn.loops.auto",
    "uvicorn.loops.asyncio",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.http.httptools_impl",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.protocols.websockets.websockets_impl",
    "uvicorn.lifespan.on",
    "uvicorn.lifespan.off",
    # E-mail de verificacao (mailer.py).
    "smtplib",
    "ssl",
    "email.message",
    "email.mime.text",
    "email.mime.multipart",
    "email.utils",
    "email.header",
    # Login com Google (oauth.py fala HTTPS com o Google via httpx).
    "httpx",
    "certifi",
]

a = Analysis(
    [str(RAIZ / "launcher.py")],
    pathex=[str(BACKEND)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "pytest",
        "_pytest",
        "tkinter",
        "PIL",
        "PyInstaller",
        "pefile",
        "altgraph",
    ],
    noarchive=False,
    optimize=0,
)

# Tira o codigo do backend do PYZ -- ele ja viaja em datas/backend/*.py (ver
# a explicacao no topo do arquivo). Sem isto, o import pegaria a versao do
# PYZ e o caminho do frontend sairia errado.
_fora_do_pyz = set(MODULOS_DO_BACKEND)
a.pure = [entrada for entrada in a.pure if entrada[0] not in _fora_do_pyz]

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="VertexCRM",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    # UPX desligado de proposito: binario comprimido aumenta muito o falso
    # positivo de antivirus, e o ganho de tamanho nao compensa.
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ICONE) if ICONE.exists() else None,
)
