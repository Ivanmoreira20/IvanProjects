from __future__ import annotations

import json
import logging
import os
import sqlite3
import unicodedata
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

logger = logging.getLogger("vertex.db")

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_DB_PATH = BASE_DIR / "vertex.db"

OPEN_STATUSES: tuple[str, ...] = ("Prospecção", "Qualificação", "Proposta", "Negociação")
CLOSED_STATUSES: tuple[str, ...] = ("Ganho", "Perdido")
STATUSES: tuple[str, ...] = OPEN_STATUSES + CLOSED_STATUSES

STATUS_RENAMES: dict[str, str] = {"Fechado": "Ganho"}

SEGMENTS: tuple[str, ...] = (
    "SaaS",
    "Saúde",
    "Varejo",
    "Educação",
    "Indústria",
    "Finanças",
    "Serviços",
    "Outros",
)

ACTIVITY_KINDS: tuple[str, ...] = (
    "nota",
    "ligacao",
    "reuniao",
    "email",
    "whatsapp",
    "tarefa",
    "etapa",
    "proposta",
    "automacao",
    "criacao",
    "ganho",
    "perda",
)

CONTACT_KINDS: frozenset[str] = frozenset({"ligacao", "reuniao", "email", "whatsapp"})

USER_EDITABLE_KINDS: frozenset[str] = frozenset(
    {"nota", "ligacao", "reuniao", "email", "tarefa"}
)

PROPOSAL_STATUSES: tuple[str, ...] = (
    "Rascunho",
    "Enviada",
    "Visualizada",
    "Aceita",
    "Recusada",
    "Expirada",
)

DEFAULT_LOSS_REASONS: tuple[str, ...] = (
    "Preço",
    "Concorrente",
    "Sem orçamento",
    "Sem resposta",
    "Prazo",
    "Desistiu",
    "Outro",
)

CUSTOM_FIELD_TYPES: tuple[str, ...] = (
    "texto",
    "numero",
    "moeda",
    "data",
    "lista",
    "multipla",
    "sim_nao",
    "email",
    "telefone",
)

_ISO_FORMAT = "%Y-%m-%dT%H:%M:%S.%f+00:00"

def db_path() -> Path:
    override = os.environ.get("VERTEX_DB")
    return Path(override).expanduser().resolve() if override else DEFAULT_DB_PATH

def describe_backend() -> str:
    return f"banco: sqlite em arquivo ({db_path()})"

def utcnow() -> datetime:
    return datetime.now(timezone.utc)

def iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime(_ISO_FORMAT)

def now_iso() -> str:
    return iso(utcnow())

def parse_iso(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)

def try_parse_iso(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return parse_iso(str(value))
    except (TypeError, ValueError):
        return None

_COMBINING = dict.fromkeys(range(0x0300, 0x0370))

def deburr(value: Any) -> str:
    texto = unicodedata.normalize("NFD", str(value or ""))
    return texto.translate(_COMBINING).lower()

def json_load(raw: Any, fallback: Any) -> Any:
    if raw in (None, ""):
        return fallback
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        logger.warning("JSON inválido no banco; usando o valor padrão.")
        return fallback

def json_dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))

