from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import config
import db
import plans

logger = logging.getLogger("vertex.billing")

GRATUITO = "gratuito"
TRIAL = "trial"
ATIVA = "ativa"
PENDENTE = "pendente"
VENCIDA = "vencida"
CANCELADA = "cancelada"

ESTADOS_COM_ACESSO: frozenset[str] = frozenset({TRIAL, ATIVA})

MODO_CARTAO = "cartao"
MODO_AVULSO = "avulso"

class SemAcesso(Exception):

    def __init__(self, recurso: str, plano: str) -> None:
        self.recurso = recurso
        self.plano = plano
        nome = plans.NOME_DO_RECURSO.get(recurso, recurso)
        super().__init__(f"O plano {plans.obter(plano).nome} não inclui {nome}.")

def agora() -> datetime:
    return datetime.now(timezone.utc)

def carimbo() -> str:
    return agora().isoformat()

def _iso(momento: datetime | None) -> str | None:
    return momento.isoformat() if momento else None

def _ler_data(valor: Any) -> datetime | None:
    if not valor:
        return None
    try:
        d = datetime.fromisoformat(str(valor))
    except (TypeError, ValueError):
        logger.warning("Data de assinatura ilegível no banco: %r", valor)
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)

def _linha(conn: db.Connection, user_id: int) -> db.Row | None:
    return conn.execute(
        "SELECT * FROM subscriptions WHERE user_id = ?", (user_id,)
    ).fetchone()

def _criar(conn: db.Connection, user_id: int) -> db.Row:
    momento = _iso(agora())
    conn.execute(
        """INSERT INTO subscriptions
               (user_id, plan, status, trial_ends_at, current_period_end,
                centavos, created_at, updated_at)
           VALUES (?, ?, ?, NULL, NULL, 0, ?, ?)
           ON CONFLICT(user_id) DO NOTHING""",
        (user_id, plans.PADRAO, GRATUITO, momento, momento),
    )
    linha = _linha(conn, user_id)
    assert linha is not None
    return linha

def garantir(conn: db.Connection, user_id: int) -> None:
    _criar(conn, user_id)

class TesteIndisponivel(Exception):
    pass

def ativar_teste(conn: db.Connection, user_id: int) -> dict[str, Any]:

    if config.paywall_ativo():
        raise TesteIndisponivel("O teste gratuito não está mais disponível.")

    linha = _linha(conn, user_id) or _criar(conn, user_id)
    estado = _resolver(conn, linha)

    if linha["trial_ends_at"]:
        raise TesteIndisponivel("O teste de 14 dias já foi usado nesta conta.")
    if estado["vigente"]:
        raise TesteIndisponivel("Esta conta já tem o Pro liberado.")

    fim = agora() + timedelta(days=plans.DIAS_DE_TESTE)
    _tocar(
        conn,
        user_id,
        {
            "status": TRIAL,
            "plan": plans.PLANO_DO_TESTE,
            "trial_ends_at": _iso(fim),
            "current_period_end": _iso(fim),
            "centavos": 0,
        },
    )
    logger.info("Teste de %s dias ativado na conta %s.", plans.DIAS_DE_TESTE, user_id)
    linha = _linha(conn, user_id)
    assert linha is not None
    return _resolver(conn, linha)

def _resolver(conn: db.Connection, linha: db.Row) -> dict[str, Any]:
    momento = agora()
    status = str(linha["status"] or VENCIDA)
    plano_contratado = str(linha["plan"] or plans.PADRAO)
    fim = _ler_data(linha["current_period_end"])
    fim_trial = _ler_data(linha["trial_ends_at"])

    vigente = status in ESTADOS_COM_ACESSO and fim is not None and fim > momento
    if status in ESTADOS_COM_ACESSO and not vigente:

        status = GRATUITO if status == TRIAL else VENCIDA

    plano = plano_contratado if vigente else plans.PADRAO

    dias = None
    if status == TRIAL and fim_trial and fim_trial > momento:

        falta = fim_trial - momento
        dias = falta.days + (1 if falta.seconds else 0)

    return {
        "plano": plano,
        "plano_contratado": plano_contratado,
        "status": status,
        "vigente": vigente,

        "tem_acesso": vigente,
        "em_trial": status == TRIAL and vigente,
        "dias_de_trial": dias,

        "pode_testar": (
            linha["trial_ends_at"] is None and not vigente and not config.paywall_ativo()
        ),
        "dias_do_teste": plans.DIAS_DE_TESTE,
        "trial_ends_at": linha["trial_ends_at"],
        "current_period_end": linha["current_period_end"],
        "modo": str(linha["modo"] or ""),
        "provider": str(linha["provider"] or ""),
        "cancela_no_fim": bool(linha["cancel_at_period_end"]),
        "centavos": int(linha["centavos"] or 0),
    }

