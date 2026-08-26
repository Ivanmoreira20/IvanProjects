import * as api from './api.js';
import { el, icon, clear, brl, toast, emptyState } from './ui.js';
import { store, hooks } from './store.js';
import { planoInclui, bloqueio } from './cobranca.js';

const ROTULO_ACAO = {
  ligar: 'Ligar',
  mensagem: 'Mandar mensagem',
  reuniao: 'Agendar reunião',
  revisar_proposta: 'Revisar a proposta',
  concluir_tarefa: 'Concluir a tarefa',
};

const ICONE_ACAO = {
  ligar: 'phone',
  mensagem: 'send',
  reuniao: 'calendar',
  revisar_proposta: 'doc',
  concluir_tarefa: 'check',
};

const ROTULO_MARCO = {
  primeiro_lead: 'Cadastrar o primeiro lead',
  primeiro_negocio: 'Ter um negócio no funil',
  primeira_atividade: 'Registrar uma conversa',
  primeiro_followup: 'Criar uma tarefa com prazo',
  primeira_proposta: 'Enviar uma proposta',
  primeira_automacao: 'Criar uma automação',
  primeiro_ganho: 'Ganhar um negócio',
};

export async function loadIntel() {

  const temIA = planoInclui('ia');
  const [resumo, prev, ativ, ia] = await Promise.allSettled([
    api.intelResumo(), api.previsao(), api.ativacao(),
    temIA ? api.iaStatus() : Promise.resolve(null),
  ]);
  if (resumo.status === 'fulfilled') store.intel = resumo.value;
  if (prev.status === 'fulfilled') store.previsao = prev.value;
  if (ativ.status === 'fulfilled') store.ativacao = ativ.value;
  store.iaStatus = temIA && ia.status === 'fulfilled' ? ia.value : null;
}

export async function loadAvancado(periodo = store.avancadoPeriodo) {
  store.avancadoPeriodo = periodo;
  if (!planoInclui('relatorios_avancados')) { store.avancado = null; return; }
  try {
    store.avancado = await api.relatorioAvancado(periodo);
  } catch (err) {
    if (err?.status !== 401) store.avancado = null;
  }
}

export function seloScore(score, banda, { titulo = '' } = {}) {
  if (score === null || score === undefined) {
    return el('span', { class: 'score score--vazio', attrs: { title: 'Ainda não calculado' } }, ['—']);
  }
  return el(
    'span',
    {
      class: `score score--${banda}`,
      attrs: { title: titulo || `Prioridade ${score} de 100 (${banda})` },
    },
    [String(score)],
  );
}

function tabelaFatores(fatores) {
  return el('ul', { class: 'fatores' }, fatores.map((f) => el('li', { class: 'fator' }, [
    el('div', { class: 'fator__topo' }, [
      el('span', { class: 'fator__nome' }, [f.nome]),
      el('span', { class: 'fator__pontos' }, [`${f.pontos} / ${f.maximo}`]),
    ]),
    el('div', { class: 'fator__barra' }, [
      el('span', {
        class: 'fator__preenchido',
        attrs: { style: `width:${Math.max(0, Math.min(100, (f.pontos / f.maximo) * 100))}%` },
      }),
    ]),
    el('p', { class: 'fator__txt' }, [f.texto]),
  ])));
}

function listaRiscos(riscos) {
  return el('ul', { class: 'riscos' }, riscos.map((r) => el(
    'li',
    { class: `risco risco--${r.gravidade}` },
    [icon('alert', 'ico risco__ico'), el('span', {}, [r.texto])],
  )));
}

function blocoSugestao(sug) {
  if (!sug) return null;
  const quando = sug.em_dias === 0
    ? 'hoje'
    : sug.em_dias === 1 ? 'amanhã' : `em ${sug.em_dias} dias`;
  return el('div', { class: 'sugestao' }, [
    icon(ICONE_ACAO[sug.acao] || 'spark', 'ico sugestao__ico'),
    el('div', {}, [
      el('strong', {}, [`${ROTULO_ACAO[sug.acao] || sug.acao} ${quando}`]),
      el('span', { class: 'sugestao__porque' }, [sug.porque]),
    ]),
  ]);
}

