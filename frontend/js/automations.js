import * as api from './api.js';
import { el, icon, clear, toast, openModal, closeModal, confirmar, emptyState } from './ui.js';
import { store, hooks, quando, relativo } from './store.js';

export async function loadAutomations() {
  const [meta, regras, execucoes] = await Promise.allSettled([
    store.automationMeta ? Promise.resolve(store.automationMeta) : api.automationMeta(),
    api.listAutomations(),
    api.automationRuns(),
  ]);
  if (meta.status === 'fulfilled') store.automationMeta = meta.value;
  if (regras.status === 'fulfilled') store.automations = regras.value;
  if (execucoes.status === 'fulfilled') store.automationRuns = execucoes.value;
}

const meta = () => store.automationMeta || {
  events: [], fields: [], operators: [], actions: [],
  statuses: [], segments: [], updatable_fields: [], templates: [],
  max_actions: 8, max_conditions: 8,
};

const rotuloDe = (lista, valor) =>
  lista.find((i) => i.value === valor)?.label || valor;

function frase(regra) {
  const m = meta();
  const quando_ = rotuloDe(m.events, regra.event);
  const acoes = (regra.actions || []).map((a) => rotuloDe(m.actions, a.tipo).toLowerCase());
  const condicoes = (regra.conditions || []).length;
  const se = condicoes ? ` (com ${condicoes} condição${condicoes > 1 ? 'ões' : ''})` : '';
  return `Quando ${quando_.toLowerCase()}${se}, ${acoes.join(' e ') || 'nada'}.`;
}

function cartao(regra) {
  const ativa = regra.active;
  return el('article', { class: `bezel auto tilt3d${ativa ? '' : ' is-off'}` }, [
    el('div', { class: 'bezel__in auto__in' }, [
      el('div', { class: 'auto__top' }, [
        el('span', { class: 'auto__bolt' }, [icon('bolt')]),
        el('div', { class: 'auto__names' }, [
          el('strong', { class: 'auto__name', text: regra.name }),
          el('p', { class: 'auto__frase', text: frase(regra) }),
        ]),
        el('span', { class: `pill pill--${ativa ? 'ok' : 'neutra'}`, text: ativa ? 'Ativa' : 'Pausada' }),
      ]),
      el('div', { class: 'auto__foot' }, [
        el('span', {
          class: 'auto__runs',
          text: regra.run_count
            ? `Rodou ${regra.run_count}× · última ${relativo(regra.last_run_at)}`
            : 'Ainda não rodou',
        }),
        el('div', { class: 'auto__acts' }, [
          el('button', { class: 'btn btn--quiet btn--xs', attrs: { type: 'button', 'data-auto-toggle': regra.id } },
            [icon(ativa ? 'pause' : 'play'), el('span', { text: ativa ? 'Pausar' : 'Ativar' })]),
          el('button', { class: 'btn btn--quiet btn--xs', attrs: { type: 'button', 'data-auto-edit': regra.id } },
            [icon('pencil'), el('span', { text: 'Editar' })]),
          el('button', {
            class: 'iconbtn iconbtn--xs',
            attrs: { type: 'button', 'data-auto-del': regra.id, 'aria-label': `Excluir “${regra.name}”` },
          }, [icon('trash')]),
        ]),
      ]),
    ]),
  ]);
}

function renderRuns() {
  const alvo = document.getElementById('auto-runs');
  clear(alvo);
  if (!store.automationRuns.length) {
    alvo.append(el('p', { class: 'panel__body', text: 'Nada rodou ainda. O histórico aparece aqui assim que a primeira regra disparar.' }));
    return;
  }
  const tom = { ok: 'ok', parcial: 'atencao', erro: 'ruim' };
  for (const execucao of store.automationRuns) {
    alvo.append(el('div', { class: `run run--${execucao.status}` }, [
      el('span', { class: `pill pill--${tom[execucao.status] || 'neutra'}`, text: execucao.status }),
      el('div', { class: 'run__txt' }, [
        el('strong', { text: execucao.automation_name || 'Automação removida' }),
        el('span', { text: `${execucao.event_label}${execucao.lead_name ? ` · ${execucao.lead_name}` : ''}` }),
        el('small', { text: execucao.error || execucao.summary }),
      ]),
      el('span', { class: 'run__when', text: quando(execucao.created_at) }),
    ]));
  }
}