def _estado_cortesia() -> dict[str, Any]:
    return {
        "plano": plans.PRO,
        "plano_contratado": plans.PRO,
        "status": ATIVA,
        "vigente": True,
        "tem_acesso": True,
        "em_trial": False,
        "dias_de_trial": None,
        "pode_testar": False,
        "dias_do_teste": plans.DIAS_DE_TESTE,
        "trial_ends_at": None,
        "current_period_end": None,
        "modo": "cortesia",
        "provider": "",
        "cancela_no_fim": False,
        "centavos": 0,
        "cortesia": True,
    }

def is_cortesia(conn: db.Connection, user_id: int) -> bool:
    row = conn.execute("SELECT email FROM users WHERE id = ?", (user_id,)).fetchone()
    return bool(row) and config.is_comp_pro(row["email"])

def assinatura(user_id: int) -> dict[str, Any]:
    with db.get_conn() as conn:
        if is_cortesia(conn, user_id):
            return _estado_cortesia()
        linha = _linha(conn, user_id) or _criar(conn, user_id)
        return _resolver(conn, linha)

def assinatura_conn(conn: db.Connection, user_id: int) -> dict[str, Any]:
    if is_cortesia(conn, user_id):
        return _estado_cortesia()
    linha = _linha(conn, user_id) or _criar(conn, user_id)
    return _resolver(conn, linha)

def estado_efetivo(linha: Any) -> dict[str, Any]:
    return _resolver(None, linha)  # type: ignore[arg-type]

def pode(user_id: int, recurso: str) -> bool:
    estado = assinatura(user_id)
    return plans.obter(estado["plano"]).libera(recurso)

def exigir(user_id: int, recurso: str) -> None:
    estado = assinatura(user_id)
    if not plans.obter(estado["plano"]).libera(recurso):
        raise SemAcesso(recurso, estado["plano"])

def limite(user_id: int, chave: str) -> int:
    return plans.obter(assinatura(user_id)["plano"]).limite(chave)

def _tocar(conn: db.Connection, user_id: int, campos: dict[str, Any]) -> None:
    campos = dict(campos)
    campos["updated_at"] = _iso(agora())
    colunas = ", ".join(f"{k} = ?" for k in campos)
    conn.execute(
        f"UPDATE subscriptions SET {colunas} WHERE user_id = ?",
        (*campos.values(), user_id),
    )

def marcar_pendente(
    conn: db.Connection, user_id: int, plano: str, modo: str, provider: str, ref: str
) -> None:
    estado = assinatura_conn(conn, user_id)
    campos: dict[str, Any] = {
        "modo": modo,
        "provider": provider,
        "provider_ref": ref,
        "cancel_at_period_end": 0,
    }
    if not estado["vigente"]:
        campos["status"] = PENDENTE
        campos["plan"] = plano
        campos["centavos"] = plans.obter(plano).centavos
    _tocar(conn, user_id, campos)

