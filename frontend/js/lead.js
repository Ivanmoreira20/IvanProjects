import * as api from './api.js';
import { planoInclui } from './cobranca.js';
import {
  el, icon, clear, brl, initials, toast, openModal, closeModal, confirmar, emptyState,
} from './ui.js';
import {
  store, hooks, OPEN_STATUSES, STATUSES, ACTIVITY_META, CONTACT_KINDS,
  quando, relativo, venceu,
} from './store.js';

export function currentLead() {
  return (hooks.state?.leads || []).find((l) => String(l.id) === String(store.leadId)) || null;
}

export async function loadLead(id) {
  store.leadId = id;

  const [ativ, props, chat, neg] = await Promise.allSettled([
    api.leadActivities(id),
    planoInclui('propostas') ? api.listProposals(id) : Promise.resolve([]),
    planoInclui('whatsapp') ? api.leadConversation(id) : Promise.resolve(null),
    api.negociacao(id),
  ]);
  if ([ativ, props, chat, neg].some((r) => r.status === 'rejected' && r.reason?.status === 401)) return;

  if (ativ.status === 'rejected' && ativ.reason?.status === 404) {
    toast('Lead não encontrado.', 'error');
    hooks.go('leads');
    return;
  }

  store.leadActivities = ativ.status === 'fulfilled' ? ativ.value : [];
  store.leadProposals = props.status === 'fulfilled' ? props.value : [];
  store.leadChat = chat.status === 'fulfilled' ? chat.value : null;
  store.leadNegociacao = neg.status === 'fulfilled' ? neg.value : null;
}

function renderHead(lead) {
  document.getElementById('lead-initials').textContent = initials(lead.name);
  document.getElementById('lead-title').textContent = lead.name;
  document.getElementById('lead-subtitle').textContent =
    [lead.company, lead.segment, lead.source].filter(Boolean).join(' · ');
  document.getElementById('lead-amount').textContent = brl(lead.value);

  const partes = [];
  if (lead.owner) partes.push(`Responsável: ${lead.owner}`);
  partes.push(lead.last_activity_at
    ? `Último contato ${relativo(lead.last_activity_at)}`
    : 'Nenhum contato registrado');

  const neg = store.leadNegociacao;
  if (neg && neg.variacao !== 0) {
    const seta = neg.variacao < 0 ? '↓' : '↑';
    partes.push(`De ${brl(neg.valor_inicial)} · ${seta} ${Math.abs(neg.variacao_pct)}%`);
  }
  document.getElementById('lead-meta').textContent = partes.join(' · ');

  const tags = document.getElementById('lead-tags');
  clear(tags);
  for (const tag of lead.tags || []) tags.append(el('span', { class: 'tag', text: tag }));

  const fechado = lead.status === 'Ganho' || lead.status === 'Perdido';
  document.getElementById('lead-win').hidden = fechado;
  document.getElementById('lead-lose').hidden = fechado;

  const perdaCard = document.getElementById('lead-lost-card');
  perdaCard.hidden = lead.status !== 'Perdido';
  if (lead.status === 'Perdido') {
    document.getElementById('lead-lost-reason').textContent = lead.lost_reason || 'Sem motivo informado';
    const nota = document.getElementById('lead-lost-note');
    nota.textContent = lead.lost_note || '';
    nota.hidden = !lead.lost_note;
  }

  renderOwnerControl(lead);
}

async function renderOwnerControl(lead) {
  const box = document.getElementById('lead-owner-ctl');
  if (!box) return;
  const papel = hooks.state?.user?.role;
  if (papel !== 'admin' && papel !== 'gestor') { clear(box); box.hidden = true; return; }

  let membros = [];
  try { membros = (await api.org()).members || []; } catch { membros = []; }

  clear(box);
  box.hidden = false;
  const sel = el('select', {
    class: 'owner-select', attrs: { 'aria-label': 'Dono do negócio' },
    on: { change: (e) => reatribuirDono(lead.id, e.target.value) },
  }, [
    el('option', { text: 'Sem dono', attrs: { value: '', selected: !lead.owner_user_id } }),
    ...membros.map((m) => el('option', {
      text: m.name,
      attrs: { value: String(m.user_id), selected: String(m.user_id) === String(lead.owner_user_id) },
    })),
  ]);
  box.append(el('span', { class: 'leadowner__label', text: 'Dono' }), sel);
}