def _sql_list(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{v}'" for v in values)

_STATUS_SQL_LIST = _sql_list(STATUSES)
_SEGMENT_SQL_LIST = _sql_list(SEGMENTS)
_ACTIVITY_SQL_LIST = _sql_list(ACTIVITY_KINDS)
_PROPOSAL_SQL_LIST = _sql_list(PROPOSAL_STATUSES)
_CUSTOM_TYPE_SQL_LIST = _sql_list(CUSTOM_FIELD_TYPES)

LEADS_DDL = f"""
CREATE TABLE IF NOT EXISTS {{tabela}} (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id          INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name             TEXT NOT NULL,
    company          TEXT NOT NULL,
    value            REAL NOT NULL DEFAULT 0,
    status           TEXT NOT NULL CHECK (status IN ({_STATUS_SQL_LIST})),
    segment          TEXT NOT NULL CHECK (segment IN ({_SEGMENT_SQL_LIST})),
    email            TEXT NOT NULL DEFAULT '',
    phone            TEXT NOT NULL DEFAULT '',
    whatsapp         TEXT NOT NULL DEFAULT '',
    source           TEXT NOT NULL DEFAULT '',
    notes            TEXT NOT NULL DEFAULT '',
    -- `tags` guarda um array JSON. `owner` e texto livre: enquanto nao existe
    -- multiusuario, "responsavel" e um nome escrito, e nao uma conta. Quando
    -- houver equipe, esta coluna vira chave estrangeira -- e a acao de
    -- automacao "alterar responsavel" continua com a mesma cara.
    tags             TEXT NOT NULL DEFAULT '[]',
    owner            TEXT NOT NULL DEFAULT '',
    -- Responsavel de VERDADE pelo negocio, agora que existe equipe: aponta para
    -- o `users.id` do vendedor dono. NULL = "sem dono" (visivel a todos ate um
    -- admin/gestor distribuir). O `owner` (texto livre) acima continua existindo
    -- para exibicao/legado; `owner_user_id` e' quem manda na visibilidade.
    -- ON DELETE SET NULL: quando um membro sai, os leads dele viram "sem dono",
    -- nunca somem.
    owner_user_id    INTEGER REFERENCES users(id) ON DELETE SET NULL,
    lost_reason      TEXT NOT NULL DEFAULT '',
    lost_note        TEXT NOT NULL DEFAULT '',
    closed_at        TEXT,
    -- Os dois relogios do lead, e por que sao separados de `updated_at`:
    --   `last_activity_at`  sobe so quando ha CONTATO (db.CONTACT_KINDS);
    --   `stage_changed_at`  sobe so quando a ETAPA muda.
    -- `updated_at` sobe a cada edicao, inclusive corrigir um nome. Usa-lo para
    -- decidir "quem esta parado" fazia o alerta sumir sem que ninguem tivesse
    -- falado com o cliente.
    last_activity_at TEXT,
    stage_changed_at TEXT,
    -- Pontuacao comercial (Fase 3). Nasce NULA de proposito: "ainda nao
    -- calculado" e "prioridade zero" sao coisas diferentes, e a tela precisa
    -- poder distinguir as duas.
    score            INTEGER,
    score_band       TEXT NOT NULL DEFAULT '',
    score_at         TEXT,
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL
);
"""

SUBSCRIPTIONS_DDL = """CREATE TABLE IF NOT EXISTS subscriptions (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id              INTEGER NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
    plan                 TEXT NOT NULL DEFAULT 'inicial',
    -- gratuito  : no plano Inicial, sem prazo. E onde TODA conta comeca.
    -- ativa     : pago e em dia
    -- pendente  : assinatura criada, aguardando o primeiro pagamento
    -- vencida   : pagamento falhou ou o periodo acabou sem renovar
    -- cancelada : encerrada pelo cliente ou pelo provedor
    -- trial     : herdado. Nao e mais criado; sobrevive nas contas que ja o
    --             tinham quando o periodo de teste foi removido do produto.
    status               TEXT NOT NULL DEFAULT 'gratuito'
                         CHECK (status IN ('gratuito','ativa','pendente','vencida','cancelada','trial')),
    trial_ends_at        TEXT,
    -- Ate quando o acesso pago vale. E' o campo que decide liberar ou barrar;
    -- comparar com "agora" e' mais seguro que confiar num booleano gravado,
    -- que pode ficar velho se um webhook se perder.
    current_period_end   TEXT,
    provider             TEXT NOT NULL DEFAULT '',
    -- id da assinatura no provedor (preapproval_id do Mercado Pago)
    provider_ref         TEXT NOT NULL DEFAULT '',
    -- forma escolhida: 'cartao' (recorrente) ou 'avulso' (Pix/boleto mes a mes)
    modo                 TEXT NOT NULL DEFAULT '',
    cancel_at_period_end INTEGER NOT NULL DEFAULT 0,
    -- Valor em CENTAVOS travado no momento da assinatura. Reajuste de tabela
    -- nao pode mudar o que quem ja' assinou paga sem uma decisao explicita.
    centavos             INTEGER NOT NULL DEFAULT 0,
    created_at           TEXT NOT NULL,
    updated_at           TEXT NOT NULL
);"""

SCHEMA = f"""
CREATE TABLE IF NOT EXISTS users (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    email          TEXT NOT NULL COLLATE NOCASE UNIQUE,
    name           TEXT NOT NULL,
    password_hash  TEXT NOT NULL,
    created_at     TEXT NOT NULL,
    email_verified INTEGER NOT NULL DEFAULT 0,
    google_sub     TEXT,
    auth_provider  TEXT NOT NULL DEFAULT 'password',
    -- Foto de perfil. Guarda so a CHAVE aleatoria do arquivo, nunca o nome
    -- enviado pelo usuario nem o binario. Vazio = sem foto. A chave muda a
    -- cada troca, o que tambem resolve cache (a URL passa a ser outra).
    avatar_key     TEXT NOT NULL DEFAULT '',
    -- "ja dispensei a trilha de primeiros passos". E' preferencia de INTERFACE,
    -- por pessoa -- nao muda dado nenhum do CRM. Fica em `users` e nao numa
    -- tabela nova porque e' um bit por conta, e uma tabela para um bit custa
    -- mais em leitura do que economiza.
    onboarding_off INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS email_codes (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    code_hash  TEXT NOT NULL,
    purpose    TEXT NOT NULL DEFAULT 'verify_email',
    attempts   INTEGER NOT NULL DEFAULT 0,
    expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL
);

-- Pedido de troca de e-mail, aguardando confirmacao no endereco NOVO.
-- Uma linha por conta: pedir de novo substitui o anterior. Vence junto com o
-- codigo em `email_codes` -- aqui fica so' o destino pretendido.
CREATE TABLE IF NOT EXISTS email_changes (
    user_id    INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    novo_email TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash TEXT NOT NULL UNIQUE,
    csrf_hash  TEXT NOT NULL,
    remember   INTEGER NOT NULL DEFAULT 0,
    expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    -- Rotulo GROSSO do aparelho ("Chrome no Windows"), calculado na criacao.
    -- NAO guardamos o User-Agent inteiro nem o IP: para a pessoa reconhecer a
    -- propria sessao isto basta, e o que nao se guarda nao vaza.
    device       TEXT NOT NULL DEFAULT '',
    last_seen_at TEXT NOT NULL DEFAULT ''
);

{LEADS_DDL.format(tabela="leads")}

-- Historico. Toda funcionalidade da Fase 2 deposita aqui.
--
-- Tarefa e atividade sao a MESMA linha, distinguidas por `due_at`: uma
-- atividade com prazo e ainda sem `done_at` e uma tarefa em aberto. Sao a
-- mesma coisa contada de dois angulos ("o que combinei de fazer" e "o que
-- aconteceu"), e separa-las em duas tabelas obrigaria a costurar as duas
-- listas em toda tela que mostra a linha do tempo.
--
-- `source` distingue quem escreveu: pessoa, sistema, automacao ou WhatsApp.
-- E o que permite ao motor de automacoes ignorar os proprios rastros e nao
-- entrar em laco.
CREATE TABLE IF NOT EXISTS activities (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    lead_id    INTEGER REFERENCES leads(id) ON DELETE CASCADE,
    kind       TEXT NOT NULL CHECK (kind IN ({_ACTIVITY_SQL_LIST})),
    title      TEXT NOT NULL,
    detail     TEXT NOT NULL DEFAULT '',
    source     TEXT NOT NULL DEFAULT 'user'
               CHECK (source IN ('user', 'system', 'automation', 'whatsapp')),
    ref_type   TEXT NOT NULL DEFAULT '',
    ref_id     INTEGER,
    due_at     TEXT,
    done_at    TEXT,
    created_at TEXT NOT NULL
);

-- Notificacoes internas.
--
-- `dedup_key` existe para cumprir "nao gerar notificacoes excessivas ou
-- redundantes": o mesmo lead atrasado no mesmo dia produz UMA linha, porque o
-- indice unico parcial recusa a segunda. Sem isso, um agendador que roda de
-- 15 em 15 minutos criaria 96 avisos identicos por dia e a central viraria
-- ruido -- e ninguem age sobre ruido.
CREATE TABLE IF NOT EXISTS notifications (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    type       TEXT NOT NULL,
    title      TEXT NOT NULL,
    body       TEXT NOT NULL DEFAULT '',
    severity   TEXT NOT NULL DEFAULT 'info'
               CHECK (severity IN ('info', 'alerta', 'sucesso', 'erro')),
    ref_type   TEXT NOT NULL DEFAULT '',
    ref_id     INTEGER,
    dedup_key  TEXT NOT NULL DEFAULT '',
    read_at    TEXT,
    created_at TEXT NOT NULL
);

-- Motivos de perda configuraveis por conta.
--
-- O lead guarda o motivo como TEXTO, e nao como id: renomear ou desativar um
-- motivo nao pode reescrever a historia de um negocio que ja foi perdido.
CREATE TABLE IF NOT EXISTS loss_reasons (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    label      TEXT NOT NULL,
    position   INTEGER NOT NULL DEFAULT 0,
    active     INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    UNIQUE (user_id, label)
);

-- Propostas comerciais.
--
-- `public_token` e a credencial do link compartilhavel: 32 bytes de
-- `secrets.token_urlsafe`. Quem tem o link ve a proposta -- e so ela, e so em
-- leitura. Nenhum outro dado da conta e alcancavel por essa porta.
CREATE TABLE IF NOT EXISTS proposals (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id        INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    lead_id        INTEGER NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
    number         TEXT NOT NULL DEFAULT '',
    title          TEXT NOT NULL,
    status         TEXT NOT NULL DEFAULT 'Rascunho'
                   CHECK (status IN ({_PROPOSAL_SQL_LIST})),
    client_name    TEXT NOT NULL DEFAULT '',
    client_company TEXT NOT NULL DEFAULT '',
    client_email   TEXT NOT NULL DEFAULT '',
    client_phone   TEXT NOT NULL DEFAULT '',
    owner_name     TEXT NOT NULL DEFAULT '',
    discount       REAL NOT NULL DEFAULT 0,
    subtotal       REAL NOT NULL DEFAULT 0,
    total          REAL NOT NULL DEFAULT 0,
    terms          TEXT NOT NULL DEFAULT '',
    delivery       TEXT NOT NULL DEFAULT '',
    notes          TEXT NOT NULL DEFAULT '',
    valid_until    TEXT,
    public_token   TEXT NOT NULL UNIQUE,
    sent_at        TEXT,
    viewed_at      TEXT,
    decided_at     TEXT,
    decided_by     TEXT NOT NULL DEFAULT '',
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS proposal_items (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    proposal_id INTEGER NOT NULL REFERENCES proposals(id) ON DELETE CASCADE,
    position    INTEGER NOT NULL DEFAULT 0,
    description TEXT NOT NULL,
    qty         REAL NOT NULL DEFAULT 1,
    unit_price  REAL NOT NULL DEFAULT 0,
    total       REAL NOT NULL DEFAULT 0
);

-- Automacoes: EVENTO -> CONDICAO -> ACAO.
-- `conditions` e `actions` sao JSON validado na entrada pelo Pydantic; o
-- banco guarda o texto ja normalizado.
CREATE TABLE IF NOT EXISTS automations (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    event       TEXT NOT NULL,
    conditions  TEXT NOT NULL DEFAULT '[]',
    actions     TEXT NOT NULL DEFAULT '[]',
    active      INTEGER NOT NULL DEFAULT 1,
    run_count   INTEGER NOT NULL DEFAULT 0,
    last_run_at TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

-- Historico de execucao.
--
-- `automation_name` e `lead_name` sao copias propositais: apagar a automacao
-- ou o lead nao pode transformar o historico numa lista de linhas orfas
-- dizendo "algo rodou sobre alguem".
CREATE TABLE IF NOT EXISTS automation_runs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    automation_id   INTEGER REFERENCES automations(id) ON DELETE SET NULL,
    automation_name TEXT NOT NULL DEFAULT '',
    lead_id         INTEGER REFERENCES leads(id) ON DELETE SET NULL,
    lead_name       TEXT NOT NULL DEFAULT '',
    event           TEXT NOT NULL,
    summary         TEXT NOT NULL DEFAULT '',
    status          TEXT NOT NULL CHECK (status IN ('ok', 'erro', 'parcial')),
    error           TEXT NOT NULL DEFAULT '',
    dedup_key       TEXT NOT NULL DEFAULT '',
    created_at      TEXT NOT NULL
);

-- Campos personalizados: definicao + valores (EAV).
--
-- EAV e escolha deliberada. O requisito e "nao exigir alteracao de banco para
-- cada campo novo do cliente"; uma coluna por campo faria exatamente o
-- contrario -- cada cliente novo viraria uma migracao.
CREATE TABLE IF NOT EXISTS custom_fields (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    entity      TEXT NOT NULL DEFAULT 'lead' CHECK (entity IN ('lead')),
    key         TEXT NOT NULL,
    label       TEXT NOT NULL,
    type        TEXT NOT NULL CHECK (type IN ({_CUSTOM_TYPE_SQL_LIST})),
    options     TEXT NOT NULL DEFAULT '[]',
    description TEXT NOT NULL DEFAULT '',
    required    INTEGER NOT NULL DEFAULT 0,
    position    INTEGER NOT NULL DEFAULT 0,
    active      INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT NOT NULL,
    UNIQUE (user_id, entity, key)
);

-- `value_num` existe ao lado de `value_text` para que numero, moeda e data
-- possam ser comparados e ordenados em SQL sem depender de ordenacao de
-- string -- que colocaria "9" depois de "10".
CREATE TABLE IF NOT EXISTS custom_values (
    field_id   INTEGER NOT NULL REFERENCES custom_fields(id) ON DELETE CASCADE,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    entity     TEXT NOT NULL DEFAULT 'lead',
    entity_id  INTEGER NOT NULL,
    value_text TEXT NOT NULL DEFAULT '',
    value_num  REAL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (field_id, entity, entity_id)
);

-- WhatsApp: configuracao da conexao, por conta.
--
-- O TOKEN DE ACESSO NAO FICA AQUI. Ele mora no `.env` do servidor, do mesmo
-- jeito que a senha do SMTP e o segredo do Google -- ver `whatsapp.py` para o
-- porque. Esta tabela guarda so o que identifica a conta e o estado da
-- conexao, que e justamente o que a tela de configuracao precisa mostrar.
CREATE TABLE IF NOT EXISTS wa_config (
    user_id         INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    provider        TEXT NOT NULL DEFAULT 'cloud_api',
    phone_number_id TEXT NOT NULL DEFAULT '',
    waba_id         TEXT NOT NULL DEFAULT '',
    display_phone   TEXT NOT NULL DEFAULT '',
    status          TEXT NOT NULL DEFAULT 'desconectado'
                    CHECK (status IN ('desconectado', 'conectado', 'erro')),
    last_error      TEXT NOT NULL DEFAULT '',
    last_check_at   TEXT,
    connected_at    TEXT,
    updated_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS wa_templates (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name       TEXT NOT NULL,
    language   TEXT NOT NULL DEFAULT 'pt_BR',
    category   TEXT NOT NULL DEFAULT 'UTILITY',
    body       TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (user_id, name, language)
);

CREATE TABLE IF NOT EXISTS wa_messages (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id       INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    lead_id       INTEGER REFERENCES leads(id) ON DELETE SET NULL,
    direction     TEXT NOT NULL CHECK (direction IN ('saida', 'entrada')),
    phone         TEXT NOT NULL,
    body          TEXT NOT NULL DEFAULT '',
    template_name TEXT NOT NULL DEFAULT '',
    wa_message_id TEXT NOT NULL DEFAULT '',
    status        TEXT NOT NULL DEFAULT 'pendente'
                  CHECK (status IN ('pendente', 'enviada', 'entregue', 'lida', 'falhou', 'recebida')),
    error         TEXT NOT NULL DEFAULT '',
    created_at    TEXT NOT NULL
);

-- Trava de agendador.
--
-- O uvicorn sobe com 2 workers. Sem esta tabela, os dois rodariam a varredura
-- de automacoes por tempo e cada lead atrasado geraria duas execucoes. O
-- vencedor pega um arrendamento curto; o outro simplesmente pula a rodada.
CREATE TABLE IF NOT EXISTS scheduler_leases (
    name       TEXT PRIMARY KEY,
    holder     TEXT NOT NULL,
    expires_at TEXT NOT NULL
);

-- Contador de rate limit. Fica no BANCO, e nao num dict em memoria, porque no
-- servidor o uvicorn costuma subir com mais de um worker: com contagem por
-- processo, cada worker teria o proprio teto e a protecao contra forca bruta
-- valeria uma fracao do que promete. De quebra, o bloqueio sobrevive a um
-- reinicio do servico. Ver `auth.py`.
CREATE TABLE IF NOT EXISTS rate_hits (
    id     INTEGER PRIMARY KEY,
    bucket TEXT NOT NULL,
    hit_at TEXT NOT NULL
);

-- Interesse nos planos pagos.
--
-- Enquanto nao existe cobranca, o botao "Quero o Pro" precisa levar a algum
-- lugar de verdade. Aqui: uma linha no banco e um e-mail para o dono. Sem
-- isto, o pedido do cliente morreria num alert() -- e o interessado some.
--
-- `user_id` e opcional de proposito: da landing chega gente sem conta.
CREATE TABLE IF NOT EXISTS plan_interests (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER REFERENCES users(id) ON DELETE SET NULL,
    plan       TEXT NOT NULL CHECK (plan IN ('pro', 'empresa')),
    name       TEXT NOT NULL,
    email      TEXT NOT NULL,
    company    TEXT NOT NULL DEFAULT '',
    phone      TEXT NOT NULL DEFAULT '',
    seats      INTEGER NOT NULL DEFAULT 1,
    message    TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

-- Assinatura da conta.
--
-- Uma linha por usuario, sempre -- inclusive para quem nunca pagou nada.
-- `billing.assinatura()` cria a linha faltante em vez de devolver None, porque
-- "sem linha" e "plano Inicial" precisam ser a mesma coisa em todo o codigo;
-- caso contrario cada consulta teria que lembrar do caso nulo, e a que
-- esquecesse liberaria acesso.
--
-- Quando o bloco de organizacoes chegar, esta tabela ganha `org_id` e o
-- `user_id` vira o dono. O estado nao muda de formato.
{SUBSCRIPTIONS_DDL}

-- Historico de cobranca: uma linha por pagamento que o provedor confirmou.
--
-- `UNIQUE(provider, provider_ref)` e' a idempotencia: o Mercado Pago reenvia o
-- mesmo webhook quando nao recebe 200, e sem esta restricao a mesma cobranca
-- entraria duas vezes no extrato do cliente.
CREATE TABLE IF NOT EXISTS invoices (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    provider     TEXT NOT NULL,
    provider_ref TEXT NOT NULL,
    plan         TEXT NOT NULL DEFAULT '',
    centavos     INTEGER NOT NULL DEFAULT 0,
    currency     TEXT NOT NULL DEFAULT 'BRL',
    status       TEXT NOT NULL,
    metodo       TEXT NOT NULL DEFAULT '',
    periodo_ate  TEXT,
    paid_at      TEXT,
    created_at   TEXT NOT NULL,
    UNIQUE (provider, provider_ref)
);

-- Todo aviso que o provedor mandou, cru, com o veredito da assinatura HMAC.
--
-- Serve para tres coisas: idempotencia (o UNIQUE), auditoria de dinheiro (dá
-- para reconstruir por que uma conta virou paga) e investigacao de fraude --
-- um webhook forjado fica gravado com `signature_ok = 0` em vez de sumir.
CREATE TABLE IF NOT EXISTS billing_events (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    provider     TEXT NOT NULL,
    topic        TEXT NOT NULL,
    event_id     TEXT NOT NULL,
    signature_ok INTEGER NOT NULL DEFAULT 0,
    payload      TEXT NOT NULL DEFAULT '',
    resultado    TEXT NOT NULL DEFAULT '',
    user_id      INTEGER REFERENCES users(id) ON DELETE SET NULL,
    created_at   TEXT NOT NULL,
    UNIQUE (provider, topic, event_id)
);

CREATE INDEX IF NOT EXISTS idx_invoices_user      ON invoices(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_billing_events_at  ON billing_events(created_at DESC);

CREATE INDEX IF NOT EXISTS idx_leads_user_id       ON leads(user_id);
CREATE INDEX IF NOT EXISTS idx_leads_user_created  ON leads(user_id, created_at);
CREATE INDEX IF NOT EXISTS idx_leads_user_status   ON leads(user_id, status);
CREATE INDEX IF NOT EXISTS idx_sessions_token      ON sessions(token_hash);
CREATE INDEX IF NOT EXISTS idx_sessions_user_id    ON sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_email_codes_user    ON email_codes(user_id, purpose);
CREATE INDEX IF NOT EXISTS idx_rate_hits_bucket    ON rate_hits(bucket, hit_at);
CREATE INDEX IF NOT EXISTS idx_plan_interests_at   ON plan_interests(created_at);
CREATE INDEX IF NOT EXISTS idx_act_user_created    ON activities(user_id, created_at);
CREATE INDEX IF NOT EXISTS idx_act_lead            ON activities(lead_id, created_at);
CREATE INDEX IF NOT EXISTS idx_act_pendentes       ON activities(user_id, due_at)
    WHERE due_at IS NOT NULL AND done_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_notif_user          ON notifications(user_id, created_at);
CREATE INDEX IF NOT EXISTS idx_notif_nao_lidas     ON notifications(user_id) WHERE read_at IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS idx_notif_dedup  ON notifications(user_id, dedup_key)
    WHERE dedup_key <> '';
CREATE INDEX IF NOT EXISTS idx_loss_user           ON loss_reasons(user_id, position);
CREATE INDEX IF NOT EXISTS idx_prop_user           ON proposals(user_id, created_at);
CREATE INDEX IF NOT EXISTS idx_prop_lead           ON proposals(lead_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_prop_token   ON proposals(public_token);
CREATE INDEX IF NOT EXISTS idx_prop_items          ON proposal_items(proposal_id, position);
CREATE INDEX IF NOT EXISTS idx_auto_user           ON automations(user_id, event);
CREATE INDEX IF NOT EXISTS idx_autorun_user        ON automation_runs(user_id, created_at);
CREATE UNIQUE INDEX IF NOT EXISTS idx_autorun_dedup ON automation_runs(automation_id, dedup_key)
    WHERE dedup_key <> '';
CREATE INDEX IF NOT EXISTS idx_cf_user             ON custom_fields(user_id, entity, position);
CREATE INDEX IF NOT EXISTS idx_cv_entity           ON custom_values(entity, entity_id);
CREATE INDEX IF NOT EXISTS idx_wamsg_lead          ON wa_messages(lead_id, created_at);
CREATE INDEX IF NOT EXISTS idx_wamsg_user          ON wa_messages(user_id, created_at);

-- ==========================================================================
-- FASE 3 -- INTELIGENCIA COMERCIAL
-- ==========================================================================

-- Historico do funil em formato LEGIVEL POR MAQUINA.
--
-- A mudanca de etapa ja aparece na linha do tempo desde a Fase 2, mas la ela e
-- uma frase ("Proposta -> Negociacao") escrita para gente ler. Calcular
-- conversao real por etapa em cima daquele texto seria fazer estatistica em
-- cima de string de interface -- que muda no dia em que alguem melhorar a
-- frase, e leva a previsao junto.
--
-- Aqui cada transicao vira duas colunas. E isto que permite responder, com
-- dado e nao com chute: "de cada 10 negocios que chegaram em Proposta, quantos
-- foram ganhos?". Enquanto nao houver historico suficiente, a previsao diz em
-- voz alta que esta usando a curva padrao -- ver `intel.previsao`.
CREATE TABLE IF NOT EXISTS stage_events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    lead_id    INTEGER NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
    de         TEXT NOT NULL,
    para       TEXT NOT NULL,
    -- Quantos dias o negocio passou na etapa que esta deixando. Guardado no
    -- momento da transicao porque depois nao da mais para reconstruir: a
    -- proxima mudanca sobrescreve `leads.stage_changed_at`.
    dias_na_etapa INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

-- Uso da IA, por usuario.
--
-- Existe por tres motivos, nesta ordem: limitar chamadas (a conta de IA e paga
-- por uso e um laco acidental na interface viraria fatura), mostrar ao dono
-- quanto esta gastando, e deixar rastro de QUE pergunta foi feita para o caso
-- de a resposta sair errada.
--
-- Nao guarda a resposta inteira nem os dados enviados: o que interessa e
-- quanto custou e quando. O conteudo do CRM ja esta no CRM.
CREATE TABLE IF NOT EXISTS ai_usage (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id       INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    kind          TEXT NOT NULL,
    model         TEXT NOT NULL DEFAULT '',
    tokens_in     INTEGER NOT NULL DEFAULT 0,
    tokens_out    INTEGER NOT NULL DEFAULT 0,
    ok            INTEGER NOT NULL DEFAULT 1,
    error         TEXT NOT NULL DEFAULT '',
    prompt_resumo TEXT NOT NULL DEFAULT '',
    created_at    TEXT NOT NULL
);

-- Ativacao (secao 25): a primeira vez que a conta fez cada coisa.
--
-- Uma linha por marco, gravada uma unica vez. O indice unico e quem garante
-- isso -- sem ele, "primeiro lead criado" seria regravado a cada lead novo e
-- a data perderia o sentido.
CREATE TABLE IF NOT EXISTS activation (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    marco      TEXT NOT NULL,
    created_at TEXT NOT NULL
);

-- Historico de mudanca de VALOR de um negocio (secao 3 -- Negociacao).
--
-- Mesma ideia do stage_events: a linha do tempo mostra "Valor: R$ X -> R$ Y"
-- para gente ler; esta tabela guarda os numeros para a maquina responder sem
-- parsear texto de interface -- "quanto de desconto foi dado?", "qual era o
-- valor inicial?". `de` e o valor ANTES da mudanca; `para`, o depois.
CREATE TABLE IF NOT EXISTS deal_value_events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    lead_id    INTEGER NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
    de         REAL NOT NULL,
    para       REAL NOT NULL,
    note       TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_stage_user          ON stage_events(user_id, created_at);
CREATE INDEX IF NOT EXISTS idx_stage_lead          ON stage_events(lead_id, created_at);
CREATE INDEX IF NOT EXISTS idx_ai_usage_user       ON ai_usage(user_id, created_at);
CREATE UNIQUE INDEX IF NOT EXISTS idx_activation   ON activation(user_id, marco);
CREATE INDEX IF NOT EXISTS idx_dve_lead            ON deal_value_events(lead_id, created_at);

-- ==========================================================================
-- SEGURANCA -- iscas (honeytokens / tripwires)
-- ==========================================================================
--
-- Registro dos acessos as ISCAS: caminhos e tokens falsos que nenhum cliente
-- legitimo do Vertex toca. Uma linha aqui e', por definicao, alguem fucando
-- onde nao devia. NAO e' a fechadura (isso e' sessao + isolamento por conta);
-- e' o alarme. O painel do dono le' esta tabela. Ver `honeypot.py`.
--
-- NAO tem `user_id`: quem cai na isca normalmente nem tem sessao. Guarda o
-- minimo para o dono reconhecer um padrao (de onde, o que tentou, quando) --
-- e nada que seja dado pessoal de cliente, porque nao ha cliente aqui.
CREATE TABLE IF NOT EXISTS security_events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    kind       TEXT NOT NULL,
    path       TEXT NOT NULL DEFAULT '',
    ip         TEXT NOT NULL DEFAULT '',
    user_agent TEXT NOT NULL DEFAULT '',
    detail     TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_secevents_at ON security_events(created_at DESC);

-- ==========================================================================
-- FASE 4 -- ORGANIZACOES / EQUIPES (multiempresa + papeis)
-- ==========================================================================
--
-- Cada conta vira uma ORGANIZACAO com equipe. O ponto-chave do desenho: o
-- NAMESPACE de dados da organizacao E' o `user_id` do dono -- exatamente o que
-- todo `leads.user_id` (e activities, proposals, etc.) ja' guarda hoje. Por
-- isso a introducao de equipes NAO migra nenhuma linha existente: a org de uma
-- conta antiga aponta para o proprio id dela, e tudo continua no lugar.
--
-- `memberships` liga PESSOAS (users) a uma org com um PAPEL. Enquanto so' existe
-- o dono, ha uma membership 'admin' e nada muda. Um segundo usuario (membro
-- convidado) e' o primeiro momento em que "quem sou eu" (actor) e "de qual
-- empresa sao os dados" (tenant) deixam de ser a mesma coisa.
CREATE TABLE IF NOT EXISTS organizations (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name          TEXT NOT NULL,
    created_at    TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_org_owner ON organizations(owner_user_id);

-- Uma pessoa pertence a UMA org (por ora): o UNIQUE em user_id garante isso e
-- mantem a resolucao de contexto trivial -- uma sessao -> uma membership -> uma
-- org. Multi-org por pessoa fica para depois, se e quando fizer sentido.
CREATE TABLE IF NOT EXISTS memberships (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    org_id     INTEGER NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role       TEXT NOT NULL DEFAULT 'vendedor'
               CHECK (role IN ('admin', 'gestor', 'vendedor')),
    created_at TEXT NOT NULL,
    UNIQUE (org_id, user_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_membership_user ON memberships(user_id);

-- Convites por LINK. O sistema nao manda e-mail: o admin/gestor gera o link e o
-- entrega como quiser. Guardamos so' o sha256 do token -- o link cru aparece
-- UMA vez, na criacao, e nunca mais (mesmo principio de `sessions`).
CREATE TABLE IF NOT EXISTS org_invites (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    org_id      INTEGER NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    token_hash  TEXT NOT NULL UNIQUE,
    role        TEXT NOT NULL DEFAULT 'vendedor'
                CHECK (role IN ('admin', 'gestor', 'vendedor')),
    email       TEXT NOT NULL DEFAULT '',
    created_by  INTEGER REFERENCES users(id) ON DELETE SET NULL,
    expires_at  TEXT NOT NULL,
    accepted_at TEXT,
    accepted_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_invite_org ON org_invites(org_id, created_at DESC);

-- Trilha de auditoria: quem fez O QUE de sensivel na organizacao.
--
-- Diferente de `security_events` (isca/ataque, sem dono): aqui e' acao
-- LEGITIMA de um membro que precisa ficar registrada -- convidou, removeu,
-- mudou papel, distribuiu lead. `actor_name` e `target_label` sao copias
-- DENORMALIZADAS de proposito: se a pessoa depois for removida (actor vira
-- NULL), o registro ainda diz "Fulano removeu Beltrano". NUNCA guarda segredo.
CREATE TABLE IF NOT EXISTS audit_events (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    org_id        INTEGER NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    actor_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    actor_name    TEXT NOT NULL DEFAULT '',
    action        TEXT NOT NULL,
    target_type   TEXT NOT NULL DEFAULT '',
    target_id     INTEGER,
    target_label  TEXT NOT NULL DEFAULT '',
    detail        TEXT NOT NULL DEFAULT '',
    created_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_org ON audit_events(org_id, created_at DESC);

-- ===========================================================================
-- Email marketing (Fase A). Tudo escopado por user_id = tenant (dono da conta),
-- igual ao resto. Contato NAO e' entidade nova: sao os leads que ja' existem; o
-- que vive aqui e' o ESTADO DE MARKETING de um e-mail (consentimento,
-- suppression) e os objetos de campanha. Regra de ferro: a suppression tem
-- prioridade sobre qualquer segmento, e sem consentimento nao ha envio.
-- ===========================================================================

-- Consentimento por e-mail, dentro de um tenant. E' a fonte da elegibilidade:
-- so' 'subscribed' recebe marketing. Chega la' por opt-in explicito (import com
-- base legal, formulario, ou marcacao manual do dono) -- nunca por presuncao.
CREATE TABLE IF NOT EXISTS mkt_consent (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    email           TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('subscribed', 'unsubscribed', 'pending')),
    source          TEXT NOT NULL DEFAULT '',
    consent_at      TEXT,
    unsubscribed_at TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    UNIQUE (user_id, email)
);
CREATE INDEX IF NOT EXISTS idx_mkt_consent_user ON mkt_consent(user_id, status);

-- Lista de exclusao. TEM PRIORIDADE sobre segmento e consentimento: e-mail aqui,
-- nao sai campanha, ponto. Entra por descadastro, hard bounce, reclamacao,
-- invalido ou decisao manual.
CREATE TABLE IF NOT EXISTS mkt_suppression (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    email      TEXT NOT NULL,
    reason     TEXT NOT NULL DEFAULT 'manual'
               CHECK (reason IN ('unsubscribed','hard_bounce','complaint','invalid','manual')),
    detail     TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    UNIQUE (user_id, email)
);
CREATE INDEX IF NOT EXISTS idx_mkt_suppression_user ON mkt_suppression(user_id);

-- Token de descadastro: aleatorio, nao sequencial, mapeia para (tenant, email).
-- O link de cada campanha carrega SO' este token -- nunca o e-mail em claro na
-- URL. Clicar consome: marca unsubscribed + entra na suppression.
CREATE TABLE IF NOT EXISTS mkt_unsub_tokens (
    token      TEXT PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    email      TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (user_id, email)
);

-- Templates reutilizaveis. O HTML JA' entra sanitizado (marketing.sanitizar):
-- sem <script>, sem on*, sem javascript:. Multi-tenant como tudo.
CREATE TABLE IF NOT EXISTS mkt_templates (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name       TEXT NOT NULL,
    subject    TEXT NOT NULL DEFAULT '',
    body_html  TEXT NOT NULL DEFAULT '',
    archived   INTEGER NOT NULL DEFAULT 0,
    created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_mkt_templates_user ON mkt_templates(user_id, archived);

-- Campanhas. `segmento` e' um filtro JSON resolvido SEMPRE dentro do tenant.
-- Os estados cobrem o ciclo de vida; o envio real e' assincrono (mkt_messages).
CREATE TABLE IF NOT EXISTS mkt_campaigns (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name         TEXT NOT NULL,
    subject      TEXT NOT NULL DEFAULT '',
    preheader    TEXT NOT NULL DEFAULT '',
    from_name    TEXT NOT NULL DEFAULT '',
    from_email   TEXT NOT NULL DEFAULT '',
    body_html    TEXT NOT NULL DEFAULT '',
    segmento     TEXT NOT NULL DEFAULT '{{}}',
    status       TEXT NOT NULL DEFAULT 'draft'
                 CHECK (status IN ('draft','scheduled','queued','sending','sent','paused','cancelled','failed')),
    scheduled_at TEXT,
    total_dest   INTEGER NOT NULL DEFAULT 0,
    created_by   INTEGER REFERENCES users(id) ON DELETE SET NULL,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_mkt_campaigns_user ON mkt_campaigns(user_id, status);

-- Fila de envio: UMA linha por (campanha, destinatario). O UNIQUE(dedupe) e' a
-- idempotencia -- o worker nunca envia a mesma mensagem duas vezes, nem numa
-- corrida entre os dois workers do uvicorn.
CREATE TABLE IF NOT EXISTS mkt_messages (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    campaign_id  INTEGER NOT NULL REFERENCES mkt_campaigns(id) ON DELETE CASCADE,
    email        TEXT NOT NULL,
    lead_id      INTEGER,
    status       TEXT NOT NULL DEFAULT 'queued'
                 CHECK (status IN ('queued','sending','sent','failed','skipped')),
    tentativas   INTEGER NOT NULL DEFAULT 0,
    erro         TEXT NOT NULL DEFAULT '',
    provider_ref TEXT NOT NULL DEFAULT '',
    dedupe       TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    sent_at      TEXT,
    UNIQUE (dedupe)
);
CREATE INDEX IF NOT EXISTS idx_mkt_messages_fila ON mkt_messages(status, id);
CREATE INDEX IF NOT EXISTS idx_mkt_messages_camp ON mkt_messages(campaign_id, status);
"""

USER_COLUMN_MIGRATIONS: tuple[tuple[str, str, str | None], ...] = (
    (
        "email_verified",
        "ALTER TABLE users ADD COLUMN email_verified INTEGER DEFAULT 0",
        "UPDATE users SET email_verified = 1",
    ),
    (
        "google_sub",
        "ALTER TABLE users ADD COLUMN google_sub TEXT",
        None,
    ),
    (
        "auth_provider",
        "ALTER TABLE users ADD COLUMN auth_provider TEXT DEFAULT 'password'",
        "UPDATE users SET auth_provider = 'password' WHERE auth_provider IS NULL",
    ),
    (
        "avatar_key",
        "ALTER TABLE users ADD COLUMN avatar_key TEXT NOT NULL DEFAULT ''",
        None,
    ),
    (
        "onboarding_off",
        "ALTER TABLE users ADD COLUMN onboarding_off INTEGER NOT NULL DEFAULT 0",
        None,
    ),
)

LEAD_COLUMN_MIGRATIONS: tuple[tuple[str, str, str | None], ...] = (
    ("email", "ALTER TABLE leads ADD COLUMN email TEXT NOT NULL DEFAULT ''", None),
    ("phone", "ALTER TABLE leads ADD COLUMN phone TEXT NOT NULL DEFAULT ''", None),
    ("whatsapp", "ALTER TABLE leads ADD COLUMN whatsapp TEXT NOT NULL DEFAULT ''", None),
    ("source", "ALTER TABLE leads ADD COLUMN source TEXT NOT NULL DEFAULT ''", None),
    ("notes", "ALTER TABLE leads ADD COLUMN notes TEXT NOT NULL DEFAULT ''", None),
    ("tags", "ALTER TABLE leads ADD COLUMN tags TEXT NOT NULL DEFAULT '[]'", None),
    ("owner", "ALTER TABLE leads ADD COLUMN owner TEXT NOT NULL DEFAULT ''", None),

    (
        "owner_user_id",
        "ALTER TABLE leads ADD COLUMN owner_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL",
        None,
    ),
    ("lost_reason", "ALTER TABLE leads ADD COLUMN lost_reason TEXT NOT NULL DEFAULT ''", None),
    ("lost_note", "ALTER TABLE leads ADD COLUMN lost_note TEXT NOT NULL DEFAULT ''", None),
    ("closed_at", "ALTER TABLE leads ADD COLUMN closed_at TEXT", None),

    ("last_activity_at", "ALTER TABLE leads ADD COLUMN last_activity_at TEXT", None),

    (
        "stage_changed_at",
        "ALTER TABLE leads ADD COLUMN stage_changed_at TEXT",
        "UPDATE leads SET stage_changed_at = updated_at WHERE stage_changed_at IS NULL",
    ),

    ("score", "ALTER TABLE leads ADD COLUMN score INTEGER", None),
    ("score_band", "ALTER TABLE leads ADD COLUMN score_band TEXT NOT NULL DEFAULT ''", None),
    ("score_at", "ALTER TABLE leads ADD COLUMN score_at TEXT", None),
)

POST_MIGRATION_SCHEMA = """
CREATE UNIQUE INDEX IF NOT EXISTS idx_users_google_sub
    ON users(google_sub) WHERE google_sub IS NOT NULL;

-- Ordenar por prioridade e a consulta mais quente da Fase 3. Fica AQUI, e nao
-- no SCHEMA, porque num banco que ja existe a coluna `score` so nasce em
-- `migrate()` -- que roda depois do SCHEMA. No SCHEMA, este indice quebraria o
-- startup de toda instalacao anterior a Fase 3.
CREATE INDEX IF NOT EXISTS idx_leads_score ON leads(user_id, score DESC);

-- Visibilidade por vendedor (Fase 4). Fica AQUI, e nao no SCHEMA, porque
-- `owner_user_id` so' nasce em `migrate()` num banco que ja' existia -- que roda
-- depois do SCHEMA. O filtro do vendedor casa (user_id, owner_user_id).
CREATE INDEX IF NOT EXISTS idx_leads_owner ON leads(user_id, owner_user_id);

-- Indices de `subscriptions`. Ficam AQUI, e nao no SCHEMA, porque
-- `migrate_subscription_status` reconstroi a tabela com DROP + RENAME -- e o
-- DROP leva os indices junto. O SCHEMA ja teria rodado a essa altura; estes
-- rodam depois, e por isso sobrevivem a reconstrucao.
CREATE INDEX IF NOT EXISTS idx_subs_user ON subscriptions(user_id);
CREATE INDEX IF NOT EXISTS idx_subs_ref  ON subscriptions(provider, provider_ref)
    WHERE provider_ref <> '';

CREATE TABLE IF NOT EXISTS known_devices (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    device_hash  TEXT NOT NULL,
    label        TEXT NOT NULL DEFAULT '',
    created_at   TEXT NOT NULL,
    last_seen_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_known_devices ON known_devices(user_id, device_hash);
"""

Connection = sqlite3.Connection
Cursor = sqlite3.Cursor
Row = sqlite3.Row

def table_columns(conn: Connection, table: str) -> set[str]:
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}