function cartaoLead(lead, { abrirFatores = true } = {}) {
  const cabeca = el('button', {
    class: 'ilead__head',
    attrs: { type: 'button' },
    on: { click: () => hooks.go(`lead/${lead.id}`) },
  }, [
    seloScore(lead.score, lead.banda),
    el('span', { class: 'ilead__nome' }, [
      el('strong', {}, [lead.name]),
      el('span', { class: 'ilead__emp' }, [lead.company || '—']),
    ]),
    el('span', { class: 'ilead__val' }, [brl(lead.value)]),
    el('span', { class: 'chip chip--etapa' }, [lead.status]),
  ]);

  const filhos = [cabeca];
  if (lead.riscos?.length) filhos.push(listaRiscos(lead.riscos));
  const sug = blocoSugestao(lead.sugestao);
  if (sug) filhos.push(sug);

  if (abrirFatores && lead.fatores?.length) {
    filhos.push(el('details', { class: 'ilead__conta' }, [
      el('summary', {}, [`Por que ${lead.score} pontos`]),
      tabelaFatores(lead.fatores),
    ]));
  }

  return el('li', { class: 'ilead' }, filhos);
}

function renderPrevisao() {
  const alvo = document.getElementById('prev-corpo');
  const chip = document.getElementById('prev-origem');
  if (!alvo) return;
  clear(alvo);

  const p = store.previsao;
  if (!p) {
    alvo.append(emptyState({
      title: 'Sem previsão ainda',
      text: 'Cadastre negócios no funil para o Vertex conseguir projetar receita.',
      iconName: 'chart',
    }));
    if (chip) chip.textContent = '—';
    return;
  }

  if (chip) {
    chip.textContent = p.probabilidade_origem === 'padrao'
      ? 'Curva padrão do sistema'
      : p.probabilidade_origem === 'misto'
        ? 'Parte do seu histórico'
        : 'Do seu histórico';
    chip.className = p.probabilidade_origem === 'padrao' ? 'chip chip--warn' : 'chip chip--ok';
  }

  alvo.append(el('div', { class: 'prevnum' }, [
    el('div', { class: 'prevnum__i' }, [
      el('span', { class: 'prevnum__r' }, ['Já ganho']),
      el('strong', { class: 'prevnum__v' }, [brl(p.ganho)]),
      el('span', { class: 'prevnum__h' }, ['Negócios fechados']),
    ]),
    el('div', { class: 'prevnum__i' }, [
      el('span', { class: 'prevnum__r' }, ['Potencial']),
      el('strong', { class: 'prevnum__v' }, [brl(p.potencial)]),
      el('span', { class: 'prevnum__h' }, ['Tudo que está aberto']),
    ]),
    el('div', { class: 'prevnum__i prevnum__i--destaque' }, [
      el('span', { class: 'prevnum__r' }, ['Ponderado']),
      el('strong', { class: 'prevnum__v' }, [brl(p.ponderado)]),
      el('span', { class: 'prevnum__h' }, ['Estimativa, não promessa']),
    ]),
  ]));

  const linhas = p.linhas.filter((l) => l.negocios > 0);
  if (linhas.length) {
    alvo.append(el('div', { class: 'tablewrap' }, [
      el('table', { class: 'table' }, [
        el('thead', {}, [el('tr', {}, [
          el('th', { attrs: { scope: 'col' } }, ['Etapa']),
          el('th', { class: 'num', attrs: { scope: 'col' } }, ['Negócios']),
          el('th', { class: 'num', attrs: { scope: 'col' } }, ['Valor']),
          el('th', { class: 'num', attrs: { scope: 'col' } }, ['Chance']),
          el('th', { class: 'num', attrs: { scope: 'col' } }, ['Ponderado']),
        ])]),
        el('tbody', {}, linhas.map((l) => el('tr', {}, [
          el('td', {}, [l.etapa]),
          el('td', { class: 'num' }, [String(l.negocios)]),
          el('td', { class: 'num' }, [brl(l.valor)]),
          el('td', { class: 'num' }, [`${Math.round(l.probabilidade * 100)}%`]),
          el('td', { class: 'num' }, [brl(l.ponderado)]),
        ]))),
      ]),
    ]));
  }

  alvo.append(el('p', { class: 'prev__aviso' }, [icon('alert', 'ico'), el('span', {}, [p.aviso])]));
}