async function reatribuirDono(leadId, value) {
  const ownerId = value === '' ? null : Number(value);
  try {
    const atualizado = await api.assignLeadOwner(leadId, ownerId);
    const leads = hooks.state?.leads || [];
    const i = leads.findIndex((l) => String(l.id) === String(leadId));
    if (i >= 0) leads[i] = { ...leads[i], ...atualizado };
    toast('Dono do negócio atualizado.', 'success');
    hooks.renderCurrentPage?.();
  } catch (e) {
    toast(e.message || 'Não foi possível reatribuir.', 'error');
  }
}

function renderStageBar(lead) {
  const barra = document.getElementById('lead-stagebar');
  clear(barra);

  if (lead.status === 'Perdido') {
    barra.append(el('p', { class: 'stagebar__closed stagebar__closed--lost' }, [
      icon('x-circle'),
      el('span', { text: `Perdido — ${lead.lost_reason || 'sem motivo informado'}` }),
    ]));
    return;
  }
  if (lead.status === 'Ganho') {
    barra.append(el('p', { class: 'stagebar__closed stagebar__closed--won' }, [
      icon('trophy'), el('span', { text: 'Negócio ganho' }),
    ]));
    return;
  }

  const atual = OPEN_STATUSES.indexOf(lead.status);
  OPEN_STATUSES.forEach((etapa, indice) => {
    const estado = indice < atual ? 'is-done' : indice === atual ? 'is-now' : 'is-next';
    barra.append(el('button', {
      class: `stage ${estado}`,
      attrs: {
        type: 'button',
        'data-stage': etapa,
        'aria-current': indice === atual ? 'step' : false,
        title: indice === atual ? `Etapa atual: ${etapa}` : `Mover para ${etapa}`,
      },
    }, [
      el('span', { class: 'stage__face' }, [
        el('span', { class: 'stage__n', text: String(indice + 1) }),
        el('span', { class: 'stage__label', text: etapa }),
      ]),
    ]));
  });
}

function passaNoFiltro(item) {
  if (store.tlFilter === 'todos') return true;
  if (store.tlFilter === 'contato') return CONTACT_KINDS.has(item.kind);
  return item.source !== 'user';
}

function linhaDoTempo(item) {
  const meta = ACTIVITY_META[item.kind] || ACTIVITY_META.nota;
  const pendente = item.pendente;
  const atrasada = pendente && venceu(item.due_at);

  const corpo = [
    el('div', { class: 'tl__top' }, [
      el('strong', { class: 'tl__title', text: item.title }),
      el('span', { class: 'tl__when', text: quando(item.created_at) }),
    ]),
  ];

  if (item.detail) corpo.push(el('p', { class: 'tl__detail', text: item.detail }));

  const rodape = [el('span', { class: 'tl__kind', text: meta.rotulo })];

  if (item.source === 'automation') rodape.push(el('span', { class: 'tl__by tl__by--auto' }, [icon('bolt'), el('span', { text: 'automação' })]));
  else if (item.source === 'system') rodape.push(el('span', { class: 'tl__by', text: 'registro automático' }));
  else if (item.source === 'whatsapp') rodape.push(el('span', { class: 'tl__by' }, [icon('whats'), el('span', { text: 'WhatsApp' })]));

  if (item.due_at) {
    rodape.push(el('span', {
      class: `tl__due${atrasada ? ' is-late' : ''}${item.done_at ? ' is-done' : ''}`,
      text: item.done_at ? `concluída ${relativo(item.done_at)}` : `vence ${relativo(item.due_at)}`,
    }));
  }

  const acoes = [];
  if (pendente) {
    acoes.push(el('button', {
      class: 'btn btn--quiet btn--xs',
      attrs: { type: 'button', 'data-done': item.id },
    }, [icon('check'), el('span', { text: 'Concluir' })]));
  }
  if (item.source === 'user') {
    acoes.push(el('button', {
      class: 'iconbtn iconbtn--xs',
      attrs: { type: 'button', 'data-del-act': item.id, 'aria-label': `Apagar “${item.title}”` },
    }, [icon('trash')]));
  }

  corpo.push(el('div', { class: 'tl__foot' }, [el('div', { class: 'tl__meta' }, rodape), el('div', { class: 'tl__acts' }, acoes)]));

  return el('li', { class: `tl tl--${meta.cor}${atrasada ? ' tl--late' : ''}` }, [
    el('span', { class: 'tl__dot', attrs: { 'aria-hidden': 'true' } }, [icon(meta.icone.replace(/^i-/, ''))]),
    el('div', { class: 'tl__card' }, corpo),
  ]);
}

