import * as api from './api.js';
import { initAuth, showAuth, hideAuth } from './auth.js';
import {
  el, icon, clear, brl, num, pct, initials, parseAmount,
  readTheme, applyTheme, saveTheme,
  toast, openModal, closeModal, confirmar,
  emptyState, emptyRow, seriesColors,
  chartsFallback, sceneFallback,
} from './ui.js';
import {
  store, hooks, OPEN_STATUSES, CLOSED_STATUSES, STATUSES as FUNIL,
} from './store.js';
import { loadLead, renderLead, wireLead, pedirMotivo } from './lead.js';
import { openImport, wireImport } from './importar.js';
import { loadAdmin, renderAdmin, wireAdmin } from './admin.js';
import { loadMarketing, renderMarketing, wireMarketing } from './marketing.js';
import { mostrarPaywall, wirePaywall } from './paywall.js';
import { loadOnboarding, renderOnboarding, reexibirOnboarding, estadoOnboarding } from './onboarding.js';
import {
  loadEquipe, renderEquipe, wireEquipe, setPendingInvite, abrirConviteSePendente,
} from './equipe.js';
import { loadProposals, renderProposals, wireProposals, abrirProposta, enviarProposta, apagarProposta } from './proposals.js';
import { loadAutomations, renderAutomations, wireAutomations } from './automations.js';
import { loadSettings, renderSettings, wireSettings } from './settings.js';
import { loadNotifications, renderNotifications, wireNotify } from './notify.js';
import {
  loadIntel, loadAvancado, renderIntel, renderAvancado, ligarIntel, seloScore,
} from './inteligencia.js';
import {
  cobranca, loadCobranca, loadFaturas, renderCobranca, planoInclui, bloqueio,
} from './cobranca.js';

const STATUSES = FUNIL;
const ROUTES = [
  'dashboard', 'leads', 'negocios', 'acompanhamento',
  'propostas', 'automacoes', 'inteligencia', 'relatorios', 'cobranca', 'config',
  'marketing', 'equipe', 'admin', 'lead',
];

const state = {
  user: null,
  leads: [],

  leadsTotal: 0,
  leadsTruncado: false,
  leadsTeto: 0,
  stats: null,
  followups: null,
  semAcao: null,
  losses: null,
  fupFilter: 'todas',
  search: '',
  route: 'dashboard',
  loading: false,
};

const Charts3D = { ...chartsFallback };
const Scene = { ...sceneFallback };

async function loadViz() {
  try {
    const mod = await import('./charts3d.js');
    for (const fn of ['donut', 'bars', 'line', 'empty', 'destroy']) {
      if (typeof mod[fn] === 'function') Charts3D[fn] = mod[fn];
    }
  } catch (err) {
    console.info('[vertex] charts3d.js indisponível; usando o fallback SVG.', err?.message || err);
  }
  try {
    const mod = await import('./scene.js');
    if (typeof mod.mount === 'function') Scene.mount = mod.mount;
    if (typeof mod.unmount === 'function') Scene.unmount = mod.unmount;
  } catch (err) {
    console.info('[vertex] scene.js indisponível; os halos em CSS cobrem o fundo.', err?.message || err);
  }
}

function draw(kind, target, options) {
  if (!target) return;
  try { Charts3D.destroy(target); } catch {  }

  clear(target);
  try {
    Charts3D[kind](target, options);
  } catch (err) {
    console.warn(`[vertex] Charts3D.${kind} falhou; caindo para o fallback.`, err);
    try { chartsFallback[kind](target, options); } catch {  }
  }
}

function destroyCharts(scope) {
  (scope || document).querySelectorAll('[data-chart]').forEach((node) => {
    try { Charts3D.destroy(node); } catch { clear(node); }
  });
}

const COMBINING = /[\u0300-\u036f]/g;

const deburr = (s) => String(s ?? '').normalize('NFD').replace(COMBINING, '').toLowerCase();

function filteredLeads() {
  const q = deburr(state.search).trim();
  if (!q) return state.leads;
  return state.leads.filter((l) => (
    deburr(l.name).includes(q)
    || deburr(l.company).includes(q)
    || deburr(l.segment).includes(q)
    || deburr(l.status).includes(q)
  ));
}

function leadValue(lead) {
  const n = Number(lead?.value);
  return Number.isFinite(n) ? n : 0;
}

function hasStatsData() {
  return Boolean(state.stats && state.stats.has_data);
}

function syncTruncadoAviso(pageEl) {
  if (!pageEl) return;
  pageEl.querySelector('.trunc')?.remove();
  if (!state.leadsTruncado) return;
  const aviso = el('p', { class: 'trunc' }, [
    icon('alert'),
    el('span', {

      text: `Mostrando os ${num(state.leads.length)} negócios mais recentes de ${num(state.leadsTotal)}. `
        + 'A exportação em CSV cobre só estes — fale com a gente para levar a base inteira.',
    }),
  ]);
  pageEl.querySelector('.page__head')?.after(aviso);
}

function syncFilterChip(pageEl, shownCount) {
  if (!pageEl) return;
  pageEl.querySelector('.filterchip')?.remove();
  syncTruncadoAviso(pageEl);
  if (!state.search.trim()) return;
  const chip = el('p', { class: 'filterchip' }, [
    icon('search'),
    el('span', { text: `${num(shownCount)} resultado(s) para "${state.search.trim()}"` }),
  ]);
  pageEl.querySelector('.page__head')?.after(chip);
}

function badge(status) {
  return el('span', { class: 'badge', text: status, attrs: { 'data-status': status } });
}

function kpiTile({ cls, label, value, iconName, foot, meter }) {
  const inner = [
    el('div', { class: 'kpi__top' }, [
      el('span', { class: 'kpi__label', text: label }),
      el('span', { class: 'kpi__badge' }, [icon(iconName)]),
    ]),
    el('p', { class: `kpi__value${cls === 'kpi-a' ? '' : ''}`, text: value }),
  ];
  if (meter != null) {
    const width = Math.max(0, Math.min(100, Number(meter) || 0));
    inner.push(el('div', { class: 'meter' }, [
      el('div', { class: 'meter__fill', attrs: { style: `width:${width}%` } }),
    ]));
  }
  if (foot) inner.push(el('p', { class: 'kpi__foot', text: foot }));

  return el('div', { class: `bezel ${cls}` }, [
    el('div', { class: `bezel__in kpi${cls === 'kpi-a' ? ' kpi--hero' : ''}` }, inner),
  ]);
}

function renderKpis() {
  const host = document.getElementById('kpi-grid');
  if (!host) return;
  const k = state.stats?.kpis || {};
  const receita = Number(k.receita_total) || 0;
  const ativos = Number(k.leads_ativos) || 0;
  const fechados = Number(k.fechados) || 0;
  const propostas = Number(k.propostas) || 0;
  const ticket = Number(k.ticket_medio) || 0;
  const conv = Number(k.taxa_conversao) || 0;

  clear(host);
  host.append(
    kpiTile({
      cls: 'kpi-a', label: 'Receita total', value: brl(receita), iconName: 'wallet',
      foot: ativos > 0 ? `${num(ativos)} leads no pipeline` : 'Nenhum lead cadastrado ainda',
    }),
    kpiTile({
      cls: 'kpi-b', label: 'Ticket médio', value: brl(ticket), iconName: 'spark',
      foot: ativos > 0 ? 'Média por lead cadastrado' : 'Sem base para calcular',
    }),
    kpiTile({
      cls: 'kpi-c', label: 'Conversão', value: pct(conv), iconName: 'target', meter: conv,
      foot: ativos > 0 ? `${num(fechados)} de ${num(ativos)} fechados` : 'Sem base para calcular',
    }),
    kpiTile({ cls: 'kpi-d', label: 'Leads ativos', value: num(ativos), iconName: 'users' }),
    kpiTile({ cls: 'kpi-e', label: 'Propostas', value: num(propostas), iconName: 'columns' }),
    kpiTile({ cls: 'kpi-f', label: 'Fechados', value: num(fechados), iconName: 'check-circle' }),
  );
}

function renderRevenueChart() {
  const target = document.getElementById('chart-revenue');
  const chip = document.getElementById('revenue-chip');
  if (!target) return;
  const monthly = Array.isArray(state.stats?.monthly) ? state.stats.monthly : [];

  if (!hasStatsData() || monthly.length === 0) {
    if (chip) chip.textContent = 'Sem período';
    draw('empty', target, { message: 'Sem receita registrada', icon: 'chart' });
    return;
  }
  if (chip) {
    chip.textContent = monthly.length === 1
      ? monthly[0].label
      : `${monthly[0].label} a ${monthly[monthly.length - 1].label}`;
  }
  draw('line', target, {
    points: monthly.map((m) => ({ label: m.label, value: Number(m.value) || 0 })),
    currency: true,
  });
}

function renderSegments() {
  const target = document.getElementById('chart-segments');
  const legend = document.getElementById('segments-legend');
  if (!target || !legend) return;

  const segments = Array.isArray(state.stats?.segments) ? state.stats.segments : [];
  clear(legend);

  if (!hasStatsData() || segments.length === 0) {
    legend.hidden = true;

    try { Charts3D.destroy(target); } catch {  }
    clear(target);
    target.append(emptyState({
      iconName: 'pie',
      title: 'Sem dados de segmento',
      text: 'Este gráfico divide o dinheiro do funil por tipo de cliente — é como se descobre de onde vem a maior parte da receita. Ele aparece assim que houver leads com valor.',
      action: { label: 'Novo lead', onClick: () => openLeadModal(null) },
    }));
    return;
  }

  legend.hidden = false;
  const palette = seriesColors();

  const data = segments.map((s, i) => ({
    label: s.label,
    value: Number(s.value) || 0,
    count: Number(s.count) || 0,
    percent: Number(s.percent) || 0,
    color: palette[i % palette.length],
  }));
  const total = data.reduce((a, s) => a + s.value, 0);

  draw('donut', target, {
    segments: data.map(({ label, value, color }) => ({ label, value, color })),
    total,
    currency: true,
    legend: false,
  });

  for (const s of data) {
    legend.append(el('li', { class: 'legend__item' }, [
      el('span', { class: 'legend__dot', attrs: { style: `background:${s.color}` } }),
      el('span', { class: 'legend__name', text: s.label }),
      el('span', { class: 'legend__val', text: pct(s.percent) }),
    ]));
  }
}