function renderPrioridades() {
  const alvo = document.getElementById('intel-prioridades');
  if (!alvo) return;
  clear(alvo);

  const dados = store.intel;
  if (!dados || !dados.prioridades.length) {
    alvo.append(emptyState({
      title: 'Nenhum negócio aberto',
      text: 'Quando houver negócio no funil, a lista de prioridades aparece aqui.',
      iconName: 'target',
    }));
    return;
  }

  const banda = store.intelBanda;
  const itens = banda ? dados.prioridades.filter((l) => l.banda === banda) : dados.prioridades;

  if (!itens.length) {
    alvo.append(emptyState({
      title: 'Nada nesta faixa',
      text: 'Nenhum negócio aberto está classificado nesta prioridade.',
      iconName: 'target',
    }));
    return;
  }

  alvo.append(el('ul', { class: 'ileads' }, itens.map((l) => cartaoLead(l))));
}

function renderRiscos() {
  const alvo = document.getElementById('intel-riscos');
  const chip = document.getElementById('intel-risco-valor');
  if (!alvo) return;
  clear(alvo);

  const dados = store.intel;
  if (!dados || !dados.riscos.length) {
    if (chip) { chip.textContent = 'Nada em risco'; chip.className = 'chip chip--ok'; }
    alvo.append(emptyState({
      title: 'Nada em risco agora',
      text: 'Nenhum negócio aberto disparou sinal de alerta.',
      iconName: 'check',
    }));
    return;
  }

  if (chip) {
    chip.textContent = `${brl(dados.valor_em_risco)} em risco`;
    chip.className = 'chip chip--warn';
  }
  alvo.append(el('ul', { class: 'ileads' },
    dados.riscos.map((l) => cartaoLead(l, { abrirFatores: false }))));
}

function renderAtivacao() {
  const alvo = document.getElementById('intel-ativacao');
  const chip = document.getElementById('ativ-chip');
  if (!alvo) return;
  clear(alvo);

  const a = store.ativacao;
  if (!a) return;
  if (chip) chip.textContent = `${a.concluidos} de ${a.total}`;

  a.marcos.forEach((m) => {
    alvo.append(el('li', { class: `marco ${m.em ? 'is-feito' : ''}` }, [
      el('span', { class: 'marco__caixa' }, m.em ? [icon('check', 'ico')] : []),
      el('span', {}, [ROTULO_MARCO[m.marco] || m.marco]),
    ]));
  });
}

const ATALHOS = [
  { rotulo: 'Como está o meu pipeline?', tarefa: 'resumo_desempenho' },
  { rotulo: 'Quem eu ligo primeiro hoje?', tarefa: 'pergunta', pergunta: 'Quem eu devo contatar primeiro hoje e por quê?' },
  { rotulo: 'Por que estou perdendo?', tarefa: 'pergunta', pergunta: 'Quais são os principais motivos de perda e o que eles têm em comum?' },
];

function renderIA() {
  const chip = document.getElementById('ia-chip');
  const aviso = document.getElementById('ia-aviso');
  const atalhos = document.getElementById('ia-atalhos');
  const form = document.getElementById('ia-form');
  const campo = document.getElementById('ia-pergunta');
  const enviar = document.getElementById('ia-enviar');
  if (!chip || !form) return;

  const painel = form.closest('.panel') || form.parentElement;
  const convite = painel?.querySelector('.bloq');
  if (!planoInclui('ia')) {
    if (!convite && painel) {
      painel.querySelectorAll(':scope > *:not(.bloq)').forEach((n) => { n.hidden = true; });
      painel.append(bloqueio(
        'ia',
        'O assistente responde sobre os seus próprios negócios: quem contatar primeiro, por que um negócio está parado, o que os perdidos têm em comum. Faz parte do plano Pro.',
      ));
    }
    return;
  }
  if (convite) {
    convite.remove();
    painel.querySelectorAll(':scope > *').forEach((n) => { n.hidden = false; });
  }

  const st = store.iaStatus;
  const ligada = !!st?.disponivel;

  chip.textContent = ligada ? `${st.usadas_hora}/${st.limite_hora} perguntas nesta hora` : 'Não configurado';
  chip.className = ligada ? 'chip chip--ok' : 'chip chip--warn';

  if (aviso) {

    aviso.textContent = ligada
      ? st.aviso_dados
      : 'O assistente não está ligado neste servidor. É preciso configurar uma chave de API no arquivo .env do servidor.';
  }

  campo.disabled = !ligada || store.iaOcupada;
  enviar.disabled = !ligada || store.iaOcupada;

  clear(atalhos);
  if (ligada) {
    ATALHOS.forEach((a) => {
      atalhos.append(el('button', {
        class: 'chip chip--acao',
        attrs: { type: 'button', disabled: store.iaOcupada },
        on: { click: () => perguntar(a.tarefa, a.pergunta || '', a.rotulo) },
      }, [a.rotulo]));
    });
  }

  renderConversa();
}