function renderTimeline() {
  const lista = document.getElementById('lead-timeline');
  clear(lista);
  const itens = store.leadActivities.filter(passaNoFiltro);

  if (!itens.length) {
    lista.append(el('li', { class: 'tl tl--empty' }, [
      emptyState({
        title: store.leadActivities.length ? 'Nada neste filtro' : 'Nenhum registro ainda',
        text: store.leadActivities.length
          ? 'Troque o filtro acima para ver o resto do histórico.'
          : 'Registre a primeira ligação, reunião ou anotação no campo acima.',
        iconName: 'clock',
      }),
    ]));
    return;
  }
  for (const item of itens) lista.append(linhaDoTempo(item));
}

function linhaDef(rotulo, valor, extra) {
  if (!valor) return null;
  return el('div', { class: 'deflist__row' }, [
    el('dt', { text: rotulo }),
    el('dd', {}, [extra || document.createTextNode(String(valor))]),
  ]);
}

function renderContato(lead) {
  const dl = document.getElementById('lead-contact');
  clear(dl);
  const linhas = [
    linhaDef('Empresa', lead.company),
    linhaDef('E-mail', lead.email, lead.email
      ? el('a', { attrs: { href: `mailto:${lead.email}` }, text: lead.email })
      : null),
    linhaDef('Telefone', lead.phone),
    linhaDef('WhatsApp', lead.whatsapp),
    linhaDef('Origem', lead.source),
    linhaDef('Responsável', lead.owner),
    linhaDef('Criado em', quando(lead.created_at)),
  ].filter(Boolean);

  if (!linhas.length) {
    dl.append(el('p', { class: 'panel__body', text: 'Sem dados de contato. Use "Editar" para preencher.' }));
    return;
  }
  for (const linha of linhas) dl.append(linha);

  if (lead.notes) {
    dl.append(el('div', { class: 'deflist__row deflist__row--notes' }, [
      el('dt', { text: 'Observações' }),
      el('dd', { text: lead.notes }),
    ]));
  }
}

function valorLegivel(campo, valor) {
  if (valor == null || valor === '') return '';
  if (campo.type === 'sim_nao') return valor ? 'Sim' : 'Não';
  if (campo.type === 'multipla') return [].concat(valor).join(', ');
  if (campo.type === 'moeda') return brl(Number(valor) || 0);
  if (campo.type === 'data') {
    const [a, m, d] = String(valor).split('-');
    return a && m && d ? `${d}/${m}/${a}` : String(valor);
  }
  return String(valor);
}

function renderCustom(lead) {
  const card = document.getElementById('lead-custom-card');
  const dl = document.getElementById('lead-custom');
  const campos = (store.customFields || []).filter((c) => c.active);
  clear(dl);

  if (!campos.length) { card.hidden = true; return; }
  card.hidden = false;

  let algum = false;
  for (const campo of campos) {
    const texto = valorLegivel(campo, (lead.custom || {})[campo.key]);
    if (!texto) continue;
    algum = true;
    dl.append(el('div', { class: 'deflist__row' }, [
      el('dt', { text: campo.label }), el('dd', { text: texto }),
    ]));
  }
  if (!algum) {
    dl.append(el('p', { class: 'panel__body', text: 'Nenhum campo preenchido para este lead.' }));
  }
}

