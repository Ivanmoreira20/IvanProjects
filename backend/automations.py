from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any, Callable

import activities
import db

logger = logging.getLogger("vertex.automations")

MAX_DEPTH = 2
MAX_ACTIONS = 8
MAX_CONDITIONS = 8

LIVE_EVENTS: dict[str, str] = {
    "lead.criado": "Lead criado",
    "lead.etapa": "Mudança de etapa",
    "lead.ganho": "Negócio ganho",
    "lead.perdido": "Negócio perdido",
    "proposta.criada": "Proposta criada",
    "proposta.enviada": "Proposta enviada",
    "proposta.visualizada": "Proposta visualizada",
    "proposta.aceita": "Proposta aceita",
    "proposta.recusada": "Proposta recusada",
    "atividade.concluida": "Atividade concluída",
    "whatsapp.recebido": "Mensagem recebida no WhatsApp",
}

TIME_EVENTS: dict[str, str] = {
    "lead.sem_interacao": "Lead sem interação",
    "lead.parado_etapa": "Negócio parado na etapa",
    "tarefa.vencida": "Tarefa vencida",
    "proposta.vencendo": "Proposta perto de vencer",
}

EVENTS: dict[str, str] = {**LIVE_EVENTS, **TIME_EVENTS}

CONDITION_FIELDS: dict[str, str] = {
    "status": "texto",
    "segment": "texto",
    "company": "texto",
    "source": "texto",
    "owner": "texto",
    "tag": "texto",
    "value": "numero",
    "dias_sem_interacao": "numero",
    "dias_na_etapa": "numero",
    "tem_whatsapp": "sim_nao",
}

OPERATORS: tuple[str, ...] = ("igual", "diferente", "contem", "nao_contem", "maior", "menor")

ACTION_TYPES: dict[str, str] = {
    "criar_tarefa": "Criar tarefa",
    "criar_followup": "Criar follow-up",
    "mudar_etapa": "Mudar etapa",
    "alterar_responsavel": "Alterar responsável",
    "adicionar_tag": "Adicionar tag",
    "remover_tag": "Remover tag",
    "notificar": "Enviar notificação",
    "registrar_atividade": "Registrar atividade",
    "atualizar_dado": "Atualizar dado do lead",
    "enviar_whatsapp": "Enviar mensagem no WhatsApp",
}

UPDATABLE_FIELDS: tuple[str, ...] = ("segment", "source", "owner", "notes")

def _dias(carimbo: Any, agora) -> int:
    momento = db.try_parse_iso(carimbo)
    if momento is None:
        return 0
    return max(0, (agora - momento).days)

def _valor_do_campo(campo: str, lead: dict, agora) -> Any:

    if campo == "dias_sem_interacao":
        return _dias(lead.get("last_activity_at") or lead.get("created_at"), agora)
    if campo == "dias_na_etapa":
        return _dias(lead.get("stage_changed_at") or lead.get("created_at"), agora)
    if campo == "tem_whatsapp":
        return bool(str(lead.get("whatsapp") or "").strip())
    if campo == "tag":
        return db.json_load(lead.get("tags"), [])
    return lead.get(campo)

def _compara(operador: str, atual: Any, esperado: Any) -> bool:
    if isinstance(atual, list):
        alvo = db.deburr(esperado)
        presentes = {db.deburr(t) for t in atual}
        if operador in ("igual", "contem"):
            return alvo in presentes
        if operador in ("diferente", "nao_contem"):
            return alvo not in presentes
        return False

    if isinstance(atual, bool):
        querido = str(esperado).strip().lower() in ("1", "true", "sim", "yes")
        return atual == querido if operador in ("igual", "contem") else atual != querido

    if isinstance(atual, (int, float)) and not isinstance(atual, bool):
        try:
            numero = float(str(esperado).replace(",", "."))
        except (TypeError, ValueError):
            return False
        if operador == "maior":
            return float(atual) > numero
        if operador == "menor":
            return float(atual) < numero
        if operador == "igual":
            return float(atual) == numero
        if operador == "diferente":
            return float(atual) != numero
        return False

    texto = db.deburr(atual)
    alvo = db.deburr(esperado)
    if operador == "igual":
        return texto == alvo
    if operador == "diferente":
        return texto != alvo
    if operador == "contem":
        return alvo in texto
    if operador == "nao_contem":
        return alvo not in texto
    return False