function renderDashTable() {
  const tbody = document.getElementById('dash-tbody');
  if (!tbody) return;
  const rows = filteredLeads().slice(0, 5);
  clear(tbody);

  if (rows.length === 0) {
    tbody.append(state.search.trim()
      ? emptyRow(5, 'Nenhum lead corresponde à busca.',
                 'Limpe a busca para ver os cinco negócios mais recentes.')
      : emptyRow(5, 'Nenhum lead cadastrado ainda.',
                 'Esta lista mostra os cinco negócios mais recentes — é por onde se confere, num relance, o que entrou no funil hoje.'));
    return;
  }

  for (const lead of rows) {
    tbody.append(el('tr', {}, [
      el('td', { class: 'cell-strong', text: lead.name }),
      el('td', { class: 'cell-muted', text: lead.company }),
      el('td', { class: 'cell-muted', text: lead.segment || 'Outros' }),
      el('td', { class: 'num', text: brl(leadValue(lead)) }),
      el('td', {}, [badge(lead.status)]),
    ]));
  }
}

function renderDashboard() {
  const page = document.getElementById('page-dashboard');
  renderOnboarding((rota) => { location.hash = `#/${rota}`; }, () => openLeadModal());
  renderKpis();
  renderAttentionCard();
  renderSemAcaoCard();
  renderRevenueChart();
  renderSegments();
  renderDashTable();
  syncFilterChip(page, filteredLeads().length);

  const sub = document.getElementById('dash-sub');
  if (sub) {
    sub.textContent = hasStatsData()
      ? 'Seus clientes, seus negócios e o que precisa de atenção hoje.'
      : 'Cadastre o primeiro cliente para ver os números aparecerem aqui.';
  }
}

const SEVERIDADE = {
  alta: { rotulo: 'Urgente', icone: 'fire' },
  media: { rotulo: 'Atenção', icone: 'clock' },
  baixa: { rotulo: 'De olho', icone: 'clock' },
};

function followupRow(item, { compacto = false } = {}) {
  const sev = SEVERIDADE[item.severity] || SEVERIDADE.baixa;

  const cabeca = el('div', { class: 'fup__top' }, [
    el('div', { class: 'fup__who' }, [
      el('strong', { class: 'fup__name', text: item.name }),
      el('span', { class: 'fup__co', text: item.company }),
    ]),
    el('span', { class: 'fup__val', text: brl(item.value) }),
  ]);

  const meta = el('div', { class: 'fup__meta' }, [
    el('span', { class: `sev sev--${item.severity}` }, [
      icon(sev.icone),
      el('span', { text: sev.rotulo }),
    ]),
    el('span', { class: 'fup__stage', text: item.status }),
    el('span', { class: 'fup__reason', text: item.reason }),
  ]);

  const filhos = [cabeca, meta];

  if (!compacto) {
    filhos.push(el('div', { class: 'fup__acts' }, [
      el('button', {
        class: 'btn btn--quiet btn--sm',
        attrs: { type: 'button', 'data-followup-open': item.lead_id },
      }, [icon('pencil'), el('span', { text: 'Abrir negócio' })]),
      el('button', {
        class: 'btn btn--quiet btn--sm',
        attrs: {
          type: 'button', 'data-followup-touch': item.lead_id,
          title: 'Marca que você falou com o cliente hoje e tira o alerta da lista',
        },
      }, [icon('check'), el('span', { text: 'Registrar contato' })]),
    ]));
  }

  return el('li', { class: `fup fup--${item.severity}` }, filhos);
}

function renderAttentionCard() {
  const card = document.getElementById('dash-attn');
  const lista = document.getElementById('attn-list');
  const sub = document.getElementById('attn-sub');
  if (!card || !lista) return;

  const dados = state.followups;

  if (!dados || !dados.total) {
    card.hidden = true;
    return;
  }

  card.hidden = false;
  const n = dados.total;
  if (sub) {
    sub.textContent = `${n} ${n === 1 ? 'oportunidade parada' : 'oportunidades paradas'}`
      + `, somando ${brl(dados.value_at_risk)} sem movimento.`;
  }

  clear(lista);
  dados.items.slice(0, 3).forEach((item) => lista.appendChild(followupRow(item, { compacto: true })));
}

function renderSemAcaoCard() {
  const card = document.getElementById('dash-semacao');
  const lista = document.getElementById('semacao-list');
  const sub = document.getElementById('semacao-sub');
  if (!card || !lista) return;

  const dados = state.semAcao;
  if (!dados || !dados.total) {
    card.hidden = true;
    return;
  }

  card.hidden = false;
  const n = dados.total;
  if (sub) {
    sub.textContent =
      `${n} ${n === 1 ? 'negócio aberto' : 'negócios abertos'} sem próximo passo`
      + `, somando ${brl(dados.valor_parado)} sem dono da vez.`;
  }

  clear(lista);
  dados.items.slice(0, 3).forEach((item) => lista.appendChild(semAcaoRow(item)));
}

function semAcaoRow(item) {
  const dias = item.dias_parado || 0;
  const motivo = dias > 0
    ? `Parado há ${dias} ${dias === 1 ? 'dia' : 'dias'}, sem próxima ação.`
    : 'Sem próxima ação definida.';
  return el('li', { class: 'fup fup--media' }, [
    el('div', { class: 'fup__top' }, [
      el('div', { class: 'fup__who' }, [
        el('strong', { class: 'fup__name', text: item.name }),
        el('span', { class: 'fup__co', text: item.company }),
      ]),
      el('span', { class: 'fup__val', text: brl(item.value) }),
    ]),
    el('div', { class: 'fup__meta' }, [
      el('span', { class: 'fup__stage', text: item.status }),
      el('span', { class: 'fup__reason', text: motivo }),
    ]),
    el('div', { class: 'fup__acts' }, [
      el('button', {
        class: 'btn btn--quiet btn--sm',
        attrs: { type: 'button', 'data-followup-open': item.lead_id },
      }, [icon('pencil'), el('span', { text: 'Abrir e agendar' })]),
    ]),
  ]);
}

function renderFollowups() {
  const resumo = document.getElementById('fup-summary');
  const caixa = document.getElementById('fup-list');
  const filtros = document.getElementById('fup-filters');
  if (!caixa || !resumo) return;

  const dados = state.followups;
  clear(resumo);
  clear(caixa);

  if (!dados) {
    caixa.appendChild(emptyState({
      iconName: 'clock', title: 'Carregando…',
      text: 'Buscando o que precisa de atenção no seu funil.',
    }));
    if (filtros) filtros.hidden = true;
    return;
  }

  if (!dados.total) {
    if (filtros) filtros.hidden = true;
    caixa.appendChild(emptyState({
      iconName: 'check-circle',
      title: state.leads.length ? 'Está tudo em dia' : 'Nada para acompanhar ainda',
      text: state.leads.length
        ? 'Esta tela lista os negócios abertos que passaram do prazo sem contato. Nenhum passou — quando algum passar, ele aparece aqui sozinho, com há quantos dias está parado.'
        : 'É aqui que o Vertex avisa quais negócios estão esfriando sem que ninguém tenha percebido. Ele passa a apontar sozinho assim que houver leads cadastrados e contatos registrados.',
    }));
    return;
  }

  if (filtros) filtros.hidden = false;

  const urgentes = dados.items.filter((i) => i.severity === 'alta');
  const emRisco = dados.items.reduce((soma, i) => soma + i.value, 0);

  const wrap = document.getElementById('fup-chart-wrap');
  const alvo = document.getElementById('chart-followups');
  if (wrap && alvo) {
    const porEtapa = new Map();
    dados.items.forEach((i) => porEtapa.set(i.status, (porEtapa.get(i.status) || 0) + i.value));
    const serie = STATUSES
      .filter((s) => porEtapa.has(s))
      .map((s) => ({ label: s, value: porEtapa.get(s) }));

    wrap.hidden = serie.length < 2;
    if (wrap.hidden) destroyCharts(wrap);
    else draw('bars', alvo, { series: serie, currency: 'BRL' });
  }

  [
    { rotulo: 'Precisam de atenção', valor: String(dados.total), tom: 'neutro' },
    { rotulo: 'Urgentes', valor: String(urgentes.length), tom: 'alta' },
    { rotulo: 'Valor sem movimento', valor: brl(emRisco), tom: 'valor' },
  ].forEach((bloco) => {
    resumo.appendChild(el('div', { class: `fupsum__card fupsum__card--${bloco.tom}` }, [
      el('span', { class: 'fupsum__num', text: bloco.valor }),
      el('span', { class: 'fupsum__lbl', text: bloco.rotulo }),
    ]));
  });

  const visiveis = state.fupFilter === 'todas'
    ? dados.items
    : dados.items.filter((i) => i.severity === state.fupFilter);

  if (!visiveis.length) {
    caixa.appendChild(emptyState({
      iconName: 'check-circle', title: 'Nada nesta urgência',
      text: 'Troque o filtro para ver os demais acompanhamentos.',
    }));
    return;
  }

  const lista = el('ul', { class: 'fuplist' });
  visiveis.forEach((item) => lista.appendChild(followupRow(item)));
  caixa.appendChild(lista);
}