function renderTasks() {
  const alvo = document.getElementById('lead-tasks');
  clear(alvo);
  const lead = currentLead();
  const aberto = lead && lead.status !== 'Ganho' && lead.status !== 'Perdido';

  const tarefas = store.leadActivities
    .filter((a) => a.pendente)
    .sort((a, b) => String(a.due_at || '').localeCompare(String(b.due_at || '')));

  if (!tarefas.length) {

    if (aberto) {
      alvo.append(el('div', { class: 'nextaction nextaction--vazia' }, [
        icon('alert'),
        el('div', { class: 'nextaction__txt' }, [
          el('strong', { text: 'Sem próxima ação' }),
          el('small', { text: 'Agende o próximo passo para este negócio não parar.' }),
        ]),
      ]));
    } else {
      alvo.append(el('p', { class: 'panel__body', text: 'Nenhuma tarefa em aberto.' }));
    }
    return;
  }

  tarefas.forEach((t, indice) => {
    const proxima = indice === 0;
    const classe = `task${venceu(t.due_at) ? ' is-late' : ''}${proxima ? ' is-next' : ''}`;
    alvo.append(el('div', { class: classe }, [
      el('button', {
        class: 'task__check',
        attrs: { type: 'button', 'data-done': t.id, 'aria-label': `Concluir “${t.title}”` },
      }, [icon('check')]),
      el('span', { class: 'task__txt' }, [
        proxima ? el('span', { class: 'task__badge', text: 'Próxima ação' }) : null,
        el('strong', { text: t.title }),
        el('small', { text: `vence ${relativo(t.due_at)}` }),
      ].filter(Boolean)),
    ]));
  });
}

const PROP_TOM = {
  Rascunho: 'neutra', Enviada: 'info', Visualizada: 'atencao',
  Aceita: 'ok', Recusada: 'ruim', Expirada: 'neutra',
};

export function cartaoProposta(p, { compacto = false } = {}) {
  const linhas = [
    el('div', { class: 'prop__top' }, [
      el('strong', { class: 'prop__title', text: p.title }),
      el('span', { class: `pill pill--${PROP_TOM[p.status] || 'neutra'}`, text: p.status }),
    ]),
    el('div', { class: 'prop__meta' }, [
      el('span', { text: p.number }),
      compacto ? null : el('span', { text: p.lead_name }),
      el('strong', { text: brl(p.total) }),
    ].filter(Boolean)),
  ];

  const marcos = [];
  if (p.sent_at) marcos.push(`enviada ${relativo(p.sent_at)}`);
  if (p.viewed_at) marcos.push(`aberta pelo cliente ${relativo(p.viewed_at)}`);
  if (p.decided_at) marcos.push(`${p.status.toLowerCase()} ${relativo(p.decided_at)} por ${p.decided_by}`);
  if (p.valid_until && !p.decided_at) marcos.push(`vence ${relativo(p.valid_until)}`);
  if (marcos.length) linhas.push(el('p', { class: 'prop__track', text: marcos.join(' · ') }));

  const acoes = [];
  const decidida = p.status === 'Aceita' || p.status === 'Recusada';
  if (!decidida) {
    acoes.push(el('button', { class: 'btn btn--quiet btn--xs', attrs: { type: 'button', 'data-prop-edit': p.id } }, [icon('pencil'), el('span', { text: 'Editar' })]));
    acoes.push(el('button', { class: 'btn btn--ghost btn--xs', attrs: { type: 'button', 'data-prop-send': p.id } }, [icon('send'), el('span', { text: p.sent_at ? 'Reenviar' : 'Enviar' })]));
  }
  acoes.push(el('a', {
    class: 'btn btn--quiet btn--xs',
    attrs: { href: p.public_url, target: '_blank', rel: 'noopener' },
  }, [icon('eye'), el('span', { text: 'Ver como cliente' })]));
  if (p.status !== 'Aceita') {
    acoes.push(el('button', {
      class: 'iconbtn iconbtn--xs',
      attrs: { type: 'button', 'data-prop-del': p.id, 'aria-label': `Excluir a proposta ${p.number}` },
    }, [icon('trash')]));
  }
  linhas.push(el('div', { class: 'prop__acts' }, acoes));

  return el('article', { class: 'bezel prop tilt3d' }, [el('div', { class: 'bezel__in prop__in' }, linhas)]);
}

