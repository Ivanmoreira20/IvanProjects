#!/usr/bin/env python3
"""Carimba os arquivos estáticos citados no HTML com o hash do conteúdo deles.

Por que existe
--------------
O navegador precisa de duas coisas ao mesmo tempo, e elas se contradizem:

  * **nunca** servir um CSS/JS velho depois de um deploy (senão a tela quebra
    contra uma API nova, e ninguém consegue reproduzir);
  * **não** rebaixar todo carregamento a uma ida ao servidor por arquivo.

A saída clássica é colocar o conteúdo na URL. `style.css?v=9f2c1a` é um endereço
diferente de `style.css?v=3b70de`: se o conteúdo mudou, o endereço mudou, e o
navegador baixa o novo sem precisar perguntar nada. Enquanto não muda, ele pode
guardar por um ano com segurança.

O servidor fecha o par (ver `EstaticosComCache` em `app.py`): pedido **com**
`?v=` recebe um ano de cache; pedido **sem** `?v=` recebe `no-cache`. Ou seja,
esquecer de rodar este script custa desempenho, nunca correção.

Uso (a partir da raiz do projeto, antes de empacotar o deploy):

    python deploy/carimbar.py             # carimba e diz o que mudou
    python deploy/carimbar.py --conferir  # só confere, não escreve (para CI)

É idempotente: rodar duas vezes seguidas não muda nada na segunda.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
FRONT = RAIZ / "frontend"

EXTENSOES = (".css", ".js", ".webp", ".jpg", ".png", ".svg", ".ico")

_REF = re.compile(
    r'((?:href|src|content)=")([^"?#]+?)(\?[^"#]*)?(#[^"]*)?(")',
    re.IGNORECASE,
)

TAM_CARIMBO = 10

def _hash(arquivo: Path) -> str:
    return hashlib.sha256(arquivo.read_bytes()).hexdigest()[:TAM_CARIMBO]

def _resolver(caminho: str) -> Path | None:
    if caminho.startswith(("http://", "https://")):

        marca = "vertexcrm.tech/"
        if marca not in caminho:
            return None
        caminho = caminho.split(marca, 1)[1]
    if caminho.startswith("//") or caminho.startswith("mailto:"):
        return None
    if not caminho.lower().endswith(EXTENSOES):
        return None
    alvo = FRONT / caminho.lstrip("/")
    return alvo if alvo.is_file() else None

def carimbar_texto(html: str) -> tuple[str, int]:
    trocas = 0

    def troca(m: re.Match[str]) -> str:
        nonlocal trocas
        prefixo, caminho, _query_antiga, ancora, fim = m.groups()
        arquivo = _resolver(caminho)
        if arquivo is None:
            return m.group(0)
        trocas += 1

        return f"{prefixo}{caminho}?v={_hash(arquivo)}{ancora or ''}{fim}"

    return _REF.sub(troca, html), trocas

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--conferir", action="store_true",
                    help="não escreve; sai com 1 se algum arquivo estiver desatualizado")
    args = ap.parse_args()

    paginas = sorted(FRONT.glob("*.html"))
    if not paginas:
        print(f"nenhum HTML em {FRONT}", file=sys.stderr)
        return 1

    desatualizados = []
    for pagina in paginas:
        antes = pagina.read_text(encoding="utf-8")
        depois, trocas = carimbar_texto(antes)
        if antes == depois:
            print(f"  = {pagina.name} ({trocas} referência(s), já em dia)")
            continue
        desatualizados.append(pagina.name)
        if args.conferir:
            print(f"  ! {pagina.name} ({trocas} referência(s) desatualizada(s))")
            continue
        pagina.write_text(depois, encoding="utf-8", newline="")
        print(f"  * {pagina.name} ({trocas} referência(s) carimbada(s))")

    if args.conferir and desatualizados:
        print(f"\nDesatualizados: {', '.join(desatualizados)}", file=sys.stderr)
        return 1
    print("\ncarimbo concluído.")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