export function renderAutomations() {
  const lista = document.getElementById('auto-list');
  clear(lista);

  if (!store.automations.length) {
    lista.append(emptyState({
      title: 'Nenhuma automação ainda',
      text: 'Escreva a regra uma vez e o Vertex passa a agir sozinho: cobrar proposta parada, criar tarefa quando o negócio avança, avisar quando algo trava.',
      iconName: 'bolt',
      action: { label: 'Criar a primeira', onClick: () => abrirAutomacao(null) },
    }));
  } else {
    for (const regra of store.automations) lista.append(cartao(regra));
  }
  renderRuns();
}

function linhaCondicao(condicao = {}) {
  const m = meta();
  const campo = el('select', { class: 'cond__field', attrs: { 'aria-label': 'Campo' } },
    m.fields.map((f) => el('option', { attrs: { value: f.value, selected: f.value === condicao.campo }, text: f.label })));
  const operador = el('select', { class: 'cond__op', attrs: { 'aria-label': 'Comparação' } },
    m.operators.map((o) => el('option', {
      attrs: { value: o, selected: o === condicao.operador },
      text: { igual: 'é', diferente: 'não é', contem: 'contém', nao_contem: 'não contém', maior: 'maior que', menor: 'menor que' }[o] || o,
    })));
  const valor = el('input', { class: 'cond__val', attrs: { type: 'text', maxlength: 120, value: condicao.valor || '', placeholder: 'valor', 'aria-label': 'Valor' } });

  return el('div', { class: 'cond' }, [
    campo, operador, valor,
    el('button', { class: 'iconbtn iconbtn--xs', attrs: { type: 'button', 'data-rm-cond': '', 'aria-label': 'Remover condição' } }, [icon('close')]),
  ]);
}

function camposDaAcao(tipo, acao = {}) {
  const m = meta();
  const campos = [];
  const texto = (classe, chave, placeholder, tamanho = 160) => el('input', {
    class: `act__${classe}`,
    attrs: { type: 'text', maxlength: tamanho, value: acao[chave] || '', placeholder, 'aria-label': placeholder },
  });

  switch (tipo) {
    case 'criar_tarefa':
    case 'criar_followup':
      campos.push(texto('titulo', 'titulo', tipo === 'criar_tarefa' ? 'O que fazer' : 'Título do follow-up'));
      campos.push(el('label', { class: 'act__dias' }, [
        el('span', { text: 'em' }),
        el('input', { class: 'act__diasval', attrs: { type: 'number', min: 0, max: 365, value: acao.dias ?? 1, 'aria-label': 'Dias até vencer' } }),
        el('span', { text: 'dia(s)' }),
      ]));
      break;
    case 'mudar_etapa':
      campos.push(el('select', { class: 'act__status', attrs: { 'aria-label': 'Etapa' } },
        m.statuses.filter((s) => s !== 'Perdido').map((s) =>
          el('option', { attrs: { value: s, selected: s === acao.status }, text: s }))));
      campos.push(el('small', { class: 'act__nota', text: 'Perdido não entra aqui: o motivo é obrigatório e só uma pessoa sabe qual é.' }));
      break;
    case 'alterar_responsavel':
      campos.push(texto('valor', 'valor', 'Nome do responsável', 80));
      break;
    case 'adicionar_tag':
    case 'remover_tag':
      campos.push(texto('valor', 'valor', 'Nome da tag', 40));
      break;
    case 'notificar':
      campos.push(texto('titulo', 'titulo', 'Título do aviso'));
      campos.push(texto('texto', 'texto', 'Texto (opcional)', 500));
      break;
    case 'registrar_atividade':
      campos.push(texto('titulo', 'titulo', 'O que registrar'));
      campos.push(texto('texto', 'texto', 'Detalhe (opcional)', 500));
      break;
    case 'atualizar_dado':
      campos.push(el('select', { class: 'act__campo', attrs: { 'aria-label': 'Campo' } },
        m.updatable_fields.map((f) => el('option', { attrs: { value: f, selected: f === acao.campo }, text: f }))));
      campos.push(texto('valor', 'valor', 'Novo valor', 120));
      break;
    case 'enviar_whatsapp':
      if (m.templates.length) {
        campos.push(el('select', { class: 'act__template', attrs: { 'aria-label': 'Modelo' } }, [
          el('option', { attrs: { value: '' }, text: 'Sem modelo (texto livre)' }),
          ...m.templates.map((t) => el('option', { attrs: { value: t, selected: t === acao.template }, text: t })),
        ]));
      }
      campos.push(texto('texto', 'texto', 'Mensagem', 500));
      campos.push(el('small', { class: 'act__nota', text: 'Fora da janela de 24h, a Meta só entrega por modelo aprovado.' }));
      break;
    default:
      break;
  }
  return campos;
}