function renderProposals() {
  const alvo = document.getElementById('lead-proposals');
  clear(alvo);

  if (!planoInclui('propostas')) {
    alvo.append(emptyState({
      title: 'Propostas fazem parte do Pro',
      text: 'Monte a proposta aqui e mande um link para o cliente abrir, ver e aceitar.',
      iconName: 'doc',
      action: { label: 'Ver os planos', iconName: null, onClick: () => { location.hash = '#/cobranca'; } },
    }));
    return;
  }
  if (!store.leadProposals.length) {
    alvo.append(emptyState({
      title: 'Nenhuma proposta',
      text: 'Monte uma proposta com itens e valores e envie por link ao cliente.',
      iconName: 'doc',
    }));
    return;
  }
  for (const p of store.leadProposals) alvo.append(cartaoProposta(p, { compacto: true }));
}

function renderChat(lead) {
  const chip = document.getElementById('lead-wa-status');
  const caixa = document.getElementById('lead-wa-chat');
  const form = document.getElementById('wa-form');
  const chat = store.leadChat;
  clear(caixa);

  const numero = lead.whatsapp || lead.phone;
  if (!numero) {
    chip.textContent = 'sem número';
    caixa.append(el('p', { class: 'panel__body', text: 'Este lead não tem número. Use "Editar" para preencher o WhatsApp.' }));
    form.hidden = true;
    return;
  }

  const pronto = Boolean(chat?.ready);
  chip.textContent = pronto ? 'conectado' : 'não conectado';
  chip.className = `chip${pronto ? ' chip--ok' : ' chip--warn'}`;
  form.hidden = false;
  document.getElementById('wa-send').disabled = !pronto;

  if (!pronto) {
    caixa.append(el('p', { class: 'panel__body' }, [
      document.createTextNode('A integração ainda não está ligada. '),
      el('a', { attrs: { href: '#/config' }, text: 'Configurar o WhatsApp' }),
      document.createTextNode('.'),
    ]));
  }

  const mensagens = chat?.messages || [];
  if (!mensagens.length) {
    caixa.append(el('p', { class: 'panel__body', text: 'Nenhuma mensagem trocada ainda.' }));
    return;
  }
  for (const m of mensagens) {
    caixa.append(el('div', { class: `bubble bubble--${m.direction}` }, [
      el('p', { class: 'bubble__txt', text: m.body || '—' }),
      el('span', { class: 'bubble__meta', text: `${quando(m.created_at)} · ${m.status}${m.error ? ` — ${m.error}` : ''}` }),
    ]));
  }
  caixa.scrollTop = caixa.scrollHeight;
}

export function renderLead() {
  const lead = currentLead();
  if (!lead) return;
  renderHead(lead);
  renderStageBar(lead);
  renderTimeline();
  renderContato(lead);
  renderCustom(lead);
  renderTasks();
  renderProposals();
  renderChat(lead);
}

