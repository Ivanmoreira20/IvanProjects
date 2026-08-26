from __future__ import annotations

import base64
import binascii
import io
import logging
import os
import secrets
import shutil
from pathlib import Path

import db

logger = logging.getLogger("vertex.avatars")

MAX_BYTES = 5 * 1024 * 1024

MAX_BASE64 = int(MAX_BYTES * 1.40)

MAX_PIXELS = 50_000_000
MAX_LADO_ENTRADA = 12000

LADO = 512
QUALIDADE = 82

FORMATOS_ACEITOS = frozenset({"JPEG", "PNG", "WEBP"})

TAM_CHAVE = 32
_HEX = frozenset("0123456789abcdef")

class AvatarInvalido(Exception):
    pass

def _raiz() -> Path:
    destino = Path(db.db_path()).resolve().parent / "avatars"
    destino.mkdir(parents=True, exist_ok=True)
    return destino

def caminho(user_id: int, key: str) -> Path | None:
    if not key or len(key) != TAM_CHAVE or any(c not in _HEX for c in key):
        return None
    return _raiz() / str(int(user_id)) / f"{key}.webp"

def _abrir_e_validar(bruto: bytes):
    try:
        from PIL import Image, UnidentifiedImageError
        from PIL.Image import DecompressionBombError, DecompressionBombWarning
    except ImportError as erro:  # pragma: no cover - ambiente sem a lib
        raise AvatarInvalido(
            "O envio de foto não está disponível neste servidor."
        ) from erro

    Image.MAX_IMAGE_PIXELS = MAX_PIXELS

    try:
        imagem = Image.open(io.BytesIO(bruto))
        formato = (imagem.format or "").upper()
        largura, altura = imagem.size
    except DecompressionBombError as erro:

        raise AvatarInvalido("A imagem tem dimensões grandes demais.") from erro
    except UnidentifiedImageError as erro:
        raise AvatarInvalido("Não reconhecemos esse arquivo como uma imagem.") from erro
    except Exception as erro:
        raise AvatarInvalido("A imagem parece corrompida. Tente outro arquivo.") from erro

    if formato not in FORMATOS_ACEITOS:
        raise AvatarInvalido("Use uma imagem JPEG, PNG ou WebP.")
    if largura <= 0 or altura <= 0:
        raise AvatarInvalido("A imagem parece corrompida. Tente outro arquivo.")
    if largura > MAX_LADO_ENTRADA or altura > MAX_LADO_ENTRADA:
        raise AvatarInvalido("A imagem tem dimensões grandes demais.")
    if largura * altura > MAX_PIXELS:
        raise AvatarInvalido("A imagem tem dimensões grandes demais.")
    return imagem

def _processar(imagem) -> bytes:
    from PIL import Image

    imagem = imagem.convert("RGB")
    largura, altura = imagem.size
    lado = min(largura, altura)
    esq = (largura - lado) // 2
    topo = (altura - lado) // 2
    imagem = imagem.crop((esq, topo, esq + lado, topo + lado))
    if lado != LADO:
        imagem = imagem.resize((LADO, LADO), Image.LANCZOS)

    saida = io.BytesIO()
    imagem.save(saida, "WEBP", quality=QUALIDADE, method=6)
    return saida.getvalue()

def decodificar(base64_texto: str) -> bytes:
    texto = (base64_texto or "").strip()
    if not texto:
        raise AvatarInvalido("Nenhuma imagem foi enviada.")
    if texto.startswith("data:"):
        _, _, texto = texto.partition(",")
    texto = "".join(texto.split())
    if len(texto) > MAX_BASE64:
        raise AvatarInvalido("A imagem excede o limite de 5 MB.")
    try:
        bruto = base64.b64decode(texto, validate=True)
    except (binascii.Error, ValueError) as erro:
        raise AvatarInvalido("Não foi possível ler o arquivo enviado.") from erro
    if not bruto:
        raise AvatarInvalido("Nenhuma imagem foi enviada.")
    if len(bruto) > MAX_BYTES:
        raise AvatarInvalido("A imagem excede o limite de 5 MB.")
    return bruto

def salvar(user_id: int, base64_texto: str) -> str:
    bruto = decodificar(base64_texto)
    imagem = _abrir_e_validar(bruto)
    processada = _processar(imagem)

    chave = secrets.token_hex(16)
    destino = caminho(user_id, chave)
    assert destino is not None
    destino.parent.mkdir(parents=True, exist_ok=True)

    temporario = destino.with_suffix(".parcial")
    temporario.write_bytes(processada)
    os.replace(temporario, destino)

    logger.info(
        "avatar gravado: conta=%s bytes_entrada=%s bytes_saida=%s",
        user_id, len(bruto), len(processada),
    )
    return chave

def remover(user_id: int, key: str) -> None:
    alvo = caminho(user_id, key)
    if alvo is None:
        return
    try:
        alvo.unlink(missing_ok=True)
    except OSError as erro:

        logger.warning("não consegui apagar o avatar da conta %s: %s", user_id, erro)

def remover_tudo(user_id: int) -> None:
    shutil.rmtree(_raiz() / str(int(user_id)), ignore_errors=True)

def ler(user_id: int, key: str) -> bytes | None:
    alvo = caminho(user_id, key)
    if alvo is None or not alvo.is_file():
        return None
    try:
        return alvo.read_bytes()
    except OSError:
        return None
