from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any, Iterable

import db

logger = logging.getLogger("vertex.intel")

PESO_VALOR = 25
PESO_ETAPA = 25
PESO_RECENCIA = 20
PESO_ENGAJAMENTO = 15
PESO_PROPOSTA = 15

PESO_TOTAL = PESO_VALOR + PESO_ETAPA + PESO_RECENCIA + PESO_ENGAJAMENTO + PESO_PROPOSTA

ETAPA_FRACAO: dict[str, float] = {
    "Prospecção": 0.20,
    "Qualificação": 0.45,
    "Proposta": 0.78,
    "Negociação": 1.00,
}

PROPOSTA_FRACAO: dict[str, float] = {
    "Rascunho": 0.25,
    "Enviada": 0.60,
    "Visualizada": 1.00,
    "Aceita": 1.00,
    "Recusada": 0.0,
    "Expirada": 0.15,
}

BANDA_ALTA = 65
BANDA_MEDIA = 40

FAIXAS = ("alta", "media", "baixa")

DIAS_SEM_CONTATO: dict[str, int] = {
    "Prospecção": 10,
    "Qualificação": 10,
    "Proposta": 5,
    "Negociação": 4,
}

DIAS_PARADO_NA_ETAPA: dict[str, int] = {
    "Prospecção": 21,
    "Qualificação": 21,
    "Proposta": 12,
    "Negociação": 10,
}

DIAS_PROPOSTA_SEM_RESPOSTA = 4

DIAS_PROPOSTA_VENCENDO = 3

MIN_CONTATOS_PARA_QUEDA = 2

GRAVIDADE_ORDEM = {"alta": 0, "media": 1, "baixa": 2}

PROBABILIDADE_PADRAO: dict[str, float] = {
    "Prospecção": 0.10,
    "Qualificação": 0.25,
    "Proposta": 0.50,
    "Negociação": 0.70,
}

MIN_AMOSTRA_HISTORICO = 12

def _num(valor: Any, padrao: float = 0.0) -> float:
    try:
        return float(valor)
    except (TypeError, ValueError):
        return padrao

def _dias(carimbo: Any, agora) -> int | None:
    if not carimbo:
        return None
    momento = db.try_parse_iso(carimbo)
    if momento is None:
        return None
    return max(0, (agora - momento).days)

def _percentil(valores: list[float], fracao: float) -> float:
    if not valores:
        return 0.0
    ordenados = sorted(valores)
    if len(ordenados) == 1:
        return ordenados[0]
    posicao = fracao * (len(ordenados) - 1)
    baixo = int(posicao)
    alto = min(baixo + 1, len(ordenados) - 1)
    peso = posicao - baixo
    return ordenados[baixo] * (1 - peso) + ordenados[alto] * peso