function renderConversa() {
  const log = document.getElementById('ia-log');
  if (!log) return;
  clear(log);

  if (!store.iaConversa.length) {

    const marca = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    marca.setAttribute('class', 'ico ia__vazio-ico');
    marca.setAttribute('aria-hidden', 'true');
    const uso = document.createElementNS('http://www.w3.org/2000/svg', 'use');
    uso.setAttribute('href', '#i-spark');
    marca.append(uso);
    log.append(el('div', { class: 'ia__vazio' }, [
      marca,
      el('p', {}, ['Pergunte sobre os seus negócios.']),
      el('p', { class: 'ia__vazio-sub' }, [
        'O assistente responde com base no que existe no seu CRM — e diz quando não sabe.',
      ]),
    ]));
    return;
  }

  store.iaConversa.forEach((m) => {
    const bolha = el('div', { class: `ia__msg ia__msg--${m.de}` }, []);

    String(m.texto).split('\n').forEach((linha) => {
      if (linha.trim()) bolha.append(el('p', {}, [linha]));
    });
    if (m.de === 'vertex' && m.tokens) {
      bolha.append(el('span', { class: 'ia__meta' }, [
        `Gerado a partir dos dados desta conta · ${m.tokens} tokens`,
      ]));
    }
    log.append(bolha);
  });

  if (store.iaOcupada) {
    log.append(el('div', { class: 'ia__msg ia__msg--vertex ia__msg--pensando' }, [
      el('p', {}, ['Consultando os seus dados…']),
    ]));
  }
  log.scrollTop = log.scrollHeight;
}

async function perguntar(tarefa, pergunta, rotulo) {
  if (store.iaOcupada) return;
  const texto = pergunta || rotulo || '';
  store.iaConversa.push({ de: 'voce', texto });
  store.iaOcupada = true;
  renderIA();

  try {
    const r = await api.iaPerguntar({ tarefa, pergunta });
    store.iaConversa.push({ de: 'vertex', texto: r.texto, tokens: r.tokens });
    try {
      store.iaStatus = await api.iaStatus();
    } catch {  }
  } catch (err) {
    if (err?.status !== 401) {
      store.iaConversa.push({
        de: 'vertex',
        texto: err?.message || 'Não consegui responder agora.',
      });
      toast(err?.message || 'Falha ao falar com o assistente.', 'error');
    }
  } finally {
    store.iaOcupada = false;
    renderIA();
  }
}

function tabelaRecorte(titulo, linhas) {
  if (!linhas.length) return null;
  return el('div', { class: 'adv__bloco' }, [
    el('h3', { class: 'adv__h' }, [titulo]),
    el('div', { class: 'tablewrap' }, [
      el('table', { class: 'table' }, [
        el('thead', {}, [el('tr', {}, [
          el('th', { attrs: { scope: 'col' } }, [titulo]),
          el('th', { class: 'num', attrs: { scope: 'col' } }, ['Leads']),
          el('th', { class: 'num', attrs: { scope: 'col' } }, ['Ganhos']),
          el('th', { class: 'num', attrs: { scope: 'col' } }, ['Conversão']),
          el('th', { class: 'num', attrs: { scope: 'col' } }, ['Receita']),
          el('th', { class: 'num', attrs: { scope: 'col' } }, ['Ticket médio']),
        ])]),
        el('tbody', {}, linhas.map((l) => el('tr', {}, [
          el('td', {}, [l.rotulo]),
          el('td', { class: 'num' }, [String(l.total)]),
          el('td', { class: 'num' }, [String(l.ganhos)]),
          el('td', { class: 'num' }, [`${l.conversao}%`]),
          el('td', { class: 'num' }, [brl(l.valor_ganho)]),
          el('td', { class: 'num' }, [l.ticket_medio ? brl(l.ticket_medio) : '—']),
        ]))),
      ]),
    ]),
  ]);
}