def table_exists(conn: Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
    ).fetchone()
    return row is not None

def _apply_columns(
    conn: Connection, table: str, migrations: tuple[tuple[str, str, str | None], ...]
) -> list[str]:
    existing = table_columns(conn, table)
    applied: list[str] = []
    for column, ddl, backfill in migrations:
        if column in existing:
            continue
        conn.execute(ddl)
        if backfill:
            conn.execute(backfill)
        applied.append(column)
    if applied:
        logger.info("Migração aplicada em `%s`: %s", table, ", ".join(applied))
    return applied

SESSION_COLUMN_MIGRATIONS: tuple[tuple[str, str, str | None], ...] = (
    ("device", "ALTER TABLE sessions ADD COLUMN device TEXT NOT NULL DEFAULT ''", None),
    (
        "last_seen_at",
        "ALTER TABLE sessions ADD COLUMN last_seen_at TEXT NOT NULL DEFAULT ''",

        "UPDATE sessions SET last_seen_at = created_at WHERE last_seen_at = ''",
    ),
)

def migrate(conn: Connection) -> list[str]:
    return (
        _apply_columns(conn, "users", USER_COLUMN_MIGRATIONS)
        + _apply_columns(conn, "leads", LEAD_COLUMN_MIGRATIONS)
        + _apply_columns(conn, "sessions", SESSION_COLUMN_MIGRATIONS)
    )