def ativar(
    conn: db.Connection,
    user_id: int,
    plano: str,
    ate: datetime,
    provider: str = "",
    ref: str = "",
    modo: str = "",
    centavos: int | None = None,
) -> None:
    campos: dict[str, Any] = {
        "status": ATIVA,
        "plan": plano,
        "current_period_end": _iso(ate),
        "cancel_at_period_end": 0,
    }
    if provider:
        campos["provider"] = provider
    if ref:
        campos["provider_ref"] = ref
    if modo:
        campos["modo"] = modo
    campos["centavos"] = plans.obter(plano).centavos if centavos is None else centavos
    _criar(conn, user_id)
    _tocar(conn, user_id, campos)
    logger.info("Assinatura ativa: conta %s, plano %s, até %s", user_id, plano, ate)

def vencer(conn: db.Connection, user_id: int, motivo: str = "") -> None:
    _tocar(conn, user_id, {"status": VENCIDA})
    logger.info("Assinatura vencida: conta %s (%s)", user_id, motivo or "sem motivo")

def cancelar(conn: db.Connection, user_id: int, imediato: bool = False) -> None:
    if imediato:
        _tocar(
            conn,
            user_id,
            {"status": CANCELADA, "current_period_end": _iso(agora()), "cancel_at_period_end": 1},
        )
    else:
        _tocar(conn, user_id, {"cancel_at_period_end": 1})
    logger.info("Assinatura cancelada: conta %s (imediato=%s)", user_id, imediato)

def registrar_fatura(
    conn: db.Connection,
    user_id: int,
    provider: str,
    ref: str,
    plano: str,
    centavos: int,
    status: str,
    metodo: str = "",
    periodo_ate: datetime | None = None,
    pago_em: datetime | None = None,
) -> bool:
    cur = conn.execute(
        """INSERT OR IGNORE INTO invoices
               (user_id, provider, provider_ref, plan, centavos, currency,
                status, metodo, periodo_ate, paid_at, created_at)
           VALUES (?, ?, ?, ?, ?, 'BRL', ?, ?, ?, ?, ?)""",
        (
            user_id,
            provider,
            ref,
            plano,
            int(centavos),
            status,
            metodo,
            _iso(periodo_ate),
            _iso(pago_em),
            _iso(agora()),
        ),
    )
    return cur.rowcount > 0

def faturas(user_id: int, limite_linhas: int = 24) -> list[dict[str, Any]]:
    with db.get_conn() as conn:
        linhas = conn.execute(
            """SELECT provider_ref, plan, centavos, currency, status, metodo,
                      periodo_ate, paid_at, created_at
                 FROM invoices
                WHERE user_id = ?
             ORDER BY datetime(created_at) DESC
                LIMIT ?""",
            (user_id, int(limite_linhas)),
        ).fetchall()
    return [
        {
            "referencia": r["provider_ref"],
            "plano": r["plan"],
            "centavos": int(r["centavos"] or 0),
            "valor": int(r["centavos"] or 0) / 100,
            "moeda": r["currency"],
            "status": r["status"],
            "metodo": r["metodo"],
            "periodo_ate": r["periodo_ate"],
            "pago_em": r["paid_at"],
            "criado_em": r["created_at"],
        }
        for r in linhas
    ]

def dono_da_referencia(conn: db.Connection, provider: str, ref: str) -> int | None:
    linha = conn.execute(
        "SELECT user_id FROM subscriptions WHERE provider = ? AND provider_ref = ?",
        (provider, ref),
    ).fetchone()
    return int(linha["user_id"]) if linha else None

def vencer_expirados() -> int:
    momento = _iso(agora())
    with db.get_conn() as conn:

        cur = conn.execute(
            """UPDATE subscriptions
                  SET status = ?, updated_at = ?
                WHERE status = ?
                  AND current_period_end IS NOT NULL
                  AND datetime(current_period_end) <= datetime(?)""",
            (VENCIDA, momento, ATIVA, momento),
        )
        n = cur.rowcount or 0

        cur = conn.execute(
            """UPDATE subscriptions
                  SET status = ?, updated_at = ?
                WHERE status = ?
                  AND current_period_end IS NOT NULL
                  AND datetime(current_period_end) <= datetime(?)""",
            (GRATUITO, momento, TRIAL, momento),
        )
        n += cur.rowcount or 0
    if n:
        logger.info("%s assinatura(s) marcada(s) como vencida(s).", n)
    return n