function initTilt() {  }

function renderFollowupBadge() {
  const badge = document.getElementById('nav-followup-badge');
  if (!badge) return;
  const total = state.followups?.total || 0;
  badge.textContent = total > 99 ? '99+' : String(total);
  badge.hidden = total === 0;
  badge.classList.toggle(
    'nav__badge--hot',
    Boolean(state.followups?.items?.some((i) => i.severity === 'alta')),
  );
}

async function touchLead(id) {
  const lead = state.leads.find((l) => String(l.id) === String(id));
  if (!lead) return;
  try {
    await api.createActivity(id, { kind: 'ligacao', title: 'Contato registrado' });
    toast(`Contato com ${lead.name} registrado no histórico.`, 'success');
    await refreshData({ quiet: true });
  } catch (err) {
    if (err?.status !== 401) toast(err?.message || 'Não foi possível registrar o contato.', 'error');
  }
}

function renderLeads() {
  const page = document.getElementById('page-leads');
  const tbody = document.getElementById('leads-tbody');
  if (!tbody) return;
  const rows = filteredLeads();
  clear(tbody);
  syncFilterChip(page, rows.length);

  if (rows.length === 0) {
    tbody.append(state.search.trim()
      ? emptyRow(6, 'Nenhum lead corresponde à busca.',
                 'A busca olha nome, empresa e e-mail. Limpe o campo para ver a lista inteira.')
      : emptyRow(6, 'Nenhum lead cadastrado ainda.',
                 'Cada linha aqui é uma oportunidade de venda com histórico próprio. É deste cadastro que saem o funil, os avisos do que parou e os relatórios — use "Novo lead", ou "Importar" se os seus contatos já estão numa planilha.'));
    return;
  }

  for (const lead of rows) {
    const edit = el('button', {
      class: 'iconbtn',
      attrs: { type: 'button', 'aria-label': `Editar lead ${lead.name}`, 'data-edit': lead.id },
    }, [icon('pencil')]);
    const del = el('button', {
      class: 'iconbtn is-danger',
      attrs: { type: 'button', 'aria-label': `Excluir lead ${lead.name}`, 'data-delete': lead.id },
    }, [icon('trash')]);

    const ver = el('a', {
      class: 'iconbtn',
      attrs: { href: `#/lead/${lead.id}`, 'aria-label': `Abrir o negócio de ${lead.name}` },
    }, [icon('eye')]);

    tbody.append(el('tr', {}, [

      el('td', { class: 'cell-strong' }, [
        el('a', { class: 'cell-link', attrs: { href: `#/lead/${lead.id}` }, text: lead.name }),
      ]),
      el('td', { class: 'cell-muted', text: lead.company }),
      el('td', { class: 'cell-muted', text: lead.segment || 'Outros' }),
      el('td', { class: 'num', text: brl(leadValue(lead)) }),
      el('td', {}, [badge(lead.status)]),
      el('td', { class: 'acts' }, [el('div', { class: 'rowacts' }, [ver, edit, del])]),
    ]));
  }
}

function kanbanCard(lead) {
  const select = el('select', {
    attrs: { 'aria-label': `Mover ${lead.name} para outra etapa`, 'data-move': lead.id },
  }, STATUSES.map((s) => el('option', {
    text: s, attrs: { value: s, selected: s === lead.status },
  })));

  const rodape = [
    el('span', { class: 'kcard__val', text: brl(leadValue(lead)) }),
    el('span', { class: 'kcard__seg', text: lead.segment || 'Outros' }),
  ];

  if (lead.status === 'Perdido' && lead.lost_reason) {
    rodape.push(el('span', {
      class: 'kcard__lost',
      text: lead.lost_reason,

      attrs: { title: lead.lost_reason },
    }));
  }

  const topo = [el('a', {
    class: 'kcard__name', attrs: { href: `#/lead/${lead.id}` }, text: lead.name,
  })];
  if (!CLOSED_STATUSES.includes(lead.status) && lead.score !== null && lead.score !== undefined) {
    topo.unshift(seloScore(lead.score, lead.score_band));
  }

  return el('article', {
    class: 'kcard',
    attrs: { draggable: 'true', 'data-lead': lead.id },
  }, [
    el('div', { class: 'kcard__top' }, topo),
    el('p', { class: 'kcard__co', text: lead.company }),
    el('div', { class: 'kcard__foot' }, rodape),
    el('div', { class: 'kcard__move' }, [
      el('label', { text: 'Etapa', attrs: { for: `move-${lead.id}` } }),
      select,
    ]),
  ]);
}

function renderKanban() {
  const board = document.getElementById('kanban');
  const page = document.getElementById('page-negocios');
  if (!board) return;
  const rows = filteredLeads();
  clear(board);
  syncFilterChip(page, rows.length);

  if (rows.length === 0 && !state.search.trim()) {
    board.append(emptyState({
      iconName: 'columns',
      title: 'Seu funil está vazio',
      text: 'O funil mostra em que etapa cada negócio está — e arrastar um card entre colunas é o que registra que ele avançou. Cadastre o primeiro negócio para o funil ganhar conteúdo.',
      action: { label: 'Novo lead', onClick: () => openLeadModal(null) },
    }));
    return;
  }

  for (const status of STATUSES) {
    const items = rows.filter((l) => l.status === status);
    const body = el('div', { class: 'kcol__body' }, items.map(kanbanCard));
    if (items.length === 0) {
      clear(body);
      body.append(el('p', { class: 'empty__text', text: 'Nenhum lead aqui.' }));
    }

    board.append(el('section', {
      class: 'kcol', attrs: { 'data-status': status, 'aria-label': status },
    }, [
      el('div', { class: 'bezel', attrs: { style: 'height:100%' } }, [
        el('div', { class: 'bezel__in kcol__in' }, [
          el('div', { class: 'kcol__head' }, [
            el('h2', { class: 'kcol__title', text: status }),
            el('span', { class: 'kcol__count', text: String(items.length) }),
          ]),
          body,
        ]),
      ]),
    ]));
  }

  board.querySelectorAll('[data-move]').forEach((sel) => {
    sel.id = `move-${sel.dataset.move}`;
  });
}

function funnelFromLeads(leads) {
  const total = leads.reduce((a, l) => a + leadValue(l), 0);
  return STATUSES.map((status) => {
    const group = leads.filter((l) => l.status === status);
    const value = group.reduce((a, l) => a + leadValue(l), 0);
    return {
      status,
      count: group.length,
      value,
      percent: total > 0 ? (value / total) * 100 : 0,
    };
  });
}

function renderAvisoPlano() {
  const barra = document.getElementById('aviso-plano');
  if (!barra) return;
  const a = cobranca.assinatura;
  if (!a) { barra.hidden = true; return; }

  let texto = '';
  if (a.em_trial && (a.dias_de_trial ?? 99) <= 5) {
    const n = a.dias_de_trial;
    texto = n === 1
      ? 'Último dia de teste do Pro.'
      : `Faltam ${n} dias de teste do Pro.`;
  } else if (a.status === 'pendente') {
    texto = 'Pagamento em análise. O Pro é liberado assim que for confirmado.';
  } else if (a.status === 'vencida' || (a.status === 'cancelada' && !a.vigente)) {
    texto = 'A conta está no plano Inicial. Os seus dados continuam aqui.';
  }

  if (!texto) { barra.hidden = true; return; }
  clear(barra);
  barra.append(
    el('span', { class: 'avisop__txt', text: texto }),
    el('a', { class: 'avisop__link', attrs: { href: '#/cobranca' } }, ['Ver planos']),
  );
  barra.hidden = false;
}

function bloquearSePreciso(idPagina, recurso, descricao) {
  const page = document.getElementById(idPagina);
  if (!page) return false;
  const corpo = page.querySelector('[data-conteudo]') || page;
  const jaTem = corpo.querySelector('.bloq');

  if (planoInclui(recurso)) {
    if (jaTem) jaTem.remove();
    corpo.querySelectorAll('[data-pago]').forEach((n) => { n.hidden = false; });
    return false;
  }
  corpo.querySelectorAll('[data-pago]').forEach((n) => { n.hidden = true; });
  if (!jaTem) corpo.append(bloqueio(recurso, descricao));
  return true;
}

function renderReports() {
  renderAvancado();
  const page = document.getElementById('page-relatorios');
  const monthlyEl = document.getElementById('chart-monthly');
  const funnelEl = document.getElementById('chart-funnel');
  const tbody = document.getElementById('report-tbody');

  const monthly = Array.isArray(state.stats?.monthly) ? state.stats.monthly : [];
  const searching = Boolean(state.search.trim());
  const rows = filteredLeads();
  syncFilterChip(page, rows.length);

  if (!hasStatsData() || monthly.length === 0) {
    draw('empty', monthlyEl, { message: 'Sem vendas registradas', icon: 'chart' });
  } else {
    draw('bars', monthlyEl, {
      series: monthly.map((m) => ({ label: m.label, value: Number(m.value) || 0 })),
      currency: true,
    });
  }

  const funnel = searching
    ? funnelFromLeads(rows)
    : (Array.isArray(state.stats?.funnel) ? state.stats.funnel : []);
  const funnelHasData = funnel.some((f) => (Number(f.count) || 0) > 0);

  if (!funnelHasData) {
    draw('empty', funnelEl, { message: 'Sem leads no funil', icon: 'columns' });
  } else {
    draw('bars', funnelEl, {
      series: funnel.map((f) => ({ label: f.status, value: Number(f.value) || 0 })),
      currency: true,
    });
  }

  if (!tbody) return;
  clear(tbody);
  if (!funnelHasData) {
    tbody.append(emptyRow(4, searching
      ? 'Nenhum lead corresponde à busca.'
      : 'Sem dados para exibir. Cadastre um lead para começar.'));
    return;
  }
  for (const f of funnel) {
    tbody.append(el('tr', {}, [
      el('td', {}, [badge(f.status)]),
      el('td', { class: 'num', text: num(f.count) }),
      el('td', { class: 'num', text: brl(f.value) }),
      el('td', { class: 'num', text: pct(f.percent) }),
    ]));
  }

  renderLossReport();
}