function linhaAcao(acao = {}) {
  const m = meta();
  const corpo = el('div', { class: 'act__body' }, camposDaAcao(acao.tipo || m.actions[0]?.value, acao));
  const tipo = el('select', { class: 'act__type', attrs: { 'aria-label': 'Ação' } },
    m.actions.map((a) => el('option', { attrs: { value: a.value, selected: a.value === acao.tipo }, text: a.label })));

  tipo.addEventListener('change', () => {
    clear(corpo);
    for (const campo of camposDaAcao(tipo.value)) corpo.append(campo);
  });

  return el('div', { class: 'act' }, [
    el('div', { class: 'act__head' }, [
      tipo,
      el('button', { class: 'iconbtn iconbtn--xs', attrs: { type: 'button', 'data-rm-act': '', 'aria-label': 'Remover ação' } }, [icon('close')]),
    ]),
    corpo,
  ]);
}

function lerCondicoes() {
  return [...document.querySelectorAll('#auto-conditions .cond')].map((linha) => ({
    campo: linha.querySelector('.cond__field').value,
    operador: linha.querySelector('.cond__op').value,
    valor: linha.querySelector('.cond__val').value.trim(),
  })).filter((c) => c.valor !== '' || ['tem_whatsapp'].includes(c.campo));
}

function lerAcoes() {
  return [...document.querySelectorAll('#auto-actions .act')].map((linha) => {
    const acao = { tipo: linha.querySelector('.act__type').value };
    const pega = (classe, chave) => {
      const campo = linha.querySelector(`.${classe}`);
      if (campo) acao[chave] = campo.value;
    };
    pega('act__titulo', 'titulo');
    pega('act__texto', 'texto');
    pega('act__valor', 'valor');
    pega('act__status', 'status');
    pega('act__campo', 'campo');
    pega('act__template', 'template');
    const dias = linha.querySelector('.act__diasval');
    if (dias) acao.dias = Number(dias.value) || 0;
    return acao;
  });
}

function explicarEvento() {
  const valor = document.getElementById('auto-event').value;
  const evento = meta().events.find((e) => e.value === valor);
  const hint = document.getElementById('auto-event-hint');
  if (!evento) { hint.textContent = ''; return; }
  hint.textContent = evento.tipo === 'tempo'
    ? 'Verificado a cada 5 minutos pelo servidor. Roda no máximo uma vez por dia para o mesmo negócio.'
    : 'Dispara na hora em que a ação acontece.';
}

export function abrirAutomacao(id) {
  const m = meta();
  const regra = id ? store.automations.find((a) => String(a.id) === String(id)) : null;
  const modal = document.getElementById('auto-modal');

  document.getElementById('auto-modal-title').textContent = regra ? 'Editar automação' : 'Nova automação';
  document.getElementById('auto-error').hidden = true;
  document.getElementById('auto-id').value = regra?.id || '';
  document.getElementById('auto-name').value = regra?.name || '';
  document.getElementById('auto-active').checked = regra ? regra.active : true;

  const eventos = document.getElementById('auto-event');
  clear(eventos);
  for (const grupo of ['acao', 'tempo']) {
    const lista = m.events.filter((e) => e.tipo === grupo);
    if (!lista.length) continue;
    eventos.append(el('optgroup', { attrs: { label: grupo === 'acao' ? 'Quando alguém faz algo' : 'Quando o tempo passa' } },
      lista.map((e) => el('option', { attrs: { value: e.value, selected: e.value === regra?.event }, text: e.label }))));
  }
  explicarEvento();

  const condicoes = document.getElementById('auto-conditions');
  clear(condicoes);
  for (const c of regra?.conditions || []) condicoes.append(linhaCondicao(c));

  const acoes = document.getElementById('auto-actions');
  clear(acoes);
  const lista = regra?.actions?.length ? regra.actions : [{}];
  for (const a of lista) acoes.append(linhaAcao(a));

  openModal(modal, { focus: '#auto-name' });
}