export function pedirMotivo(lead, { motivoAtual = '' } = {}) {
  const modal = document.getElementById('loss-modal');
  const opcoes = document.getElementById('loss-options');
  const erro = document.getElementById('loss-pick-error');
  const nota = document.getElementById('loss-note');

  document.getElementById('loss-lead-name').textContent = lead.name;
  document.getElementById('loss-lead-meta').textContent = `${lead.company} · ${brl(lead.value)}`;
  erro.hidden = true;
  nota.value = lead.lost_note || '';

  clear(opcoes);
  const lista = (store.lossReasons || []).filter((r) => r.active);
  for (const motivo of lista) {
    const id = `motivo-${motivo.id}`;
    opcoes.append(el('label', { class: 'reason', attrs: { for: id } }, [
      el('input', { attrs: { type: 'radio', name: 'loss-reason', id, value: motivo.label, checked: motivo.label === motivoAtual } }),
      el('span', { class: 'reason__txt', text: motivo.label }),
    ]));
  }
  if (!lista.length) {
    opcoes.append(el('p', { class: 'panel__body' }, [
      document.createTextNode('Nenhum motivo ativo. '),
      el('a', { attrs: { href: '#/config' }, text: 'Cadastre os motivos em Configurações' }),
      document.createTextNode('.'),
    ]));
  }

  return new Promise((resolve) => {
    let decidido = false;
    const form = document.getElementById('loss-pick-form');

    const responder = (valor) => {
      if (decidido) return;
      decidido = true;
      form.removeEventListener('submit', aoEnviar);
      resolve(valor);
      if (valor) closeModal();
    };

    const aoEnviar = (evento) => {
      evento.preventDefault();
      const marcado = opcoes.querySelector('input[name="loss-reason"]:checked');
      if (!marcado) {
        erro.textContent = 'Escolha um motivo para registrar a perda.';
        erro.hidden = false;
        return;
      }
      responder({ reason: marcado.value, note: nota.value.trim() });
    };

    form.addEventListener('submit', aoEnviar);
    openModal(modal, { focus: 'input[name="loss-reason"]', onClose: () => responder(null) });
  });
}

async function mudarEtapa(lead, novo) {
  if (lead.status === novo) return;

  let extra = {};
  if (novo === 'Perdido') {
    const escolha = await pedirMotivo(lead);
    if (!escolha) return;
    extra = { lost_reason: escolha.reason, lost_note: escolha.note };
  }

  try {
    await api.updateLead(lead.id, { status: novo, ...extra });
    toast(novo === 'Ganho' ? 'Negócio marcado como ganho.' : `Negócio movido para ${novo}.`,
      novo === 'Perdido' ? 'info' : 'success');
    await hooks.refreshData();
    await loadLead(lead.id);
    renderLead();
  } catch (err) {
    if (err?.status !== 401) toast(err?.message || 'Não foi possível mudar a etapa.', 'error');
  }
}

async function registrarAtividade(evento) {
  evento.preventDefault();
  const lead = currentLead();
  if (!lead) return;

  const erro = document.getElementById('activity-error');
  const titulo = document.getElementById('activity-title').value.trim();
  const kind = document.getElementById('activity-kind').value;
  const due = document.getElementById('activity-due').value;
  erro.hidden = true;

  if (!titulo) {
    erro.textContent = 'Escreva o que aconteceu.';
    erro.hidden = false;
    return;
  }

  const botao = document.getElementById('activity-save');
  botao.disabled = true;
  try {
    await api.createActivity(lead.id, { kind, title: titulo, due_date: kind === 'tarefa' ? due : '' });
    document.getElementById('activity-title').value = '';
    document.getElementById('activity-due').value = '';
    toast(CONTACT_KINDS.has(kind) ? 'Contato registrado.' : 'Registro salvo.', 'success');
    await loadLead(lead.id);

    await hooks.refreshData({ quiet: true });
    renderLead();
  } catch (err) {
    if (err?.status !== 401) {
      erro.textContent = err?.message || 'Não foi possível registrar.';
      erro.hidden = false;
    }
  } finally {
    botao.disabled = false;
  }
}

async function concluirTarefa(id) {
  try {
    await api.finishActivity(id);
    toast('Tarefa concluída.', 'success');
    await loadLead(store.leadId);
    await hooks.refreshData({ quiet: true });
    renderLead();
  } catch (err) {
    if (err?.status !== 401) toast(err?.message || 'Não foi possível concluir.', 'error');
  }
}

async function apagarAtividade(id) {
  const item = store.leadActivities.find((a) => String(a.id) === String(id));
  const ok = await confirmar({
    titulo: 'Apagar este registro?',
    texto: 'Ele sai da linha do tempo deste negócio.',
    alvo: item ? { nome: item.title, meta: quando(item.created_at) } : null,
    confirmar: 'Apagar',
  });
  if (!ok) return;
  try {
    await api.deleteActivity(id);
    await loadLead(store.leadId);
    renderLead();
  } catch (err) {
    if (err?.status !== 401) toast(err?.message || 'Não foi possível apagar.', 'error');
  }
}

