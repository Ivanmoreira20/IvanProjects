from __future__ import annotations

from typing import Literal

AUTOMACOES = "automacoes"
WHATSAPP = "whatsapp"
IA = "ia"
RELATORIOS_AVANCADOS = "relatorios_avancados"
PROPOSTAS = "propostas"
VARIOS_USUARIOS = "varios_usuarios"
API_PUBLICA = "api_publica"

RECURSOS: tuple[str, ...] = (
    AUTOMACOES,
    WHATSAPP,
    IA,
    RELATORIOS_AVANCADOS,
    PROPOSTAS,
    VARIOS_USUARIOS,
    API_PUBLICA,
)

NOME_DO_RECURSO: dict[str, str] = {
    AUTOMACOES: "automações",
    WHATSAPP: "WhatsApp",
    IA: "assistente de IA",
    RELATORIOS_AVANCADOS: "relatórios avançados",
    PROPOSTAS: "propostas",
    VARIOS_USUARIOS: "vários usuários na mesma conta",
    API_PUBLICA: "API pública",
}

Codigo = Literal["inicial", "pro", "empresa"]

INICIAL: Codigo = "inicial"
PRO: Codigo = "pro"
EMPRESA: Codigo = "empresa"

CODIGOS: tuple[str, ...] = (INICIAL, PRO, EMPRESA)

ASSINAVEIS: tuple[str, ...] = (INICIAL, PRO)

class Plano:

    __slots__ = ("codigo", "nome", "resumo", "centavos", "recursos", "limites")

    def __init__(
        self,
        codigo: str,
        nome: str,
        resumo: str,
        centavos: int,
        recursos: frozenset[str],
        limites: dict[str, int],
    ) -> None:
        self.codigo = codigo
        self.nome = nome
        self.resumo = resumo
        self.centavos = centavos
        self.recursos = recursos
        self.limites = limites

    @property
    def reais(self) -> float:
        return self.centavos / 100

    def libera(self, recurso: str) -> bool:
        return recurso in self.recursos

    def limite(self, chave: str) -> int:
        return self.limites.get(chave, -1)

    def to_dict(self) -> dict:
        return {
            "codigo": self.codigo,
            "nome": self.nome,
            "resumo": self.resumo,
            "centavos": self.centavos,
            "preco": self.reais,
            "assinavel": self.codigo in ASSINAVEIS,
            "recursos": sorted(self.recursos),
            "limites": dict(self.limites),
        }

_SEM_TETO = -1

CATALOGO: dict[str, Plano] = {
    INICIAL: Plano(
        codigo=INICIAL,
        nome="Iniciante",
        resumo="O CRM completo para organizar o funil e fechar mais.",

        centavos=3999,
        recursos=frozenset(),
        limites={"usuarios": 1, "leads": _SEM_TETO, "propostas": _SEM_TETO},
    ),
    PRO: Plano(
        codigo=PRO,
        nome="Pro",
        resumo="Para times que vivem de meta e precisam de automação.",

        centavos=7999,
        recursos=frozenset(
            {
                AUTOMACOES,
                WHATSAPP,
                IA,
                RELATORIOS_AVANCADOS,
                PROPOSTAS,
                VARIOS_USUARIOS,
            }
        ),
        limites={"usuarios": 10, "leads": _SEM_TETO, "propostas": _SEM_TETO},
    ),
    EMPRESA: Plano(
        codigo=EMPRESA,
        nome="Empresa",
        resumo="Para operações com vários times, filiais e governança.",

        centavos=0,
        recursos=frozenset(
            {
                AUTOMACOES,
                WHATSAPP,
                IA,
                RELATORIOS_AVANCADOS,
                PROPOSTAS,
                VARIOS_USUARIOS,
                API_PUBLICA,
            }
        ),
        limites={"usuarios": _SEM_TETO, "leads": _SEM_TETO, "propostas": _SEM_TETO},
    ),
}

PADRAO: str = INICIAL

PLANO_DO_TESTE: str = PRO

DIAS_DE_TESTE: int = 14

def obter(codigo: str | None) -> Plano:
    return CATALOGO.get(str(codigo or "").strip().lower(), CATALOGO[PADRAO])

def existe(codigo: str) -> bool:
    return str(codigo or "").strip().lower() in CATALOGO

def catalogo_publico() -> list[dict]:
    return [CATALOGO[c].to_dict() for c in CODIGOS]