function renderLossReport() {
  const alvo = document.getElementById('loss-report');
  if (!alvo) return;
  clear(alvo);

  const dados = state.losses;
  if (!dados || !dados.has_data) {
    alvo.append(emptyState({
      title: 'Nenhum negócio perdido ainda',
      text: 'Quando um negócio for marcado como perdido, o motivo entra aqui — em quantidade, em dinheiro e ao longo do tempo.',
      iconName: 'flag',
    }));
    return;
  }

  alvo.append(el('div', { class: 'lossnum' }, [
    el('div', { class: 'lossnum__box' }, [
      el('small', { text: 'Perdidos' }),
      el('strong', { text: num(dados.total_perdido) }),
      el('span', { text: `${pct(dados.taxa_perda)} dos negócios decididos` }),
    ]),
    el('div', { class: 'lossnum__box lossnum__box--money' }, [
      el('small', { text: 'Valor perdido' }),
      el('strong', { text: brl(dados.valor_perdido) }),
      el('span', { text: `contra ${brl(dados.valor_ganho)} ganhos` }),
    ]),
  ]));

  const grafico = el('div', { class: 'chart', attrs: { 'data-chart': 'losses', id: 'chart-losses' } });
  alvo.append(el('div', { class: 'lossgrid' }, [
    el('div', { class: 'lossgrid__chart' }, [grafico]),
    el('ul', { class: 'lossbars' }, dados.motivos.map((m) => el('li', { class: 'lossbar' }, [
      el('span', { class: 'lossbar__top' }, [
        el('strong', { text: m.label }),
        el('span', { text: `${num(m.count)} · ${brl(m.value)}` }),
      ]),
      el('span', { class: 'lossbar__track' }, [
        el('i', { class: 'lossbar__fill', attrs: { style: `width:${Math.max(2, m.percent)}%` } }),
      ]),
      el('span', { class: 'lossbar__pct', text: pct(m.percent) }),
    ]))),
  ]));

  draw('bars', grafico, {
    series: dados.motivos.map((m) => ({ label: m.label, value: m.value })),
    currency: true,
  });
}

function renderConfig() {
  const name = document.getElementById('profile-name');
  const email = document.getElementById('profile-email');
  const note = document.getElementById('session-note');
  if (name) name.value = state.user?.name || '';
  if (email) email.value = state.user?.email || '';
  if (note) {
    note.textContent = state.user
      ? `Conectado como ${state.user.email}.`
      : 'Você não está conectado.';
  }
  applyTheme(readTheme());

  const voltar = document.getElementById('pp-voltar');
  if (voltar) voltar.hidden = !estadoOnboarding()?.dispensado;
}

const RENDERERS = {
  dashboard: renderDashboard,
  leads: renderLeads,
  negocios: renderKanban,
  acompanhamento: renderFollowups,
  propostas: () => {
    if (bloquearSePreciso(
      'page-propostas', 'propostas',
      'Monte a proposta no Vertex e mande um link para o cliente abrir, ver e aceitar — com registro de quando ele abriu. As propostas fazem parte do plano Pro.',
    )) return;
    renderProposals();
  },

  automacoes: () => {
    if (bloquearSePreciso(
      'page-automacoes', 'automacoes',
      'As automações mandam o follow-up sozinhas quando um negócio para de andar — sem você precisar lembrar. Elas fazem parte do plano Pro.',
    )) return;
    renderAutomations();
  },
  inteligencia: renderIntel,
  relatorios: renderReports,
  cobranca: renderCobranca,
  config: renderConfig,
  marketing: renderMarketing,
  equipe: renderEquipe,
  admin: renderAdmin,
  lead: renderLead,
};