async function enviarWhats(evento) {
  evento.preventDefault();
  const lead = currentLead();
  if (!lead) return;
  const campo = document.getElementById('wa-body');
  const erro = document.getElementById('wa-error');
  const texto = campo.value.trim();
  erro.hidden = true;
  if (!texto) return;

  const botao = document.getElementById('wa-send');
  botao.disabled = true;
  try {
    const resposta = await api.sendWhatsapp(lead.id, { body: texto });
    campo.value = '';
    store.leadChat = { ...(store.leadChat || {}), messages: resposta.messages };
    await loadLead(lead.id);
    await hooks.refreshData({ quiet: true });
    renderLead();
  } catch (err) {
    if (err?.status !== 401) {
      erro.textContent = err?.message || 'Não foi possível enviar.';
      erro.hidden = false;
    }
  } finally {
    botao.disabled = false;
  }
}

export function wireLead({ abrirLead, abrirProposta, enviarProposta, apagarProposta }) {
  const pagina = document.getElementById('page-lead');
  if (!pagina) return;

  document.getElementById('activity-form')?.addEventListener('submit', registrarAtividade);
  document.getElementById('wa-form')?.addEventListener('submit', enviarWhats);

  document.getElementById('activity-kind')?.addEventListener('change', (e) => {
    document.getElementById('activity-due-row').hidden = e.target.value !== 'tarefa';
  });

  pagina.addEventListener('click', async (evento) => {
    const lead = currentLead();
    if (!lead) return;

    const etapa = evento.target.closest('[data-stage]');
    if (etapa) { await mudarEtapa(lead, etapa.dataset.stage); return; }

    const concluir = evento.target.closest('[data-done]');
    if (concluir) { await concluirTarefa(concluir.dataset.done); return; }

    const apagar = evento.target.closest('[data-del-act]');
    if (apagar) { await apagarAtividade(apagar.dataset.delAct); return; }

    const editarProp = evento.target.closest('[data-prop-edit]');
    if (editarProp) { abrirProposta(editarProp.dataset.propEdit); return; }

    const enviarProp = evento.target.closest('[data-prop-send]');
    if (enviarProp) { enviarProposta(enviarProp.dataset.propSend); return; }

    const apagarProp = evento.target.closest('[data-prop-del]');
    if (apagarProp) { await apagarProposta(apagarProp.dataset.propDel); return; }

    const filtro = evento.target.closest('[data-tl]');
    if (filtro) {
      store.tlFilter = filtro.dataset.tl;
      pagina.querySelectorAll('[data-tl]').forEach((b) => b.classList.toggle('is-active', b === filtro));
      renderTimeline();
      return;
    }

    if (evento.target.closest('#lead-edit')) { abrirLead(lead.id); return; }
    if (evento.target.closest('#lead-win')) { await mudarEtapa(lead, 'Ganho'); return; }
    if (evento.target.closest('#lead-lose')) { await mudarEtapa(lead, 'Perdido'); return; }
    if (evento.target.closest('#lead-fix-reason')) {
      const escolha = await pedirMotivo(lead, { motivoAtual: lead.lost_reason });
      if (!escolha) return;
      try {
        await api.updateLead(lead.id, { status: 'Perdido', lost_reason: escolha.reason, lost_note: escolha.note });
        toast('Motivo corrigido.', 'success');
        await hooks.refreshData();
        await loadLead(lead.id);
        renderLead();
      } catch (err) {
        if (err?.status !== 401) toast(err?.message || 'Não foi possível corrigir.', 'error');
      }
      return;
    }
    if (evento.target.closest('#lead-new-proposal') || evento.target.closest('#lead-new-proposal-2')) {
      abrirProposta(null, lead.id);
      return;
    }
    if (evento.target.closest('#lead-whats')) {
      document.getElementById('wa-body')?.focus();
      document.getElementById('lead-wa-card')?.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }
  });
}