def condicoes_batem(condicoes: list[dict], lead: dict, agora) -> bool:
    for condicao in condicoes:
        campo = str(condicao.get("campo") or "")
        if campo not in CONDITION_FIELDS:
            return False
        operador = str(condicao.get("operador") or "igual")
        if operador not in OPERATORS:
            return False
        if not _compara(operador, _valor_do_campo(campo, lead, agora), condicao.get("valor")):
            return False
    return True

class ActionError(RuntimeError):
    pass

def _lead_atual(conn: db.Connection, user_id: int, lead_id: int) -> dict | None:
    linha = conn.execute(
        "SELECT * FROM leads WHERE id = ? AND user_id = ?", (lead_id, user_id)
    ).fetchone()
    return dict(linha) if linha else None

def _acao_criar_tarefa(ctx: "Execucao", acao: dict) -> str:
    titulo = str(acao.get("titulo") or "Tarefa automática").strip() or "Tarefa automática"
    try:
        dias = max(0, min(365, int(acao.get("dias") or 1)))
    except (TypeError, ValueError):
        dias = 1
    vence = db.iso(db.utcnow() + timedelta(days=dias))
    activities.log(
        ctx.conn,
        ctx.user_id,
        lead_id=ctx.lead_id,
        kind="tarefa",
        title=titulo,
        detail=f"Criada pela automação “{ctx.nome}”.",
        source="automation",
        due_at=vence,
    )
    return f"tarefa “{titulo}” para daqui a {dias} dia(s)"

def _acao_criar_followup(ctx: "Execucao", acao: dict) -> str:
    titulo = str(acao.get("titulo") or "Fazer follow-up").strip() or "Fazer follow-up"
    vence = db.iso(db.utcnow() + timedelta(days=max(0, int(acao.get("dias") or 0) or 0)))
    activities.log(
        ctx.conn,
        ctx.user_id,
        lead_id=ctx.lead_id,
        kind="tarefa",
        title=titulo,
        detail=f"Follow-up criado pela automação “{ctx.nome}”.",
        source="automation",
        due_at=vence,
    )
    activities.notify(
        ctx.conn,
        ctx.user_id,
        type="followup",
        title=f"Follow-up: {ctx.lead_nome}",
        body=titulo,
        severity="alerta",
        ref_type="lead",
        ref_id=ctx.lead_id,
        dedup_key=f"fup:{ctx.lead_id}:{db.now_iso()[:10]}",
    )
    return "follow-up criado"

def _acao_mudar_etapa(ctx: "Execucao", acao: dict) -> str:
    import crm

    novo = str(acao.get("status") or "").strip()
    if novo not in db.STATUSES:
        raise ActionError(f"etapa desconhecida: {novo!r}")
    if novo == "Perdido":

        raise ActionError("automação não pode marcar como Perdido: o motivo é obrigatório")
    atual = _lead_atual(ctx.conn, ctx.user_id, ctx.lead_id or 0)
    if atual is None:
        raise ActionError("lead não encontrado")
    if atual["status"] == novo:
        return f"etapa já era {novo}"

    eventos = crm.change_status(
        ctx.conn, ctx.user_id, ctx.lead_id or 0, novo,
        origem="automation", detalhe=f"Movido pela automação “{ctx.nome}”.",
    )
    ctx.derivados.extend(eventos)
    return f"etapa alterada para {novo}"