def carregar(conn: db.Connection, user_id: int) -> dict[str, Any]:
    agora = db.utcnow()

    leads = conn.execute(
        """SELECT id, name, company, value, status, segment, source, owner,
                  tags, lost_reason, closed_at,
                  COALESCE(last_activity_at, created_at) AS ultimo_contato,
                  last_activity_at, stage_changed_at, created_at, updated_at,
                  score, score_band, score_at
             FROM leads
            WHERE user_id = ?""",
        (user_id,),
    ).fetchall()

    contatos = conn.execute(
        f"""SELECT lead_id,
                   SUM(CASE WHEN created_at >= datetime('now', '-30 days')
                            THEN 1 ELSE 0 END) AS recentes,
                   SUM(CASE WHEN created_at <  datetime('now', '-30 days')
                             AND created_at >= datetime('now', '-60 days')
                            THEN 1 ELSE 0 END) AS anteriores,
                   COUNT(*) AS total
              FROM activities
             WHERE user_id = ? AND lead_id IS NOT NULL
               AND kind IN ({','.join('?' * len(db.CONTACT_KINDS))})
          GROUP BY lead_id""",
        (user_id, *sorted(db.CONTACT_KINDS)),
    ).fetchall()

    propostas = conn.execute(
        """SELECT lead_id, status, valid_until, sent_at, viewed_at, total,
                  ROW_NUMBER() OVER (
                      PARTITION BY lead_id
                      ORDER BY CASE status
                                   WHEN 'Aceita'      THEN 1
                                   WHEN 'Visualizada' THEN 2
                                   WHEN 'Enviada'     THEN 3
                                   WHEN 'Expirada'    THEN 4
                                   WHEN 'Rascunho'    THEN 5
                                   ELSE 6
                               END, created_at DESC
                  ) AS ordem
             FROM proposals
            WHERE user_id = ? AND lead_id IS NOT NULL""",
        (user_id,),
    ).fetchall()

    tarefas = conn.execute(
        """SELECT lead_id,
                  SUM(CASE WHEN due_at < ? THEN 1 ELSE 0 END) AS atrasadas,
                  COUNT(*) AS abertas
             FROM activities
            WHERE user_id = ? AND lead_id IS NOT NULL
              AND due_at IS NOT NULL AND done_at IS NULL
         GROUP BY lead_id""",
        (db.iso(agora), user_id),
    ).fetchall()

    por_contato = {int(r["lead_id"]): r for r in contatos}
    por_proposta = {int(r["lead_id"]): r for r in propostas if int(r["ordem"]) == 1}
    por_tarefa = {int(r["lead_id"]): r for r in tarefas}

    abertos = [r for r in leads if r["status"] in db.OPEN_STATUSES]
    ganhos = [r for r in leads if r["status"] == "Ganho"]
    perdidos = [r for r in leads if r["status"] == "Perdido"]

    valores_abertos = [_num(r["value"]) for r in abertos if _num(r["value"]) > 0]
    decididos = len(ganhos) + len(perdidos)

    conta = {
        "total": len(leads),
        "abertos": len(abertos),
        "ganhos": len(ganhos),
        "perdidos": len(perdidos),
        "decididos": decididos,
        "taxa_ganho": (len(ganhos) / decididos) if decididos else 0.0,
        "valor_mediano": _percentil(valores_abertos, 0.50),
        "valor_p75": _percentil(valores_abertos, 0.75),
        "valor_aberto": round(sum(_num(r["value"]) for r in abertos), 2),
        "valor_ganho": round(sum(_num(r["value"]) for r in ganhos), 2),
    }

    enriquecidos = []
    for linha in leads:
        lid = int(linha["id"])
        c = por_contato.get(lid)
        p = por_proposta.get(lid)
        t = por_tarefa.get(lid)
        enriquecidos.append(
            {
                "id": lid,
                "name": linha["name"],
                "company": linha["company"],
                "value": round(_num(linha["value"]), 2),
                "status": linha["status"],
                "segment": linha["segment"],
                "source": linha["source"] or "",
                "owner": linha["owner"] or "",
                "lost_reason": linha["lost_reason"] or "",
                "closed_at": linha["closed_at"],
                "created_at": linha["created_at"],
                "score_gravado": linha["score"],
                "dias_sem_contato": _dias(linha["ultimo_contato"], agora),
                "teve_contato": bool(linha["last_activity_at"]),
                "dias_na_etapa": _dias(linha["stage_changed_at"] or linha["created_at"], agora),
                "contatos_total": int(c["total"]) if c else 0,
                "contatos_30d": int(c["recentes"]) if c else 0,
                "contatos_30d_anteriores": int(c["anteriores"]) if c else 0,
                "proposta_status": p["status"] if p else "",
                "proposta_total": round(_num(p["total"]), 2) if p else 0.0,
                "proposta_dias_enviada": _dias(p["sent_at"], agora) if p else None,
                "proposta_vista": bool(p["viewed_at"]) if p else False,
                "proposta_dias_para_vencer": (
                    -_dias(p["valid_until"], agora)
                    if p and p["valid_until"] and _dias(p["valid_until"], agora) is not None
                    else None
                ),
                "tarefas_atrasadas": int(t["atrasadas"]) if t else 0,
                "tarefas_abertas": int(t["abertas"]) if t else 0,
            }
        )

    return {"leads": enriquecidos, "conta": conta, "agora": agora}