export function renderAvancado() {
  const alvo = document.getElementById('adv-report');
  if (!alvo) return;
  clear(alvo);

  if (!planoInclui('relatorios_avancados')) {
    alvo.append(bloqueio(
      'relatorios_avancados',
      'Conversão por origem, por segmento e por etapa, com o tempo médio que cada negócio leva para andar. Faz parte do plano Pro.',
    ));
    return;
  }

  const a = store.avancado;
  if (!a || !a.tem_dados) {
    alvo.append(emptyState({
      title: 'Sem dados neste período',
      text: 'Nenhum lead foi criado na janela escolhida. Experimente um período maior.',
      iconName: 'chart',
    }));
    return;
  }

  alvo.append(el('div', { class: 'comparacao' }, a.comparacao.map((c) => {
    const semBase = c.variacao === null || c.variacao === undefined;
    const sobe = !semBase && c.variacao > 0;
    const desce = !semBase && c.variacao < 0;
    const ehDinheiro = /Receita|Ticket/.test(c.rotulo);
    const ehPct = c.rotulo.includes('%');
    const formata = (v) => (ehDinheiro ? brl(v) : ehPct ? `${v}%` : String(Math.round(v)));
    return el('div', { class: 'comp' }, [
      el('span', { class: 'comp__r' }, [c.rotulo]),
      el('strong', { class: 'comp__v' }, [formata(c.atual)]),
      el('span', {
        class: `comp__d ${sobe ? 'is-sobe' : ''} ${desce ? 'is-desce' : ''}`,
      }, [
        semBase

          ? 'sem período anterior'
          : `${c.variacao > 0 ? '+' : ''}${c.variacao}% vs. período anterior`,
      ]),
    ]);
  })));

  if (a.tempo_medio_fechamento !== null && a.tempo_medio_fechamento !== undefined) {
    alvo.append(el('p', { class: 'adv__nota' }, [
      `Tempo médio entre criar e ganhar: ${a.tempo_medio_fechamento} dias.`,
    ]));
  }

  [
    tabelaRecorte('Origem', a.por_origem),
    tabelaRecorte('Segmento', a.por_segmento),
    tabelaRecorte('Responsável', a.por_responsavel),
  ].filter(Boolean).forEach((n) => alvo.append(n));

  if (a.tempo_por_etapa?.length) {
    alvo.append(el('div', { class: 'adv__bloco' }, [
      el('h3', { class: 'adv__h' }, ['Tempo médio em cada etapa']),
      el('ul', { class: 'etapatempo' }, a.tempo_por_etapa.map((e) => el('li', {}, [
        el('span', {}, [e.etapa]),
        el('strong', {}, [`${e.dias_medios} dias`]),
        el('span', { class: 'etapatempo__n' }, [`${e.transicoes} passagens`]),
      ]))),
    ]));
  }
}

export function renderIntel() {
  renderPrevisao();
  renderPrioridades();
  renderRiscos();
  renderAtivacao();
  renderIA();
}

export function ligarIntel() {
  document.querySelectorAll('#page-inteligencia [data-banda]').forEach((b) => {
    b.addEventListener('click', () => {
      store.intelBanda = b.dataset.banda;
      document.querySelectorAll('#page-inteligencia [data-banda]')
        .forEach((o) => o.classList.toggle('is-on', o === b));
      renderPrioridades();
    });
  });

  document.querySelectorAll('#page-relatorios [data-periodo]').forEach((b) => {
    b.addEventListener('click', async () => {
      document.querySelectorAll('#page-relatorios [data-periodo]')
        .forEach((o) => o.classList.toggle('is-on', o === b));
      await loadAvancado(b.dataset.periodo);
      renderAvancado();
    });
  });

  const form = document.getElementById('ia-form');
  form?.addEventListener('submit', (ev) => {
    ev.preventDefault();
    const campo = document.getElementById('ia-pergunta');
    const texto = (campo.value || '').trim();
    if (!texto) return;
    campo.value = '';
    perguntar('pergunta', texto);
  });
}