def _acao_responsavel(ctx: "Execucao", acao: dict) -> str:
    nome = str(acao.get("valor") or "").strip()[:80]
    ctx.conn.execute(
        "UPDATE leads SET owner = ?, updated_at = ? WHERE id = ? AND user_id = ?",
        (nome, db.now_iso(), ctx.lead_id, ctx.user_id),
    )
    activities.log(
        ctx.conn, ctx.user_id, lead_id=ctx.lead_id, kind="automacao",
        title=f"Responsável definido: {nome or '—'}", source="automation",
    )
    return f"responsável = {nome or '—'}"

def _mexe_tag(ctx: "Execucao", acao: dict, *, adicionar: bool) -> str:
    tag = str(acao.get("valor") or "").strip()[:40]
    if not tag:
        raise ActionError("tag vazia")
    atual = _lead_atual(ctx.conn, ctx.user_id, ctx.lead_id or 0)
    if atual is None:
        raise ActionError("lead não encontrado")
    tags = [str(t) for t in db.json_load(atual["tags"], [])]
    chaves = {db.deburr(t) for t in tags}
    if adicionar:
        if db.deburr(tag) in chaves:
            return f"tag “{tag}” já existia"
        tags.append(tag)
    else:
        if db.deburr(tag) not in chaves:
            return f"tag “{tag}” não estava no lead"
        tags = [t for t in tags if db.deburr(t) != db.deburr(tag)]
    ctx.conn.execute(
        "UPDATE leads SET tags = ?, updated_at = ? WHERE id = ? AND user_id = ?",
        (db.json_dump(tags[:20]), db.now_iso(), ctx.lead_id, ctx.user_id),
    )
    return f"tag “{tag}” {'adicionada' if adicionar else 'removida'}"

def _acao_notificar(ctx: "Execucao", acao: dict) -> str:
    titulo = str(acao.get("titulo") or ctx.nome).strip()
    texto = str(acao.get("texto") or "").strip()
    activities.notify(
        ctx.conn,
        ctx.user_id,
        type="automacao",
        title=titulo,
        body=texto or f"Disparada por “{ctx.nome}” em {ctx.lead_nome}.",
        severity="info",
        ref_type="lead" if ctx.lead_id else "",
        ref_id=ctx.lead_id,

        dedup_key=f"auto:{ctx.automation_id}:{ctx.lead_id}:{db.now_iso()[:10]}",
    )
    return "notificação enviada"

def _acao_registrar(ctx: "Execucao", acao: dict) -> str:
    titulo = str(acao.get("titulo") or "Registro automático").strip()
    activities.log(
        ctx.conn, ctx.user_id, lead_id=ctx.lead_id, kind="automacao",
        title=titulo, detail=str(acao.get("texto") or ""), source="automation",
    )
    return "atividade registrada"

def _acao_atualizar(ctx: "Execucao", acao: dict) -> str:
    campo = str(acao.get("campo") or "")
    if campo not in UPDATABLE_FIELDS:
        raise ActionError(f"campo não editável por automação: {campo!r}")
    valor = str(acao.get("valor") or "").strip()[:500]
    if campo == "segment" and valor not in db.SEGMENTS:
        raise ActionError(f"segmento desconhecido: {valor!r}")

    ctx.conn.execute(
        f"UPDATE leads SET {campo} = ?, updated_at = ? WHERE id = ? AND user_id = ?",
        (valor, db.now_iso(), ctx.lead_id, ctx.user_id),
    )
    return f"{campo} = {valor or '—'}"

def _acao_whatsapp(ctx: "Execucao", acao: dict) -> str:
    import whatsapp

    atual = _lead_atual(ctx.conn, ctx.user_id, ctx.lead_id or 0)
    if atual is None:
        raise ActionError("lead não encontrado")
    numero = str(atual["whatsapp"] or atual["phone"] or "").strip()
    if not numero:
        raise ActionError("o lead não tem número de WhatsApp")

    resultado = whatsapp.send_message(
        ctx.conn,
        ctx.user_id,
        lead_id=ctx.lead_id,
        phone=numero,
        body=str(acao.get("texto") or ""),
        template_name=str(acao.get("template") or ""),
        source="automation",
    )
    if not resultado["ok"]:
        raise ActionError(resultado["error"])
    return f"mensagem enviada para {resultado['phone']}"