def pontuar(lead: dict[str, Any], conta: dict[str, Any]) -> dict[str, Any]:
    if lead["status"] in db.CLOSED_STATUSES:
        return {"score": None, "banda": "fechado", "fatores": []}

    fatores: list[dict[str, Any]] = []

    valor = lead["value"]
    mediano = conta["valor_mediano"]
    p75 = conta["valor_p75"]

    if valor <= 0:
        pontos_valor, texto_valor = 0.0, "Sem valor informado."
    elif p75 <= 0 or mediano <= 0:

        pontos_valor = PESO_VALOR * 0.5
        texto_valor = "Ainda não há carteira suficiente para comparar o valor."
    elif valor >= p75:
        pontos_valor = float(PESO_VALOR)
        texto_valor = "Está entre os 25% maiores negócios abertos da sua carteira."
    elif valor >= mediano:

        faixa = max(p75 - mediano, 1.0)
        pontos_valor = PESO_VALOR * (0.6 + 0.4 * (valor - mediano) / faixa)
        texto_valor = "Está acima do valor mediano da sua carteira."
    else:
        pontos_valor = PESO_VALOR * 0.6 * (valor / max(mediano, 1.0))
        texto_valor = "Está abaixo do valor mediano da sua carteira."

    fatores.append(
        {"nome": "Valor", "pontos": round(pontos_valor, 1), "maximo": PESO_VALOR, "texto": texto_valor}
    )

    fracao_etapa = ETAPA_FRACAO.get(lead["status"], 0.2)
    pontos_etapa = PESO_ETAPA * fracao_etapa
    fatores.append(
        {
            "nome": "Etapa",
            "pontos": round(pontos_etapa, 1),
            "maximo": PESO_ETAPA,
            "texto": f"Está em {lead['status']}.",
        }
    )

    dias = lead["dias_sem_contato"]
    if dias is None:
        pontos_rec, texto_rec = 0.0, "Não há data de contato registrada."
    elif not lead["teve_contato"]:

        if dias <= 2:
            pontos_rec, texto_rec = PESO_RECENCIA * 0.7, "Entrou há pouco e ainda não foi contatado."
        elif dias <= 7:
            pontos_rec, texto_rec = PESO_RECENCIA * 0.4, f"Entrou há {dias} dias e nunca foi contatado."
        else:
            pontos_rec, texto_rec = 0.0, f"Nunca foi contatado, e já se passaram {dias} dias."
    elif dias <= 2:
        pontos_rec, texto_rec = float(PESO_RECENCIA), "Houve contato nos últimos 2 dias."
    elif dias <= 7:
        pontos_rec, texto_rec = PESO_RECENCIA * 0.75, f"Último contato há {dias} dias."
    elif dias <= 14:
        pontos_rec, texto_rec = PESO_RECENCIA * 0.40, f"Último contato há {dias} dias."
    elif dias <= 30:
        pontos_rec, texto_rec = PESO_RECENCIA * 0.15, f"Último contato há {dias} dias."
    else:
        pontos_rec, texto_rec = 0.0, f"Sem contato há {dias} dias."

    fatores.append(
        {"nome": "Contato recente", "pontos": round(pontos_rec, 1), "maximo": PESO_RECENCIA, "texto": texto_rec}
    )

    total_contatos = lead["contatos_total"]
    if total_contatos == 0:
        pontos_eng, texto_eng = 0.0, "Nenhuma conversa registrada."
    elif total_contatos == 1:
        pontos_eng, texto_eng = PESO_ENGAJAMENTO * 0.35, "1 conversa registrada."
    elif total_contatos <= 3:
        pontos_eng, texto_eng = PESO_ENGAJAMENTO * 0.7, f"{total_contatos} conversas registradas."
    else:
        pontos_eng, texto_eng = float(PESO_ENGAJAMENTO), f"{total_contatos} conversas registradas."

    fatores.append(
        {"nome": "Engajamento", "pontos": round(pontos_eng, 1), "maximo": PESO_ENGAJAMENTO, "texto": texto_eng}
    )

    estado = lead["proposta_status"]
    if not estado:
        pontos_prop, texto_prop = 0.0, "Ainda não há proposta."
    else:
        pontos_prop = PESO_PROPOSTA * PROPOSTA_FRACAO.get(estado, 0.3)
        if estado == "Visualizada":
            texto_prop = "O cliente abriu a proposta."
        elif estado == "Recusada":
            texto_prop = "A proposta foi recusada."
        else:
            texto_prop = f"Proposta {estado.lower()}."

    fatores.append(
        {"nome": "Proposta", "pontos": round(pontos_prop, 1), "maximo": PESO_PROPOSTA, "texto": texto_prop}
    )

    bruto = pontos_valor + pontos_etapa + pontos_rec + pontos_eng + pontos_prop
    score = int(round(max(0.0, min(float(PESO_TOTAL), bruto))))

    if score >= BANDA_ALTA:
        banda = "alta"
    elif score >= BANDA_MEDIA:
        banda = "media"
    else:
        banda = "baixa"

    return {"score": score, "banda": banda, "fatores": fatores}