function routeFromHash() {
  const raw = (location.hash || '').replace(/^#\/?/, '').split('?')[0];
  const [nome, param] = raw.split('/');
  if (nome === 'lead' && param) return { route: 'lead', param };

  if (nome === 'admin' && !state.user?.is_owner) return { route: 'dashboard', param: null };

  if (nome === 'equipe' && !podeGerirEquipe()) return { route: 'dashboard', param: null };

  if (nome === 'marketing' && !(state.marketingEnabled && podeGerirEquipe())) {
    return { route: 'dashboard', param: null };
  }
  return { route: ROUTES.includes(nome) && nome !== 'lead' ? nome : 'dashboard', param: null };
}

function renderCurrentPage() {
  if (!state.user) return;
  const fn = RENDERERS[state.route];
  if (fn) fn();
}

function showPage(route) {

  const outgoing = document.getElementById(`page-${state.route}`);
  if (outgoing && state.route !== route) destroyCharts(outgoing);

  state.route = route;

  for (const r of ROUTES) {
    const page = document.getElementById(`page-${r}`);
    if (page) page.hidden = r !== route;
  }
  document.querySelectorAll('.nav__item').forEach((a) => {
    a.classList.toggle('is-active', a.dataset.route === route);
    if (a.dataset.route === route) a.setAttribute('aria-current', 'page');
    else a.removeAttribute('aria-current');
  });

  renderCurrentPage();
  closeDrawer();
  document.getElementById('content')?.focus({ preventScroll: true });
  window.scrollTo({ top: 0, behavior: 'auto' });
}

async function carregarDaRota(route, param) {
  if (route === 'dashboard') {

    await loadOnboarding();
  } else if (route === 'lead' && param) {
    await loadLead(param);
  } else if (route === 'propostas') {
    if (planoInclui('propostas')) await loadProposals();
  } else if (route === 'automacoes') {

    if (planoInclui('automacoes')) await loadAutomations();
  } else if (route === 'inteligencia') {
    await loadIntel();
  } else if (route === 'relatorios') {
    await loadAvancado();
  } else if (route === 'cobranca') {
    await Promise.all([loadCobranca(), loadFaturas()]);
  } else if (route === 'config') {
    await loadSettings();
    renderSettings();
  } else if (route === 'admin') {

    if (state.user?.is_owner) await loadAdmin();
  } else if (route === 'equipe') {
    await loadEquipe();
  } else if (route === 'marketing') {
    await loadMarketing();
    wireMarketing();
  }
}

function checarConvite() {
  const m = (location.hash || '').match(/^#\/convite\/(.+)$/);
  if (m && m[1]) {
    setPendingInvite(decodeURIComponent(m[1]));
    return true;
  }
  return false;
}

async function onHashChange() {
  if (!state.user) return;
  if (checarConvite()) { abrirConviteSePendente(); return; }
  const { route, param } = routeFromHash();
  if (route !== 'lead') store.leadId = null;
  showPage(route);
  await carregarDaRota(route, param);
  renderCurrentPage();
}

async function refreshData({ quiet = false } = {}) {
  if (!state.user) return;
  state.loading = true;
  try {

    const [leadsRes, statsRes, fupRes, lossRes, camposRes, notifRes, planoRes, semAcaoRes] =
      await Promise.allSettled([
        api.listLeads(), api.stats(), api.followups(),
        api.lossReport(), api.customFields(), api.notifications(),
        api.assinatura(), api.semProximaAcao(),

        loadOnboarding(),
      ]);

    const naoAutenticado = [leadsRes, statsRes, fupRes]
      .some((r) => r.status === 'rejected' && r.reason?.status === 401);
    if (naoAutenticado) return;

    if (leadsRes.status === 'fulfilled') {
      const resposta = leadsRes.value;

      const lista = Array.isArray(resposta) ? resposta : (resposta?.items || []);
      state.leads = lista;
      state.leadsTotal = Array.isArray(resposta) ? lista.length : (resposta?.total ?? lista.length);
      state.leadsTruncado = !Array.isArray(resposta) && Boolean(resposta?.truncado);
      state.leadsTeto = Array.isArray(resposta) ? 0 : (resposta?.teto || 0);
    }
    if (statsRes.status === 'fulfilled') state.stats = statsRes.value || null;
    if (fupRes.status === 'fulfilled') state.followups = fupRes.value || null;
    if (semAcaoRes.status === 'fulfilled') state.semAcao = semAcaoRes.value || null;
    if (lossRes.status === 'fulfilled') state.losses = lossRes.value || null;

    if (camposRes.status === 'fulfilled') store.customFields = camposRes.value || [];
    if (notifRes.status === 'fulfilled') store.notifications = notifRes.value;
    if (planoRes.status === 'fulfilled') cobranca.assinatura = planoRes.value;

    const falha = [leadsRes, statsRes].find((r) => r.status === 'rejected');
    if (falha && !quiet) toast(falha.reason?.message || 'Falha ao carregar os dados.', 'error');
  } finally {
    state.loading = false;
  }
  renderFollowupBadge();
  renderNotifications();
  renderAvisoPlano();
  renderCurrentPage();
}

function setLeadError(message) {
  const box = document.getElementById('lead-error');
  if (!box) return;
  if (!message) {
    box.textContent = '';
    box.hidden = true;
    return;
  }
  box.textContent = message;
  box.hidden = false;
}

function montarCamposPersonalizados(valores = {}) {
  const alvo = document.getElementById('lead-custom-fields');
  if (!alvo) return;
  clear(alvo);

  const campos = (store.customFields || []).filter((c) => c.active);
  if (!campos.length) return;

  alvo.append(el('p', { class: 'formsection', text: 'Campos da sua empresa' }));

  for (const campo of campos) {
    const id = `cf-${campo.key}`;
    const valor = valores[campo.key];
    let entrada;

    if (campo.type === 'lista') {
      entrada = el('select', { attrs: { id, 'data-cf': campo.key } }, [
        el('option', { attrs: { value: '' }, text: campo.required ? 'Escolha…' : '—' }),
        ...campo.options.map((o) => el('option', { attrs: { value: o, selected: o === valor }, text: o })),
      ]);
    } else if (campo.type === 'multipla') {
      const marcados = new Set([].concat(valor || []));
      entrada = el('div', { class: 'checkgroup', attrs: { id, 'data-cf': campo.key, 'data-multi': '' } },
        campo.options.map((o) => el('label', { class: 'check check--inline' }, [
          el('input', { attrs: { type: 'checkbox', value: o, checked: marcados.has(o) } }),
          el('span', { text: o }),
        ])));
    } else if (campo.type === 'sim_nao') {
      entrada = el('label', { class: 'check' }, [
        el('input', { attrs: { type: 'checkbox', id, 'data-cf': campo.key, checked: Boolean(valor) } }),
        el('span', { text: campo.description || 'Sim' }),
      ]);
    } else {
      const tipoHtml = { numero: 'number', moeda: 'text', data: 'date', email: 'email', telefone: 'tel' }[campo.type] || 'text';
      entrada = el('input', {
        attrs: {
          type: tipoHtml, id, 'data-cf': campo.key, value: valor ?? '',
          maxlength: 500, inputmode: campo.type === 'moeda' ? 'decimal' : false,
        },
      });
    }

    const filhos = [];
    if (campo.type !== 'sim_nao') {
      filhos.push(el('label', { attrs: { for: id } }, [
        document.createTextNode(campo.label),
        campo.required ? el('span', { class: 'req', text: 'obrigatório' }) : null,
      ].filter(Boolean)));
    }
    filhos.push(entrada);
    if (campo.description && campo.type !== 'sim_nao') {
      filhos.push(el('small', { class: 'field__hint', text: campo.description }));
    }
    alvo.append(el('div', { class: 'field' }, filhos));
  }
}

function lerCamposPersonalizados() {
  const valores = {};
  for (const no of document.querySelectorAll('#lead-custom-fields [data-cf]')) {
    const chave = no.dataset.cf;
    if (no.hasAttribute('data-multi')) {
      valores[chave] = [...no.querySelectorAll('input:checked')].map((i) => i.value);
    } else if (no.type === 'checkbox') {
      valores[chave] = no.checked;
    } else {
      valores[chave] = no.value;
    }
  }
  return valores;
}

function openLeadModal(id) {
  const modal = document.getElementById('lead-modal');
  const form = document.getElementById('lead-form');
  const title = document.getElementById('lead-modal-title');
  if (!modal || !form) return;

  form.reset();
  setLeadError('');
  document.getElementById('lead-id').value = '';

  if (id != null) {
    const lead = state.leads.find((l) => String(l.id) === String(id));
    if (!lead) {
      toast('Este lead não está mais disponível.', 'error');
      return;
    }
    title.textContent = 'Editar lead';
    document.getElementById('lead-id').value = lead.id;

    document.getElementById('lead-name').value = lead.name || '';
    document.getElementById('lead-company').value = lead.company || '';
    document.getElementById('lead-value').value = String(leadValue(lead));

    const etapas = [...document.getElementById('lead-status').options].map((o) => o.value);
    document.getElementById('lead-status').value = etapas.includes(lead.status) ? lead.status : etapas[0];
    document.getElementById('lead-segment').value = lead.segment || 'Outros';
    document.getElementById('lead-email').value = lead.email || '';
    document.getElementById('lead-phone').value = lead.phone || '';
    document.getElementById('lead-whatsapp').value = lead.whatsapp || '';
    document.getElementById('lead-source').value = lead.source || '';
    document.getElementById('lead-owner').value = lead.owner || '';
    document.getElementById('lead-notes').value = lead.notes || '';
    document.getElementById('lead-tags-input').value = (lead.tags || []).join(', ');
    montarCamposPersonalizados(lead.custom || {});
  } else {
    title.textContent = 'Novo lead';
    montarCamposPersonalizados({});
  }

  openModal(modal, { focus: '#lead-name' });
}

async function submitLead(e) {
  e.preventDefault();
  setLeadError('');

  const id = document.getElementById('lead-id').value;
  const name = document.getElementById('lead-name').value.trim();
  const company = document.getElementById('lead-company').value.trim();
  const rawValue = document.getElementById('lead-value').value;
  const status = document.getElementById('lead-status').value;
  const segment = document.getElementById('lead-segment').value;
  const button = document.getElementById('lead-save');

  if (!name) { setLeadError('Informe o nome do cliente.'); document.getElementById('lead-name').focus(); return; }
  if (!company) { setLeadError('Informe a empresa.'); document.getElementById('lead-company').focus(); return; }

  const value = parseAmount(rawValue);
  if (value === null) {
    setLeadError('Informe um valor numérico válido, por exemplo 25000 ou 25.000,50.');
    document.getElementById('lead-value').focus();
    return;
  }
  if (!STATUSES.includes(status)) { setLeadError('Selecione um status válido.'); return; }

  const payload = {
    name, company, value, status, segment,
    email: document.getElementById('lead-email').value.trim(),
    phone: document.getElementById('lead-phone').value.trim(),
    whatsapp: document.getElementById('lead-whatsapp').value.trim(),
    source: document.getElementById('lead-source').value.trim(),
    owner: document.getElementById('lead-owner').value.trim(),
    notes: document.getElementById('lead-notes').value.trim(),
    tags: document.getElementById('lead-tags-input').value
      .split(',').map((t) => t.trim()).filter(Boolean),
    custom: lerCamposPersonalizados(),
  };

  button.disabled = true;
  try {
    if (id) {
      await api.updateLead(id, payload);
      toast('Lead atualizado.', 'success');
    } else {
      await api.createLead(payload);
      toast('Lead cadastrado.', 'success');
    }
    closeModal();
    await refreshData();

    if (store.leadId && String(store.leadId) === String(id)) {
      await loadLead(store.leadId);
      renderLead();
    }
  } catch (err) {
    if (err?.status !== 401) setLeadError(err?.message || 'Não foi possível salvar o lead.');
  } finally {
    button.disabled = false;
  }
}

async function removeLead(id) {
  const lead = state.leads.find((l) => String(l.id) === String(id));
  if (!lead) return;

  let junto = null;
  try {
    junto = await api.leadImpact(id);
  } catch {  }

  const pedacos = [];
  if (junto?.atividades) pedacos.push(`${junto.atividades} registro(s) de histórico`);
  if (junto?.propostas) pedacos.push(`${junto.propostas} proposta(s)`);
  if (junto?.mensagens) pedacos.push(`${junto.mensagens} mensagem(ns) de WhatsApp`);

  const texto = pedacos.length
    ? `Some junto: ${pedacos.join(', ')}. O cadastro sai do funil e deixa de contar nos relatórios.`
    : 'O cadastro sai do funil e deixa de contar na receita, no ticket médio e nos relatórios.';

  const ok = await confirmar({
    titulo: 'Excluir este lead?',
    texto,
    alvo: { nome: lead.name, meta: `${lead.company} · ${brl(lead.value)} · ${lead.status}` },
    aviso: junto?.propostas_aceitas
      ? `Atenção: há ${junto.propostas_aceitas} proposta(s) ACEITA(S) neste negócio. Esta ação não pode ser desfeita.`
      : 'Esta ação não pode ser desfeita.',
    confirmar: 'Excluir lead',
  });
  if (!ok) return;

  try {
    await api.deleteLead(id);
    toast('Lead excluído.', 'info');

    if (store.leadId && String(store.leadId) === String(id)) {
      store.leadId = null;
      location.hash = '#/leads';
    }

    await refreshData();
  } catch (err) {
    if (err?.status !== 401) toast(err?.message || 'Não foi possível excluir o lead.', 'error');
  }
}

async function moveLead(id, newStatus) {
  const lead = state.leads.find((l) => String(l.id) === String(id));
  if (!lead || lead.status === newStatus || !STATUSES.includes(newStatus)) return;

  let extra = {};
  if (newStatus === 'Perdido') {
    const escolha = await pedirMotivo(lead);
    if (!escolha) { renderCurrentPage(); return; }
    extra = { lost_reason: escolha.reason, lost_note: escolha.note };
  }

  const previous = lead.status;
  lead.status = newStatus;
  renderCurrentPage();
  try {
    await api.updateLead(id, { status: newStatus, ...extra });
    toast(newStatus === 'Ganho' ? 'Negócio ganho. Parabéns.' : `Negócio movido para ${newStatus}.`,
      newStatus === 'Perdido' ? 'info' : 'success');
    await refreshData({ quiet: true });
  } catch (err) {
    lead.status = previous;
    renderCurrentPage();
    if (err?.status !== 401) toast(err?.message || 'Não foi possível mover o lead.', 'error');
  }
}

function csvCell(value) {
  let s = value == null ? '' : String(value);
  if (/^[=+\-@\t\r]/.test(s)) s = `'${s}`;
  if (/["\n\r;]/.test(s)) s = `"${s.replace(/"/g, '""')}"`;
  return s;
}

function exportCsv() {
  const rows = filteredLeads();
  if (rows.length === 0) {
    toast('Não há leads para exportar.', 'error');
    return;
  }
  const header = ['ID', 'Cliente', 'Empresa', 'Segmento', 'Valor', 'Status', 'Criado em'];
  const lines = [header.map(csvCell).join(';')];
  for (const l of rows) {
    lines.push([
      l.id, l.name, l.company, l.segment || 'Outros',
      leadValue(l).toFixed(2).replace('.', ','),
      l.status, l.created_at || '',
    ].map(csvCell).join(';'));
  }

  const blob = new Blob([`\uFEFF${lines.join('\r\n')}\r\n`], { type: 'text/csv;charset=utf-8;' });
  const url = URL.createObjectURL(blob);
  const link = el('a', { attrs: { href: url, download: `vertex-leads-${new Date().toISOString().slice(0, 10)}.csv` } });
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
  // Dizer "247 exportados" quando a conta tem 6.000 seria verdade pela metade.
  toast(
    state.leadsTruncado
      ? `${num(rows.length)} de ${num(state.leadsTotal)} lead(s) exportado(s) — a lista veio limitada.`
      : `${num(rows.length)} lead(s) exportado(s).`,
    state.leadsTruncado ? 'info' : 'success',
  );
}

/* ============================================================
   13. DRAWER (mobile)
   ============================================================ */

function openDrawer() {
  document.getElementById('sidebar')?.classList.add('is-open');
  const scrim = document.getElementById('nav-scrim');
  if (scrim) scrim.hidden = false;
  document.getElementById('nav-open')?.setAttribute('aria-expanded', 'true');
  document.getElementById('nav-close')?.focus();
}

function closeDrawer() {
  document.getElementById('sidebar')?.classList.remove('is-open');
  const scrim = document.getElementById('nav-scrim');
  if (scrim) scrim.hidden = true;
  document.getElementById('nav-open')?.setAttribute('aria-expanded', 'false');
}

/* ============================================================
   14. SESSÃO
   ============================================================ */

/** Desenha a foto num elemento de avatar, ou as iniciais quando nao ha foto.
 *  A imagem entra como fundo para nao mexer no conteudo de texto (as iniciais
 *  continuam la' e reaparecem sozinhas se a foto for removida). */
function pintarAvatar(node, userId, chave, nome) {
  if (!node) return;
  node.textContent = initials(nome);
  const url = api.avatarUrl(userId, chave);
  if (url) {
    node.style.backgroundImage = `url("${url}")`;
    node.classList.add('avatar--foto');
  } else {
    node.style.backgroundImage = '';
    node.classList.remove('avatar--foto');
  }
}

function paintUser() {
  const ini = initials(state.user?.name);
  const top = document.getElementById('top-avatar');
  const side = document.getElementById('side-avatar');
  if (top) { top.textContent = ini; top.title = state.user?.name || ''; }
  if (side) side.textContent = ini;
  // Foto onde ela faz sentido: topo, menu lateral e a propria tela de perfil.
  const eu = state.user?.id;
  const foto = state.user?.avatar || '';
  pintarAvatar(top, eu, foto, state.user?.name);
  pintarAvatar(side, eu, foto, state.user?.name);
  pintarAvatar(document.getElementById('perfil-avatar'), eu, foto, state.user?.name);
  const btnRemover = document.getElementById('perfil-foto-remover');
  if (btnRemover) btnRemover.hidden = !foto;
  const name = document.getElementById('side-name');
  const mail = document.getElementById('side-email');
  if (name) name.textContent = state.user?.name || '';
  if (mail) mail.textContent = state.user?.email || '';

  // O item "Painel do dono" nasce oculto no HTML e só é revelado para o dono.
  // Não é uma trava de segurança (o backend é quem barra /api/admin/*), é só
  // não poluir o menu de quem não usa.
  const ownerNav = document.querySelector('.nav__item--owner');
  if (ownerNav) ownerNav.hidden = !state.user?.is_owner;
  // O bloco do `.env` do WhatsApp é documentação de quem OPERA o servidor.
  // Para o cliente ele seria uma instrução impossível de seguir (ele não tem
  // acesso ao servidor) e ainda mostraria infraestrutura sem necessidade.
  const waNote = document.getElementById('wa-note');
  const waNoteCliente = document.getElementById('wa-note-cliente');
  if (waNote) waNote.hidden = !state.user?.is_owner;
  if (waNoteCliente) waNoteCliente.hidden = !!state.user?.is_owner;
  // "Equipe": só para quem gerencia (admin/gestor). Mesma ideia -- cosmético.
  const teamNav = document.querySelector('.nav__item--team');
  if (teamNav) teamNav.hidden = !podeGerirEquipe();
  // "E-mail marketing": só quando o servidor liga a flag E o papel gerencia.
  // O backend barra /api/marketing/* de todo jeito -- isto é só higiene de menu.
  const mktNav = document.querySelector('.nav__item--mkt');
  if (mktNav) mktNav.hidden = !(state.marketingEnabled && podeGerirEquipe());
}

/** Admin ou Gestor podem gerir a equipe. A verdade mora no backend; aqui é só
 *  para revelar o menu e a rota certos. */
function podeGerirEquipe() {
  return state.user?.role === 'admin' || state.user?.role === 'gestor';
}

async function enterApp(user) {
  state.user = user;
  // A flag do e-mail marketing vem do servidor. Guardamos antes de pintar o
  // menu, senão o item apareceria/sumiria num flash. Falha => desligado.
  try {
    const cfg = await api.config();
    state.marketingEnabled = !!(cfg && cfg.marketing_enabled);
  } catch { state.marketingEnabled = false; }
  paintUser();
  hideAuth();

  try { Scene.unmount(); } catch { /* ignora */ }
  try { Scene.mount(document.getElementById('scene-app'), { mode: 'app' }); } catch { /* ignora */ }

  if (!location.hash) location.hash = '#/dashboard';
  const { route, param } = routeFromHash();
  state.route = route;
  showPage(route);
  // Os dados gerais primeiro: a página do lead precisa da lista carregada para
  // achar o registro pelo id da URL.
  await refreshData();
  await carregarDaRota(route, param);
  renderCurrentPage();
  // Chegou por um link de convite (logado ou recém-cadastrado)? Abre o aceite.
  abrirConviteSePendente();
}

let leavingSession = false;

function leaveToLogin({ message } = {}) {
  if (leavingSession) return;
  leavingSession = true;

  state.user = null;
  state.leads = [];
  state.stats = null;
  state.search = '';

  const search = document.getElementById('global-search');
  if (search) search.value = '';
  const clearBtn = document.getElementById('search-clear');
  if (clearBtn) clearBtn.hidden = true;

  closeModal();
  closeDrawer();
  destroyCharts(document);
  try { Scene.unmount(); } catch { /* ignora */ }
  try { Scene.mount(document.getElementById('scene-login'), { mode: 'login' }); } catch { /* ignora */ }

  document.getElementById('paywall-screen')?.classList.add('is-hidden');
  showAuth();
  if (message) toast(message, 'info');
  setTimeout(() => { leavingSession = false; }, 400);
}

async function doLogout() {
  // O servidor apaga a sessão e os dois cookies. Só DEPOIS disso saímos daqui:
  // navegar antes deixaria a sessão viva no servidor.
  try { await api.logout(); } catch { /* mesmo falhando, o estado local sai */ }
  // Limpa o estado da memória e FICA na tela de login (não some para a landing
  // sozinho, como antes). Daqui a pessoa decide: entrar de novo, ou voltar ao
  // site pelo link "Voltar ao site". `leaveToLogin` já mostra o login,
  // desmonta gráficos/cena e limpa o estado -- nenhum dado de outra conta fica
  // desenhado na tela. O hash antigo (#/dashboard) é inofensivo: o login cobre
  // tudo e um reload cai em `api.me()` -> sem sessão -> login.
  leaveToLogin();
}

async function saveProfile(e) {
  e.preventDefault();
  const box = document.getElementById('profile-error');
  const input = document.getElementById('profile-name');
  const name = input.value.trim();

  if (box) { box.hidden = true; box.textContent = ''; }
  if (!name) {
    if (box) { box.textContent = 'O nome não pode ficar vazio.'; box.hidden = false; }
    input.focus();
    return;
  }
  try {
    const user = await api.updateProfile({ name });
    state.user = user || { ...state.user, name };
    paintUser();
    toast('Perfil atualizado.', 'success');
  } catch (err) {
    if (err?.status !== 401 && box) { box.textContent = err?.message || 'Não foi possível salvar.'; box.hidden = false; }
  }
}

/* ---------- foto de perfil ---------- */

const FOTO_MAX_BYTES = 5 * 1024 * 1024;

function erroFoto(msg) {
  const box = document.getElementById('perfil-foto-erro');
  if (!box) return;
  box.textContent = msg || '';
  box.hidden = !msg;
}

async function enviarFoto(arquivo) {
  erroFoto('');
  if (!arquivo) return;
  // Checagem no navegador para dar resposta imediata. NAO e' a validacao de
  // verdade: o servidor refaz tudo (tipo real, dimensoes, tamanho) porque o
  // que chega do navegador nunca decide nada.
  if (arquivo.size > FOTO_MAX_BYTES) {
    erroFoto('A imagem excede o limite de 5 MB.');
    return;
  }
  const base64 = await new Promise((resolve, reject) => {
    const leitor = new FileReader();
    leitor.onload = () => resolve(String(leitor.result || ''));
    leitor.onerror = () => reject(new Error('Não foi possível ler o arquivo.'));
    leitor.readAsDataURL(arquivo);
  }).catch((e) => { erroFoto(e.message); return ''; });
  if (!base64) return;

  try {
    const r = await api.enviarAvatar(base64);
    state.user = { ...state.user, avatar: r.avatar };
    paintUser();
    toast('Foto atualizada.', 'success');
  } catch (err) {
    // A mensagem vem do servidor e ja' esta escrita para a pessoa ler
    // ("A imagem excede o limite de 5 MB."), nao um erro tecnico.
    erroFoto(err?.message || 'Não foi possível enviar a foto.');
  }
}

async function removerFoto() {
  const ok = await confirmar({
    titulo: 'Remover foto',
    texto: 'Sua foto de perfil será apagada. Você volta a aparecer com as suas iniciais.',
    aviso: 'O arquivo é apagado do servidor.',
    confirmar: 'Remover',
  });
  if (!ok) return;
  try {
    await api.removerAvatar();
    state.user = { ...state.user, avatar: '' };
    paintUser();
    toast('Foto removida.', 'info');
  } catch (err) {
    erroFoto(err?.message || 'Não foi possível remover a foto.');
  }
}

function wireFoto() {
  const input = document.getElementById('perfil-foto-input');
  document.getElementById('perfil-foto-btn')?.addEventListener('click', () => input?.click());
  input?.addEventListener('change', () => {
    enviarFoto(input.files && input.files[0]);
    input.value = '';   // permite reenviar o MESMO arquivo depois de um erro
  });
  document.getElementById('perfil-foto-remover')?.addEventListener('click', removerFoto);
}

/* ---------- abas de Configurações ---------- */

function abrirAba(nome) {
  document.querySelectorAll('.abas__b').forEach((b) => {
    const ativo = b.dataset.ir === nome;
    b.classList.toggle('is-on', ativo);
    b.setAttribute('aria-selected', ativo ? 'true' : 'false');
  });
  document.querySelectorAll('[data-aba]').forEach((bloco) => {
    bloco.hidden = bloco.dataset.aba !== nome;
  });
  // A aba de Segurança é a única que busca dados ao abrir.
  if (nome === 'seguranca') carregarSessoes();
}

function wireAbas() {
  document.querySelectorAll('.abas__b').forEach((b) => {
    b.addEventListener('click', () => abrirAba(b.dataset.ir));
  });
  abrirAba('perfil');
}

/* ---------- senha ---------- */

async function trocarSenha(e) {
  e.preventDefault();
  const erro = document.getElementById('senha-erro');
  const ok = document.getElementById('senha-ok');
  const atual = document.getElementById('senha-atual');
  const nova = document.getElementById('senha-nova');
  if (erro) erro.hidden = true;
  if (ok) ok.hidden = true;

  if ((nova.value || '').length < 8) {
    if (erro) { erro.textContent = 'A nova senha precisa ter pelo menos 8 caracteres.'; erro.hidden = false; }
    return;
  }
  try {
    await api.trocarSenha(atual.value, nova.value);
    atual.value = '';
    nova.value = '';
    if (ok) ok.hidden = false;
    toast('Senha alterada.', 'success');
    carregarSessoes();
  } catch (err) {
    // A mensagem vem do servidor ("A senha atual está incorreta.") e já está
    // escrita para a pessoa ler.
    if (erro) { erro.textContent = err?.message || 'Não foi possível alterar a senha.'; erro.hidden = false; }
  }
}

/* ---------- sessões ---------- */

function linhaSessao(s) {
  const quando = (iso) => {
    const t = Date.parse(iso);
    return Number.isNaN(t) ? '—' : new Date(t).toLocaleString('pt-BR',
      { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' });
  };
  return el('div', { class: 'sessao' }, [
    el('div', { class: 'sessao__txt' }, [
      el('strong', {}, [
        document.createTextNode(s.device || 'Aparelho desconhecido'),
        s.atual ? el('span', { class: 'ownertag', text: 'este aparelho' }) : null,
      ]),
      el('small', { text: `Entrou em ${quando(s.created_at)} · última atividade ${quando(s.last_seen_at)}` }),
    ]),
  ]);
}

async function carregarSessoes() {
  const caixa = document.getElementById('sessoes-lista');
  if (!caixa) return;
  try {
    const r = await api.sessoes();
    clear(caixa);
    const itens = r.items || [];
    for (const s of itens) caixa.append(linhaSessao(s));
    const btn = document.getElementById('sessoes-encerrar');
    if (btn) btn.hidden = itens.length < 2;
  } catch (err) {
    if (err?.status !== 401) toast(err?.message || 'Não foi possível ler as sessões.', 'error');
  }
}

async function encerrarOutras() {
  const ok = await confirmar({
    titulo: 'Encerrar as outras sessões',
    texto: 'Sua conta será desconectada de todos os outros aparelhos. Este continua conectado.',
    aviso: 'Quem estiver usando a conta em outro lugar precisará entrar de novo.',
    confirmar: 'Encerrar',
  });
  if (!ok) return;
  try {
    const r = await api.encerrarOutrasSessoes();
    const caixa = document.getElementById('sessoes-lista');
    if (caixa) { clear(caixa); for (const s of (r.items || [])) caixa.append(linhaSessao(s)); }
    const btn = document.getElementById('sessoes-encerrar');
    if (btn) btn.hidden = true;
    toast('Outras sessões encerradas.', 'success');
  } catch (err) {
    toast(err?.message || 'Não foi possível encerrar as sessões.', 'error');
  }
}

/* ---------- troca de e-mail (dois passos) ---------- */

function abrirTrocaDeEmail() {
  const p1 = document.getElementById('email-passo1');
  const p2 = document.getElementById('email-passo2');
  if (p1) p1.hidden = false;
  if (p2) p2.hidden = true;
  ['email-erro1', 'email-erro2'].forEach((id) => {
    const n = document.getElementById(id); if (n) n.hidden = true;
  });
  ['email-novo', 'email-senha', 'email-codigo'].forEach((id) => {
    const n = document.getElementById(id); if (n) n.value = '';
  });
  openModal(document.getElementById('email-modal'), { focus: '#email-novo' });
}

async function pedirTrocaDeEmail(e) {
  e.preventDefault();
  const erro = document.getElementById('email-erro1');
  if (erro) erro.hidden = true;
  const novo = (document.getElementById('email-novo')?.value || '').trim();
  const senha = document.getElementById('email-senha')?.value || '';
  try {
    const r = await api.pedirTrocaDeEmail(novo, senha);
    const destino = document.getElementById('email-destino');
    if (destino) destino.textContent = r.email || novo;
    document.getElementById('email-passo1').hidden = true;
    document.getElementById('email-passo2').hidden = false;
    document.getElementById('email-codigo')?.focus();
  } catch (err) {
    if (erro) { erro.textContent = err?.message || 'Não foi possível iniciar a troca.'; erro.hidden = false; }
  }
}

async function confirmarTrocaDeEmail(e) {
  e.preventDefault();
  const erro = document.getElementById('email-erro2');
  if (erro) erro.hidden = true;
  const codigo = (document.getElementById('email-codigo')?.value || '').trim();
  try {
    const u = await api.confirmarTrocaDeEmail(codigo);
    state.user = { ...state.user, email: u.email, is_owner: u.is_owner };
    paintUser();
    const campo = document.getElementById('profile-email');
    if (campo) campo.value = u.email;
    closeModal();
    toast('E-mail alterado.', 'success');
  } catch (err) {
    if (erro) { erro.textContent = err?.message || 'Não foi possível confirmar.'; erro.hidden = false; }
  }
}

function wireConta() {
  wireAbas();
  document.getElementById('senha-form')?.addEventListener('submit', trocarSenha);
  document.getElementById('sessoes-encerrar')?.addEventListener('click', encerrarOutras);
  document.getElementById('email-trocar-btn')?.addEventListener('click', abrirTrocaDeEmail);
  document.getElementById('email-passo1')?.addEventListener('submit', pedirTrocaDeEmail);
  document.getElementById('email-passo2')?.addEventListener('submit', confirmarTrocaDeEmail);
  document.getElementById('email-modal')?.addEventListener('click', (e) => {
    if (e.target.closest('[data-fechar]')) closeModal();
  });
  document.getElementById('pp-voltar-btn')?.addEventListener('click', async () => {
    try {
      await reexibirOnboarding();
      renderConfig();
      toast('Primeiros passos de volta no Dashboard.', 'success');
    } catch {
      toast('Não foi possível agora. Tente de novo.', 'error');
    }
  });
}

/* ---------- LGPD: exportar e excluir meus dados (#45) ---------- */

async function exportarDados() {
  try {
    const dados = await api.exportMyData();
    const blob = new Blob([JSON.stringify(dados, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'meus-dados-vertex.json';
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
    toast('Seus dados foram baixados.', 'success');
  } catch (err) {
    if (err?.status !== 401) toast(err?.message || 'Não foi possível exportar.', 'error');
  }
}

function abrirExclusao() {
  const pw = document.getElementById('lgpd-password');
  if (pw) pw.value = '';
  const cf = document.getElementById('lgpd-confirm');
  if (cf) cf.value = '';
  document.getElementById('lgpd-pw-field').hidden = false;
  document.getElementById('lgpd-confirm-field').hidden = true;
  const err = document.getElementById('lgpd-modal-error');
  if (err) err.hidden = true;
  openModal(document.getElementById('lgpd-modal'), { focus: '#lgpd-password' });
}

async function confirmarExclusao() {
  const err = document.getElementById('lgpd-modal-error');
  const setErr = (m) => { if (err) { err.textContent = m || ''; err.hidden = !m; } };
  setErr('');
  const password = document.getElementById('lgpd-password')?.value || '';
  const confirm = document.getElementById('lgpd-confirm')?.value || '';
  const btn = document.getElementById('lgpd-confirm-btn');
  if (btn) btn.disabled = true;
  try {
    await api.deleteAccount({ password, confirm });
    closeModal();
    leaveToLogin({ message: 'Conta excluída. Sentiremos sua falta.' });
  } catch (e) {
    if (e?.status === 400 && /EXCLUIR/i.test(e.message || '')) {
      // conta só-Google (sem senha local): revela o campo da palavra
      document.getElementById('lgpd-pw-field').hidden = true;
      document.getElementById('lgpd-confirm-field').hidden = false;
      document.getElementById('lgpd-confirm')?.focus();
      setErr('Esta conta usa login com Google. Digite EXCLUIR para confirmar.');
    } else if (e?.status !== 401) {
      setErr(e?.message || 'Não foi possível excluir a conta.');
    }
  } finally {
    if (btn) btn.disabled = false;
  }
}

/* ============================================================
   15. EVENTOS GLOBAIS
   ============================================================ */

let searchTimer = null;

function wireGlobalEvents() {
  // navegação
  window.addEventListener('hashchange', onHashChange);
  document.getElementById('nav-open')?.addEventListener('click', openDrawer);
  document.getElementById('nav-close')?.addEventListener('click', closeDrawer);
  document.getElementById('nav-scrim')?.addEventListener('click', closeDrawer);

  // tema
  document.getElementById('theme-btn')?.addEventListener('click', () => {
    const next = document.body.classList.contains('light-theme') ? 'dark' : 'light';
    applyTheme(next);
    saveTheme(next);
    renderCurrentPage(); // gráficos relêem as cores dos tokens
  });
  document.querySelectorAll('input[name="theme"]').forEach((radio) => {
    radio.addEventListener('change', () => {
      if (!radio.checked) return;
      applyTheme(radio.value);
      saveTheme(radio.value);
      renderCurrentPage();
    });
  });

  // A busca do topo passou a ser GLOBAL, atendida pelo servidor (`notify.js`):
  // procura em leads, propostas, histórico e campos personalizados, e o clique
  // leva direto ao registro. O filtro que ela fazia antes — esconder linhas da
  // tabela de leads enquanto se digitava — saiu de cena: com um painel de
  // resultados aberto, a lista mudando por baixo era ruído, não ajuda.

  // modais: o mesmo tratamento serve para todos (fechar pelo X, pelo botão
  // Cancelar ou clicando no scrim, que também carrega data-close-modal)
  document.getElementById('lead-form')?.addEventListener('submit', submitLead);
  // WhatsApp e Telefone são número, não texto: filtra letras enquanto se digita
  // (feedback imediato). O backend também limpa -- aqui é só a experiência.
  for (const id of ['lead-whatsapp', 'lead-phone']) {
    const campo = document.getElementById(id);
    if (!campo) continue;
    campo.addEventListener('input', () => {
      const limpo = campo.value.replace(/[^\d+()\-\s]/g, '');
      if (limpo === campo.value) return;
      const desloca = campo.value.length - limpo.length;
      const pos = Math.max(0, (campo.selectionStart || limpo.length) - desloca);
      campo.value = limpo;
      try { campo.setSelectionRange(pos, pos); } catch { /* ok */ }
    });
  }
  [
    'lead-modal', 'confirm-modal', 'loss-modal', 'prop-modal',
    'send-modal', 'auto-modal', 'field-modal', 'tpl-modal', 'import-modal', 'lgpd-modal',
    'admin-modal', 'convite-modal',
  ].forEach((id) => {
    document.getElementById(id)?.addEventListener('click', (e) => {
      if (e.target.closest('[data-close-modal]')) closeModal();
    });
  });

  // acompanhamento
  document.getElementById('fup-refresh')?.addEventListener('click', () => refreshData());
  document.getElementById('fup-filters')?.addEventListener('click', (e) => {
    const botao = e.target.closest('[data-sev]');
    if (!botao) return;
    state.fupFilter = botao.dataset.sev;
    document.querySelectorAll('#fup-filters [data-sev]').forEach((b) => {
      b.classList.toggle('is-active', b === botao);
    });
    renderFollowups();
  });

  // logout e perfil
  document.getElementById('logout-btn')?.addEventListener('click', doLogout);
  document.getElementById('logout-btn-2')?.addEventListener('click', doLogout);
  document.getElementById('profile-form')?.addEventListener('submit', saveProfile);
  wireFoto();
  wireConta();
  document.getElementById('lgpd-export')?.addEventListener('click', exportarDados);
  document.getElementById('lgpd-delete')?.addEventListener('click', abrirExclusao);
  document.getElementById('lgpd-confirm-btn')?.addEventListener('click', confirmarExclusao);

  // delegação: abrir modal, exportar, editar, excluir
  document.addEventListener('click', (e) => {
    if (e.target.closest('[data-open-lead]')) { openLeadModal(null); return; }
    if (e.target.closest('[data-export-csv]')) { exportCsv(); return; }
    if (e.target.closest('[data-open-import]')) { openImport(); return; }
    const edit = e.target.closest('[data-edit]');
    if (edit) { openLeadModal(edit.dataset.edit); return; }
    const abrir = e.target.closest('[data-followup-open]');
    if (abrir) { location.hash = `#/lead/${abrir.dataset.followupOpen}`; return; }
    const contato = e.target.closest('[data-followup-touch]');
    if (contato) { touchLead(contato.dataset.followupTouch); return; }
    const ver = e.target.closest('[data-open-detail]');
    if (ver) { location.hash = `#/lead/${ver.dataset.openDetail}`; return; }
    const del = e.target.closest('[data-delete]');
    if (del) { removeLead(del.dataset.delete); }
  });

  // mover status pelo select (teclado e toque, onde arrastar não funciona)
  document.addEventListener('change', (e) => {
    const sel = e.target.closest('[data-move]');
    if (sel) moveLead(sel.dataset.move, sel.value);
  });

  wireDragAndDrop();
  wireImport();
}

/* ---------- arrastar e soltar ---------- */

function wireDragAndDrop() {
  const board = document.getElementById('kanban');
  if (!board) return;
  let draggedId = null;

  board.addEventListener('dragstart', (e) => {
    const card = e.target.closest('.kcard');
    if (!card) return;
    draggedId = card.dataset.lead;
    card.classList.add('is-dragging');
    e.dataTransfer.effectAllowed = 'move';
    try { e.dataTransfer.setData('text/plain', draggedId); } catch { /* Safari antigo */ }
  });

  // O app antigo nunca removia `.dragging`, então o card ficava a 40% para sempre.
  board.addEventListener('dragend', (e) => {
    e.target.closest('.kcard')?.classList.remove('is-dragging');
    board.querySelectorAll('.kcol.is-over').forEach((c) => c.classList.remove('is-over'));
    draggedId = null;
  });

  board.addEventListener('dragover', (e) => {
    const col = e.target.closest('.kcol');
    if (!col) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = 'move';
    col.classList.add('is-over');
  });

  board.addEventListener('dragleave', (e) => {
    const col = e.target.closest('.kcol');
    if (col && !col.contains(e.relatedTarget)) col.classList.remove('is-over');
  });

  board.addEventListener('drop', (e) => {
    const col = e.target.closest('.kcol');
    if (!col) return;
    e.preventDefault();
    col.classList.remove('is-over');
    const id = draggedId || e.dataTransfer.getData('text/plain');
    board.querySelectorAll('.kcard.is-dragging').forEach((c) => c.classList.remove('is-dragging'));
    draggedId = null;
    if (id) moveLead(id, col.dataset.status);
  });
}

/* ============================================================
   16. BOOT
   ============================================================ */

async function boot() {
  applyTheme(readTheme());
  // Guarda o token do convite ANTES de decidir login/app: quem chega por um
  // link de convite sem sessão vai logar/cadastrar primeiro, e o token precisa
  // sobreviver a isso para o aceite acontecer depois de entrar.
  checarConvite();

  api.setUnauthorizedHandler(() => {
    leaveToLogin({ message: 'Sua sessão terminou. Entre novamente.' });
  });

  // 402 `assinatura_necessaria` em QUALQUER chamada abre a tela de planos.
  // O bloqueio de verdade e' do servidor; isto so' troca a tela para algo
  // acionavel em vez de um erro seco.
  api.setPaywallHandler(mostrarPaywall);
  wirePaywall({ onLogout: doLogout });

  // Preenche a caixa vazia do `store.js`. Precisa vir ANTES de qualquer
  // `wire*`, porque os módulos chamam `hooks.refreshData` já no primeiro clique.
  hooks.state = state;
  hooks.refreshData = refreshData;
  hooks.renderCurrentPage = renderCurrentPage;
  hooks.draw = draw;
  hooks.go = (rota) => { location.hash = `#/${rota}`; };

  initAuth({ onAuthenticated: enterApp });
  wireGlobalEvents();
  wireNotify();
  wireProposals();
  wireAutomations();
  wireSettings();
  ligarIntel();
  wireLead({ abrirLead: openLeadModal, abrirProposta, enviarProposta, apagarProposta });
  wireAdmin();
  // Depois de entrar numa equipe, o papel e a organização mudam: recarrega o
  // usuário (revela/esconde menus) e os dados (agora os da nova empresa).
  wireEquipe({
    afterAccept: async () => {
      try {
        const u = await api.me({ silent: true });
        if (u && u.id != null) { state.user = u; paintUser(); }
      } catch { /* mantém o usuário atual */ }
      await refreshData();
      renderCurrentPage();
    },
  });
  initTilt();

  // As DUAS telas (login e app) começam ocultas. Descobrimos a sessão e
  // carregamos a parte pesada (gráficos/WebGL) EM PARALELO; só então revelamos
  // uma delas. Assim quem já está logado NUNCA vê a tela de login piscar ao
  // recarregar -- antes ela aparecia por vir visível no HTML enquanto o
  // `api.me` não respondia.
  const carregandoViz = loadViz();
  let user = null;
  try {
    user = await api.me({ silent: true });
  } catch { user = null; }        // sem sessão: cai no login
  await carregandoViz;            // os gráficos precisam do charts3d p/ desenhar

  if (user && user.id != null) {
    await enterApp(user);
    return;
  }

  try { Scene.mount(document.getElementById('scene-login'), { mode: 'login' }); } catch { /* ignora */ }
  showAuth();
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', boot, { once: true });
} else {
  boot();
}