ACTION_HANDLERS: dict[str, Callable[["Execucao", dict], str]] = {
    "criar_tarefa": _acao_criar_tarefa,
    "criar_followup": _acao_criar_followup,
    "mudar_etapa": _acao_mudar_etapa,
    "alterar_responsavel": _acao_responsavel,
    "adicionar_tag": lambda ctx, a: _mexe_tag(ctx, a, adicionar=True),
    "remover_tag": lambda ctx, a: _mexe_tag(ctx, a, adicionar=False),
    "notificar": _acao_notificar,
    "registrar_atividade": _acao_registrar,
    "atualizar_dado": _acao_atualizar,
    "enviar_whatsapp": _acao_whatsapp,
}

class Execucao:

    def __init__(
        self,
        conn: db.Connection,
        user_id: int,
        automation_id: int,
        nome: str,
        lead_id: int | None,
        lead_nome: str,
    ) -> None:
        self.conn = conn
        self.user_id = user_id
        self.automation_id = automation_id
        self.nome = nome
        self.lead_id = lead_id
        self.lead_nome = lead_nome or "—"
        self.derivados: list[tuple[str, dict]] = []

def dispatch(
    conn: db.Connection,
    user_id: int,
    event: str,
    *,
    lead: dict | None = None,
    dedup_key: str = "",
    chain: set[int] | None = None,
    depth: int = 0,
) -> list[dict]:
    if event not in EVENTS or depth > MAX_DEPTH:
        return []

    chain = set() if chain is None else chain
    agora = db.utcnow()
    executadas: list[dict] = []

    try:
        regras = conn.execute(
            """SELECT id, name, conditions, actions FROM automations
                WHERE user_id = ? AND event = ? AND active = 1
             ORDER BY id""",
            (user_id, event),
        ).fetchall()
    except Exception:  # pragma: no cover - tabela ausente em banco muito antigo
        logger.exception("Não foi possível ler as automações.")
        return []

    for regra in regras:
        automation_id = int(regra["id"])
        if automation_id in chain:
            continue

        condicoes = db.json_load(regra["conditions"], [])
        acoes = db.json_load(regra["actions"], [])
        if lead is not None and not condicoes_batem(condicoes, lead, agora):
            continue
        if not acoes:
            continue

        lead_id = int(lead["id"]) if lead else None
        lead_nome = str(lead.get("name") if lead else "") or ""

        run_id: int | None = None
        if dedup_key:
            cur = conn.execute(
                """INSERT OR IGNORE INTO automation_runs
                       (user_id, automation_id, automation_name, lead_id, lead_name,
                        event, summary, status, error, dedup_key, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, '', 'ok', '', ?, ?)""",
                (user_id, automation_id, regra["name"], lead_id, lead_nome,
                 event, dedup_key, db.now_iso()),
            )
            if not cur.rowcount:
                continue
            run_id = int(cur.lastrowid)

        ctx = Execucao(conn, user_id, automation_id, str(regra["name"]), lead_id, lead_nome)
        feitos: list[str] = []
        erros: list[str] = []

        for acao in acoes[:MAX_ACTIONS]:
            tipo = str(acao.get("tipo") or "")
            handler = ACTION_HANDLERS.get(tipo)
            if handler is None:
                erros.append(f"ação desconhecida: {tipo}")
                continue
            try:
                feitos.append(handler(ctx, acao))
            except ActionError as erro:
                erros.append(f"{ACTION_TYPES.get(tipo, tipo)}: {erro}")
            except Exception as erro:  # noqa: BLE001 - o motor nao pode cair
                logger.exception("Ação %s falhou na automação %s", tipo, automation_id)
                erros.append(f"{ACTION_TYPES.get(tipo, tipo)}: erro inesperado ({erro})")

        resumo = "; ".join(feitos) or "nenhuma ação concluída"
        estado = "ok" if not erros else ("parcial" if feitos else "erro")
        texto_erro = " | ".join(erros)[:500]

        if run_id is not None:
            conn.execute(
                "UPDATE automation_runs SET summary = ?, status = ?, error = ? WHERE id = ?",
                (resumo, estado, texto_erro, run_id),
            )
        else:
            conn.execute(
                """INSERT INTO automation_runs
                       (user_id, automation_id, automation_name, lead_id, lead_name,
                        event, summary, status, error, dedup_key, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, '', ?)""",
                (user_id, automation_id, regra["name"], lead_id, lead_nome,
                 event, resumo, estado, texto_erro, db.now_iso()),
            )

        conn.execute(
            "UPDATE automations SET run_count = run_count + 1, last_run_at = ? WHERE id = ? AND user_id = ?",
            (db.now_iso(), automation_id, user_id),
        )

        if erros:
            activities.notify(
                conn, user_id, type="automacao_erro",
                title=f"Automação “{regra['name']}” teve erro",
                body=texto_erro, severity="erro",
                ref_type="automation", ref_id=automation_id,
                dedup_key=f"autoerr:{automation_id}:{db.now_iso()[:10]}",
            )

        executadas.append({"id": automation_id, "name": regra["name"], "status": estado})

        proxima = chain | {automation_id}
        for derivado, _dados in ctx.derivados:
            atualizado = _lead_atual(conn, user_id, lead_id) if lead_id else None
            executadas.extend(
                dispatch(conn, user_id, derivado, lead=atualizado,
                         chain=proxima, depth=depth + 1)
            )

    return executadas