async function salvarAutomacao(evento) {
  evento.preventDefault();
  const erro = document.getElementById('auto-error');
  const botao = document.getElementById('auto-save');
  erro.hidden = true;

  const id = document.getElementById('auto-id').value;
  const nome = document.getElementById('auto-name').value.trim();
  if (!nome) {
    erro.textContent = 'Dê um nome para a automação.';
    erro.hidden = false;
    return;
  }
  const acoes = lerAcoes();
  if (!acoes.length) {
    erro.textContent = 'A automação precisa de pelo menos uma ação.';
    erro.hidden = false;
    return;
  }

  const corpo = {
    name: nome,
    event: document.getElementById('auto-event').value,
    conditions: lerCondicoes(),
    actions: acoes,
    active: document.getElementById('auto-active').checked,
  };

  botao.disabled = true;
  try {
    if (id) await api.updateAutomation(id, corpo);
    else await api.createAutomation(corpo);
    toast(id ? 'Automação atualizada.' : 'Automação criada.', 'success');
    closeModal();
    await loadAutomations();
    renderAutomations();
  } catch (err) {
    if (err?.status !== 401) {
      erro.textContent = err?.message || 'Não foi possível salvar.';
      erro.hidden = false;
    }
  } finally {
    botao.disabled = false;
  }
}

export function wireAutomations() {
  document.getElementById('auto-form')?.addEventListener('submit', salvarAutomacao);
  document.getElementById('auto-event')?.addEventListener('change', explicarEvento);
  document.getElementById('new-automation-btn')?.addEventListener('click', () => abrirAutomacao(null));

  document.getElementById('auto-add-cond')?.addEventListener('click', () => {
    const alvo = document.getElementById('auto-conditions');
    if (alvo.children.length >= meta().max_conditions) {
      toast(`No máximo ${meta().max_conditions} condições.`, 'info');
      return;
    }
    alvo.append(linhaCondicao());
  });

  document.getElementById('auto-add-action')?.addEventListener('click', () => {
    const alvo = document.getElementById('auto-actions');
    if (alvo.children.length >= meta().max_actions) {
      toast(`No máximo ${meta().max_actions} ações.`, 'info');
      return;
    }
    alvo.append(linhaAcao());
  });

  document.getElementById('auto-modal')?.addEventListener('click', (evento) => {
    const cond = evento.target.closest('[data-rm-cond]');
    if (cond) { cond.closest('.cond').remove(); return; }
    const act = evento.target.closest('[data-rm-act]');
    if (act) {
      const alvo = document.getElementById('auto-actions');
      if (alvo.children.length > 1) act.closest('.act').remove();
      else toast('A automação precisa de pelo menos uma ação.', 'info');
    }
  });

  document.getElementById('page-automacoes')?.addEventListener('click', async (evento) => {
    const editar = evento.target.closest('[data-auto-edit]');
    if (editar) { abrirAutomacao(editar.dataset.autoEdit); return; }

    const alternar = evento.target.closest('[data-auto-toggle]');
    if (alternar) {
      const regra = store.automations.find((a) => String(a.id) === String(alternar.dataset.autoToggle));
      if (!regra) return;
      try {
        await api.updateAutomation(regra.id, { active: !regra.active });
        await loadAutomations();
        renderAutomations();
      } catch (err) {
        if (err?.status !== 401) toast(err?.message || 'Não foi possível alterar.', 'error');
      }
      return;
    }

    const apagar = evento.target.closest('[data-auto-del]');
    if (apagar) {
      const regra = store.automations.find((a) => String(a.id) === String(apagar.dataset.autoDel));
      if (!regra) return;
      const ok = await confirmar({
        titulo: 'Excluir esta automação?',
        texto: 'Ela para de rodar. O histórico do que já rodou continua guardado.',
        alvo: { nome: regra.name, meta: frase(regra) },
        confirmar: 'Excluir automação',
      });
      if (!ok) return;
      try {
        await api.deleteAutomation(regra.id);
        toast('Automação excluída.', 'info');
        await loadAutomations();
        renderAutomations();
      } catch (err) {
        if (err?.status !== 401) toast(err?.message || 'Não foi possível excluir.', 'error');
      }
    }
  });
}