def _leads_precisa_reconstruir(conn: Connection) -> bool:
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'leads'"
    ).fetchone()
    if row is None:
        return False
    return "'Perdido'" not in (row["sql"] or "")

def migrate_lead_stages() -> bool:
    caminho = db_path()
    if not caminho.exists():
        return False

    conn = sqlite3.connect(caminho, timeout=15.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    try:
        if not _leads_precisa_reconstruir(conn):
            return False

        try:
            reserva = caminho.with_suffix(caminho.suffix + f".pre-etapas-{utcnow():%Y%m%d-%H%M%S}")

            with sqlite3.connect(reserva) as destino:
                conn.backup(destino)
            logger.info("Backup do banco antes da migração de etapas: %s", reserva)
        except Exception:  # noqa: BLE001
            logger.exception(
                "Não foi possível gravar o backup antes da migração de etapas. "
                "A migração NÃO será executada."
            )
            return False

        antigas = table_columns(conn, "leads")
        novas = {
            "id", "user_id", "name", "company", "value", "status", "segment",
            "email", "phone", "whatsapp", "source", "notes", "tags", "owner",
            "owner_user_id",
            "lost_reason", "lost_note", "closed_at", "last_activity_at",
            "stage_changed_at", "score", "score_band", "score_at",
            "created_at", "updated_at",
        }

        comuns = [c for c in novas if c in antigas]

        selects: list[str] = []
        for coluna in comuns:
            if coluna == "status":
                quando = " ".join(
                    f"WHEN '{velho}' THEN '{novo}'" for velho, novo in STATUS_RENAMES.items()
                )
                validos = _STATUS_SQL_LIST
                selects.append(
                    f"CASE status {quando} ELSE "
                    f"(CASE WHEN status IN ({validos}) THEN status ELSE 'Prospecção' END) END"
                )
            else:
                selects.append(coluna)

        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute("PRAGMA legacy_alter_table = ON")
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute(LEADS_DDL.format(tabela="leads_migracao"))
            conn.execute(
                f"INSERT INTO leads_migracao ({', '.join(comuns)}) "
                f"SELECT {', '.join(selects)} FROM leads"
            )
            movidos = conn.execute("SELECT COUNT(*) AS t FROM leads_migracao").fetchone()["t"]
            originais = conn.execute("SELECT COUNT(*) AS t FROM leads").fetchone()["t"]
            if movidos != originais:
                raise RuntimeError(
                    f"migração das etapas copiaria {movidos} de {originais} leads"
                )
            conn.execute("DROP TABLE leads")
            conn.execute("ALTER TABLE leads_migracao RENAME TO leads")
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        finally:
            conn.execute("PRAGMA legacy_alter_table = OFF")
            conn.execute("PRAGMA foreign_keys = ON")

        logger.info(
            "Etapas do funil migradas: %d leads, 'Fechado' virou 'Ganho', "
            "'Negociação' e 'Perdido' agora existem.",
            originais,
        )
        return True
    finally:
        conn.close()

@contextmanager
def get_conn() -> Iterator[Connection]:
    conn = sqlite3.connect(db_path(), timeout=15.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 15000")

    conn.create_function("unaccent", 1, deburr, deterministic=True)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

def migrate_subscription_status(conn: Connection) -> bool:
    if not table_exists(conn, "subscriptions"):
        return False
    linha = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'subscriptions'"
    ).fetchone()
    if linha is None or "'gratuito'" in (linha["sql"] or ""):
        return False

    colunas = ", ".join(sorted(table_columns(conn, "subscriptions") & {
        "id", "user_id", "plan", "status", "trial_ends_at", "current_period_end",
        "provider", "provider_ref", "modo", "cancel_at_period_end", "centavos",
        "created_at", "updated_at",
    }))
    conn.executescript(
        SUBSCRIPTIONS_DDL.replace(
            "CREATE TABLE IF NOT EXISTS subscriptions (",
            "CREATE TABLE subscriptions_novo (",
        )
    )
    conn.execute(
        f"INSERT INTO subscriptions_novo ({colunas}) SELECT {colunas} FROM subscriptions"
    )
    conn.execute("DROP TABLE subscriptions")
    conn.execute("ALTER TABLE subscriptions_novo RENAME TO subscriptions")
    logger.info("Tabela `subscriptions` reconstruída com o estado `gratuito`.")
    return True

def init_db() -> None:
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    migrate_lead_stages()
    with get_conn() as conn:
        conn.executescript(SCHEMA)
        migrate(conn)
        migrate_subscription_status(conn)
        conn.executescript(POST_MIGRATION_SCHEMA)
    logger.info("Banco pronto em %s", path)

def purge_expired_sessions() -> int:
    with get_conn() as conn:
        cur = conn.execute("DELETE FROM sessions WHERE expires_at <= ?", (now_iso(),))
        removed = cur.rowcount or 0
    if removed:
        logger.info("Sessoes expiradas removidas: %d", removed)
    return removed

def purge_expired_email_codes() -> int:
    with get_conn() as conn:
        cur = conn.execute("DELETE FROM email_codes WHERE expires_at <= ?", (now_iso(),))
        removed = cur.rowcount or 0
    if removed:
        logger.info("Códigos de verificação expirados removidos: %d", removed)
    return removed

def purge_rate_hits_older_than(seconds: int) -> int:
    cutoff = iso(utcnow() - timedelta(seconds=seconds))
    with get_conn() as conn:
        cur = conn.execute("DELETE FROM rate_hits WHERE hit_at <= ?", (cutoff,))
        removed = cur.rowcount or 0
    if removed:
        logger.info("Registros de rate limit vencidos removidos: %d", removed)
    return removed

NOTIFICATION_RETENTION_DAYS = 90

def purge_old_notifications() -> int:
    cutoff = iso(utcnow() - timedelta(days=NOTIFICATION_RETENTION_DAYS))
    with get_conn() as conn:
        cur = conn.execute(
            "DELETE FROM notifications WHERE read_at IS NOT NULL AND created_at <= ?",
            (cutoff,),
        )
        removed = cur.rowcount or 0
    if removed:
        logger.info("Notificações lidas antigas removidas: %d", removed)
    return removed

def count_users() -> int:
    with get_conn() as conn:
        row = conn.execute("SELECT COUNT(*) AS total FROM users").fetchone()
    return int(row["total"])

def acquire_lease(name: str, holder: str, seconds: int) -> bool:
    agora = now_iso()
    expira = iso(utcnow() + timedelta(seconds=seconds))
    with get_conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "INSERT OR IGNORE INTO scheduler_leases (name, holder, expires_at) VALUES (?, ?, ?)",
            (name, holder, "1970-01-01T00:00:00.000000+00:00"),
        )
        cur = conn.execute(
            "UPDATE scheduler_leases SET holder = ?, expires_at = ? "
            "WHERE name = ? AND expires_at <= ?",
            (holder, expira, name, agora),
        )
        return (cur.rowcount or 0) > 0