def validate_conditions(condicoes: list[dict]) -> list[dict]:
    if len(condicoes) > MAX_CONDITIONS:
        raise ValueError(f"no máximo {MAX_CONDITIONS} condições por automação")
    limpas: list[dict] = []
    for condicao in condicoes:
        campo = str(condicao.get("campo") or "")
        operador = str(condicao.get("operador") or "igual")
        if campo not in CONDITION_FIELDS:
            raise ValueError(f"campo de condição desconhecido: {campo!r}")
        if operador not in OPERATORS:
            raise ValueError(f"operador desconhecido: {operador!r}")
        limpas.append(
            {"campo": campo, "operador": operador, "valor": str(condicao.get("valor") or "")[:120]}
        )
    return limpas

def validate_actions(acoes: list[dict]) -> list[dict]:
    if not acoes:
        raise ValueError("a automação precisa de pelo menos uma ação")
    if len(acoes) > MAX_ACTIONS:
        raise ValueError(f"no máximo {MAX_ACTIONS} ações por automação")
    limpas: list[dict] = []
    for acao in acoes:
        tipo = str(acao.get("tipo") or "")
        if tipo not in ACTION_TYPES:
            raise ValueError(f"ação desconhecida: {tipo!r}")
        limpa: dict[str, Any] = {"tipo": tipo}
        for chave in ("titulo", "texto", "valor", "status", "campo", "template"):
            if chave in acao and acao[chave] is not None:
                limpa[chave] = str(acao[chave])[:500]
        if "dias" in acao:
            try:
                limpa["dias"] = max(0, min(365, int(acao["dias"])))
            except (TypeError, ValueError):
                limpa["dias"] = 1
        if tipo == "mudar_etapa":
            if limpa.get("status") not in db.STATUSES:
                raise ValueError("escolha uma etapa válida para a ação de mudar etapa")
            if limpa.get("status") == "Perdido":
                raise ValueError(
                    "uma automação não pode marcar um negócio como Perdido: "
                    "o motivo da perda precisa ser informado por uma pessoa"
                )
        if tipo == "atualizar_dado" and limpa.get("campo") not in UPDATABLE_FIELDS:
            raise ValueError(f"campo não editável por automação: {limpa.get('campo')!r}")
        limpas.append(limpa)
    return limpas

SEM_INTERACAO_DIAS = 7