def riscos(lead: dict[str, Any], conta: dict[str, Any]) -> list[dict[str, Any]]:
    if lead["status"] in db.CLOSED_STATUSES:
        return []

    achados: list[dict[str, Any]] = []
    status = lead["status"]

    def juntar(codigo: str, gravidade: str, texto: str) -> None:
        achados.append({"codigo": codigo, "gravidade": gravidade, "texto": texto})

    dias_prop = lead["proposta_dias_enviada"]
    if lead["proposta_status"] in ("Enviada", "Visualizada") and dias_prop is not None:
        if dias_prop >= DIAS_PROPOSTA_SEM_RESPOSTA:
            vista = "abriu, mas não respondeu" if lead["proposta_vista"] else "ainda não abriu"
            juntar(
                "proposta_sem_resposta",
                "alta",
                f"Proposta enviada há {dias_prop} dias e o cliente {vista}.",
            )

    faltam = lead["proposta_dias_para_vencer"]
    if (
        lead["proposta_status"] in ("Enviada", "Visualizada")
        and faltam is not None
        and 0 <= faltam <= DIAS_PROPOSTA_VENCENDO
    ):
        quando = "hoje" if faltam == 0 else f"em {faltam} dia{'s' if faltam != 1 else ''}"
        juntar("proposta_vencendo", "alta", f"A validade da proposta termina {quando}.")

    dias = lead["dias_sem_contato"]
    limite = DIAS_SEM_CONTATO.get(status)
    if dias is not None and limite is not None and dias >= limite:
        if lead["teve_contato"]:
            juntar("sem_interacao", "alta" if dias >= limite * 2 else "media",
                   f"Sem contato há {dias} dias, e em {status} o normal é retomar em {limite}.")
        else:
            juntar("nunca_contatado", "alta",
                   f"Entrou há {dias} dias e nunca foi contatado.")

    parado = lead["dias_na_etapa"]
    teto = DIAS_PARADO_NA_ETAPA.get(status)
    if parado is not None and teto is not None and parado >= teto:
        juntar("parado_na_etapa", "media", f"Está em {status} há {parado} dias sem avançar.")

    if lead["tarefas_atrasadas"] > 0:
        n = lead["tarefas_atrasadas"]
        juntar("tarefa_atrasada", "alta",
               f"{n} tarefa{'s' if n != 1 else ''} de acompanhamento venceu sem ser concluída."
               if n == 1 else
               f"{n} tarefas de acompanhamento venceram sem serem concluídas.")

    antes = lead["contatos_30d_anteriores"]
    agora_30 = lead["contatos_30d"]
    if antes >= MIN_CONTATOS_PARA_QUEDA and agora_30 * 2 <= antes:
        juntar("queda_de_atividade", "media",
               f"O ritmo caiu: {antes} contatos no mês anterior contra {agora_30} neste.")

    p75 = conta["valor_p75"]
    if (
        p75 > 0
        and lead["value"] >= p75
        and dias is not None
        and limite is not None
        and dias >= max(3, limite // 2)
    ):
        juntar("alto_valor_parado", "alta",
               f"É um dos maiores negócios abertos e está há {dias} dias sem contato.")

    achados.sort(key=lambda r: GRAVIDADE_ORDEM.get(r["gravidade"], 9))
    return achados

CADENCIA: dict[str, int] = {
    "Prospecção": 7,
    "Qualificação": 7,
    "Proposta": 3,
    "Negociação": 2,
}

def sugestao(lead: dict[str, Any], achados: list[dict[str, Any]], agora) -> dict[str, Any] | None:
    if lead["status"] in db.CLOSED_STATUSES:
        return None

    codigos = {r["codigo"] for r in achados}

    if "proposta_vencendo" in codigos:
        acao, quando, porque = (
            "revisar_proposta",
            0,
            "A proposta vence agora; ou renova o prazo, ou perde o argumento.",
        )
    elif "proposta_sem_resposta" in codigos:
        acao, quando, porque = (
            "ligar",
            0,
            "Proposta parada não se resolve por mensagem: ligação tem resposta no mesmo dia.",
        )
    elif "tarefa_atrasada" in codigos:
        acao, quando, porque = (
            "concluir_tarefa",
            0,
            "Já existe uma tarefa vencida neste negócio.",
        )
    elif "nunca_contatado" in codigos:
        acao, quando, porque = ("ligar", 0, "O lead entrou e ninguém falou com ele ainda.")
    elif "alto_valor_parado" in codigos:
        acao, quando, porque = (
            "ligar",
            0,
            "É um dos seus maiores negócios abertos e está sem movimento.",
        )
    elif "sem_interacao" in codigos:
        acao = "reuniao" if lead["status"] == "Negociação" else "mensagem"
        quando, porque = 0, "Passou do prazo normal de retomada desta etapa."
    elif "parado_na_etapa" in codigos or "queda_de_atividade" in codigos:
        acao, quando, porque = ("mensagem", 1, "O negócio perdeu ritmo e precisa de um empurrão.")
    else:

        dias = lead["dias_sem_contato"]
        cadencia = CADENCIA.get(lead["status"], 7)
        if dias is None:
            return None
        faltam = cadencia - dias
        if faltam > 2:
            return None
        acao, quando, porque = (
            "mensagem",
            max(0, faltam),
            f"O ritmo desta etapa é de um contato a cada {cadencia} dias.",
        )

    return {
        "acao": acao,
        "em_dias": quando,
        "quando": db.iso(agora + timedelta(days=quando)),
        "porque": porque,
    }

def probabilidades(conn: db.Connection, user_id: int) -> dict[str, Any]:
    decididos = conn.execute(
        """SELECT COUNT(*) AS n FROM leads
            WHERE user_id = ? AND status IN ('Ganho', 'Perdido')""",
        (user_id,),
    ).fetchone()["n"]

    if int(decididos or 0) < MIN_AMOSTRA_HISTORICO:
        return {
            "por_etapa": dict(PROBABILIDADE_PADRAO),
            "origem": "padrao",
            "amostra": int(decididos or 0),
            "minimo": MIN_AMOSTRA_HISTORICO,
        }

    linhas = conn.execute(
        """SELECT s.de AS etapa,
                  COUNT(DISTINCT s.lead_id) AS passaram,
                  COUNT(DISTINCT CASE WHEN l.status = 'Ganho' THEN s.lead_id END) AS ganhos
             FROM stage_events s
             JOIN leads l ON l.id = s.lead_id AND l.user_id = s.user_id
            WHERE s.user_id = ? AND l.status IN ('Ganho', 'Perdido')
         GROUP BY s.de""",
        (user_id,),
    ).fetchall()

    medido = {}
    for linha in linhas:
        etapa = linha["etapa"]
        passaram = int(linha["passaram"] or 0)
        if etapa in PROBABILIDADE_PADRAO and passaram >= MIN_AMOSTRA_HISTORICO:
            medido[etapa] = round(int(linha["ganhos"] or 0) / passaram, 4)

    if not medido:
        return {
            "por_etapa": dict(PROBABILIDADE_PADRAO),
            "origem": "padrao",
            "amostra": int(decididos or 0),
            "minimo": MIN_AMOSTRA_HISTORICO,
        }

    por_etapa = dict(PROBABILIDADE_PADRAO)
    por_etapa.update(medido)
    return {
        "por_etapa": por_etapa,
        "origem": "historico" if len(medido) == len(PROBABILIDADE_PADRAO) else "misto",
        "amostra": int(decididos or 0),
        "minimo": MIN_AMOSTRA_HISTORICO,
        "etapas_medidas": sorted(medido),
    }

def previsao(conn: db.Connection, user_id: int, dados: dict[str, Any]) -> dict[str, Any]:
    prob = probabilidades(conn, user_id)
    por_etapa = prob["por_etapa"]

    abertos = [l for l in dados["leads"] if l["status"] in db.OPEN_STATUSES]

    linhas = []
    for etapa in db.OPEN_STATUSES:
        desta = [l for l in abertos if l["status"] == etapa]
        valor = round(sum(l["value"] for l in desta), 2)
        p = float(por_etapa.get(etapa, 0.0))
        linhas.append(
            {
                "etapa": etapa,
                "negocios": len(desta),
                "valor": valor,
                "probabilidade": round(p, 4),
                "ponderado": round(valor * p, 2),
            }
        )

    conta = dados["conta"]
    return {
        "ganho": conta["valor_ganho"],
        "potencial": round(sum(l["valor"] for l in linhas), 2),
        "ponderado": round(sum(l["ponderado"] for l in linhas), 2),
        "linhas": linhas,
        "probabilidade_origem": prob["origem"],
        "amostra": prob["amostra"],
        "amostra_minima": prob["minimo"],
        "taxa_ganho_conta": round(conta["taxa_ganho"] * 100, 1),

        "aviso": (
            "Estimativa calculada sobre os negócios abertos hoje. "
            + (
                "As probabilidades vêm do histórico desta conta."
                if prob["origem"] != "padrao"
                else f"Ainda não há {prob['minimo']} negócios fechados nesta conta, "
                "então a estimativa usa a curva padrão do sistema."
            )
        ),
    }

def analisar(conn: db.Connection, user_id: int) -> dict[str, Any]:
    dados = carregar(conn, user_id)
    conta = dados["conta"]
    agora = dados["agora"]

    saida = []
    for lead in dados["leads"]:
        nota = pontuar(lead, conta)
        achados = riscos(lead, conta)
        saida.append(
            {
                **lead,
                "score": nota["score"],
                "banda": nota["banda"],
                "fatores": nota["fatores"],
                "riscos": achados,
                "sugestao": sugestao(lead, achados, agora),
            }
        )

    dados["leads"] = saida
    return dados

def gravar_scores(conn: db.Connection, user_id: int, leads: Iterable[dict[str, Any]]) -> int:
    agora = db.now_iso()
    linhas = [
        (lead["score"], lead["banda"], agora, lead["id"], user_id)
        for lead in leads
        if lead.get("score") is not None
    ]
    if not linhas:
        return 0
    conn.executemany(
        "UPDATE leads SET score = ?, score_band = ?, score_at = ? WHERE id = ? AND user_id = ?",
        linhas,
    )
    return len(linhas)

def recalcular(conn: db.Connection, user_id: int) -> int:
    dados = analisar(conn, user_id)
    return gravar_scores(conn, user_id, dados["leads"])

def recalcular_todos() -> dict[str, int]:
    contas = 0
    leads = 0
    with db.get_conn() as conn:
        usuarios = [int(r["id"]) for r in conn.execute("SELECT id FROM users")]
    for user_id in usuarios:
        try:
            with db.get_conn() as conn:
                leads += recalcular(conn, user_id)
            contas += 1
        except Exception:  # noqa: BLE001 - uma conta com dado estranho nao

            logger.exception("Falha ao recalcular a pontuação da conta %s", user_id)
    return {"contas": contas, "leads": leads}

MARCOS = (
    "primeiro_lead",
    "primeiro_negocio",
    "primeira_atividade",
    "primeiro_followup",
    "primeira_proposta",
    "primeira_automacao",
    "primeiro_ganho",
)

def marcar(conn: db.Connection, user_id: int, marco: str) -> None:
    if marco not in MARCOS:
        return
    conn.execute(
        "INSERT OR IGNORE INTO activation (user_id, marco, created_at) VALUES (?, ?, ?)",
        (user_id, marco, db.now_iso()),
    )

def ativacao(conn: db.Connection, user_id: int) -> dict[str, Any]:
    linhas = conn.execute(
        "SELECT marco, created_at FROM activation WHERE user_id = ?", (user_id,)
    ).fetchall()
    feitos = {linha["marco"]: linha["created_at"] for linha in linhas}
    return {
        "marcos": [{"marco": m, "em": feitos.get(m)} for m in MARCOS],
        "concluidos": len([m for m in MARCOS if m in feitos]),
        "total": len(MARCOS),
    }