def scan_user(conn: db.Connection, user_id: int) -> dict[str, int]:
    agora = db.utcnow()
    hoje = db.iso(agora)[:10]
    contagem = {"tarefas": 0, "expiradas": 0, "automacoes": 0, "avisos": 0}

    vencidas = conn.execute(
        """SELECT a.id, a.title, a.due_at, a.lead_id, COALESCE(l.name, '') AS lead_name
             FROM activities a
        LEFT JOIN leads l ON l.id = a.lead_id AND l.user_id = a.user_id
            WHERE a.user_id = ? AND a.due_at IS NOT NULL AND a.done_at IS NULL AND a.due_at <= ?
         ORDER BY a.due_at LIMIT 200""",
        (user_id, db.iso(agora)),
    ).fetchall()
    for tarefa in vencidas:
        criada = activities.notify(
            conn, user_id, type="tarefa_vencida",
            title=f"Tarefa vencida: {tarefa['title']}",
            body=tarefa["lead_name"] and f"Lead: {tarefa['lead_name']}" or "",
            severity="alerta", ref_type="lead", ref_id=tarefa["lead_id"],
            dedup_key=f"tarefa:{tarefa['id']}:{hoje}",
        )
        if criada:
            contagem["avisos"] += 1
        if tarefa["lead_id"]:
            lead = _lead_atual(conn, user_id, int(tarefa["lead_id"]))
            if lead:
                contagem["tarefas"] += 1
                contagem["automacoes"] += len(
                    dispatch(conn, user_id, "tarefa.vencida", lead=lead,
                             dedup_key=f"tarefa:{tarefa['id']}:{hoje}")
                )

    expiradas = conn.execute(
        """SELECT id, title, lead_id FROM proposals
            WHERE user_id = ? AND valid_until IS NOT NULL AND valid_until <= ?
              AND status IN ('Enviada', 'Visualizada')""",
        (user_id, db.iso(agora)),
    ).fetchall()
    for proposta in expiradas:
        conn.execute(
            "UPDATE proposals SET status = 'Expirada', updated_at = ? WHERE id = ? AND user_id = ?",
            (db.now_iso(), proposta["id"], user_id),
        )
        activities.log(
            conn, user_id, lead_id=proposta["lead_id"], kind="proposta",
            title=f"Proposta “{proposta['title']}” expirou", source="system",
            ref_type="proposal", ref_id=int(proposta["id"]),
        )
        activities.notify(
            conn, user_id, type="proposta_expirada",
            title=f"Proposta expirada: {proposta['title']}",
            severity="alerta", ref_type="proposal", ref_id=int(proposta["id"]),
            dedup_key=f"propexp:{proposta['id']}",
        )
        contagem["expiradas"] += 1

    corte = db.iso(agora - timedelta(days=SEM_INTERACAO_DIAS))
    parados = conn.execute(
        """SELECT * FROM leads
            WHERE user_id = ? AND status NOT IN ('Ganho', 'Perdido')
              AND COALESCE(last_activity_at, created_at) <= ?
         ORDER BY value DESC LIMIT 200""",
        (user_id, corte),
    ).fetchall()
    for linha in parados:
        lead = dict(linha)
        for evento in ("lead.sem_interacao", "lead.parado_etapa"):
            contagem["automacoes"] += len(
                dispatch(conn, user_id, evento, lead=lead,
                         dedup_key=f"{evento}:{lead['id']}:{hoje}")
            )

    return contagem

def scan_all() -> dict[str, int]:
    total = {"contas": 0, "tarefas": 0, "expiradas": 0, "automacoes": 0, "avisos": 0}
    with db.get_conn() as conn:
        ids = [linha["id"] for linha in conn.execute("SELECT id FROM users")]

    for user_id in ids:
        try:
            with db.get_conn() as conn:
                parcial = scan_user(conn, int(user_id))
            total["contas"] += 1
            for chave, valor in parcial.items():
                total[chave] += valor
        except Exception:  # noqa: BLE001
            logger.exception("Varredura de automações falhou para a conta %s", user_id)
    return total
