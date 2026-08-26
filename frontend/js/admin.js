import * as api from './api.js';
import {
  el, icon, clear, brl, num, initials, toast, openModal, emptyState, emptyRow,
} from './ui.js';

const adminState = {
  overview: null,
  accounts: null,
  interests: null,
  revenue: null,
  security: null,
  saude: null,
  q: '',
  carregado: false,
};

const STATUS_LABEL = {
  gratuito: 'Gratuito',
  ativa: 'Ativa',
  pendente: 'Pendente',
  vencida: 'Vencida',
  cancelada: 'Cancelada',
  trial: 'Em teste',
};
const PLAN_LABEL = { inicial: 'Inicial', pro: 'Pro', empresa: 'Empresa' };

const reais = (centavos) => brl((Number(centavos) || 0) / 100);

function desde(iso) {
  if (!iso) return '—';
  const quando = new Date(iso);
  if (Number.isNaN(quando.getTime())) return '—';
  const dias = Math.floor((Date.now() - quando.getTime()) / 86400000);
  if (dias <= 0) return 'hoje';
  if (dias === 1) return 'ontem';
  if (dias < 30) return `há ${dias} dias`;
  if (dias < 365) return `há ${Math.floor(dias / 30)} meses`;
  return `há ${Math.floor(dias / 365)} ano(s)`;
}

function dataCurta(iso) {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '—';
  return d.toLocaleDateString('pt-BR', { day: '2-digit', month: 'short', year: 'numeric' });
}

export async function loadAdmin() {
  const [ov, accs, inter, rev, sec, sau] = await Promise.allSettled([
    api.adminOverview(),
    api.adminAccounts(adminState.q, { limit: 100 }),
    api.adminPlanInterests(50),
    api.adminRevenue(12),
    api.adminSecurityEvents(50),
    api.adminSaude(),
  ]);
  if (ov.status === 'fulfilled') adminState.overview = ov.value;
  if (accs.status === 'fulfilled') adminState.accounts = accs.value;
  if (inter.status === 'fulfilled') adminState.interests = inter.value;
  if (rev.status === 'fulfilled') adminState.revenue = rev.value;
  if (sec.status === 'fulfilled') adminState.security = sec.value;
  if (sau.status === 'fulfilled') adminState.saude = sau.value;
  adminState.carregado = true;

  const falha = [ov, accs, inter, rev, sec, sau].find((r) => r.status === 'rejected');
  if (falha) toast(falha.reason?.message || 'Falha ao carregar o painel do dono.', 'error');
}

async function loadAccounts() {
  try {
    adminState.accounts = await api.adminAccounts(adminState.q, { limit: 100 });
  } catch (err) {
    toast(err?.message || 'Falha ao buscar contas.', 'error');
  }
  renderAccounts();
}

function admTile({ label, value, iconName, foot, tone }) {
  const inner = [
    el('div', { class: 'kpi__top' }, [
      el('span', { class: 'kpi__label', text: label }),
      el('span', { class: 'kpi__badge' }, [icon(iconName)]),
    ]),
    el('p', { class: 'kpi__value', text: value }),
  ];
  if (foot) inner.push(el('p', { class: 'kpi__foot', text: foot }));
  const cls = `bezel kpi-adm${tone === 'warn' ? ' kpi-adm--warn' : ''}`;
  return el('div', { class: cls }, [el('div', { class: 'bezel__in kpi' }, inner)]);
}

const SAUDE_TXT = { ok: 'Tudo certo', atencao: 'Atenção', critico: 'Crítico' };
const BACKUP_TXT = {
  ok: 'em dia', atrasado: 'atrasado', critico: 'sem rodar',
  sem_backup: 'nenhum backup', desconhecido: 'sem informação',
};

function bytesCurto(n) {
  if (n == null) return '—';
  const u = ['B', 'KB', 'MB', 'GB'];
  let v = Number(n); let i = 0;
  while (v >= 1024 && i < u.length - 1) { v /= 1024; i += 1; }
  return `${v.toFixed(v < 10 && i > 0 ? 1 : 0)} ${u[i]}`;
}

function renderSaude() {
  const host = document.getElementById('admin-saude');
  if (!host) return;
  const s = adminState.saude;
  clear(host);
  if (!s) return;

  const b = s.backup || {};
  const tone = s.estado === 'critico' ? 'crit' : (s.estado === 'atencao' ? 'warn' : 'ok');
  const bTone = b.estado === 'ok' ? 'ok' : (b.estado === 'atrasado' ? 'warn' : 'crit');
  const linhaBackup = b.ultimo_em
    ? `${desde(b.ultimo_em)} · ${bytesCurto(b.tamanho)}`
    : (BACKUP_TXT[b.estado] || '—');

  const grid = el('div', { class: 'saude__grid' }, [
    el('div', { class: 'saude__item' }, [
      el('span', { class: 'saude__lbl', text: 'Último backup' }),
      el('strong', { class: `saude__val saude__val--${bTone}`, text: linhaBackup }),
    ]),
    el('div', { class: 'saude__item' }, [
      el('span', { class: 'saude__lbl', text: 'Contas / Leads' }),
      el('strong', { class: 'saude__val', text: `${num(s.uso?.contas || 0)} / ${num(s.uso?.leads || 0)}` }),
    ]),
    el('div', { class: 'saude__item' }, [
      el('span', { class: 'saude__lbl', text: 'Alertas de segurança (7d)' }),
      el('strong', { class: 'saude__val', text: num(s.uso?.alertas_seguranca_7d || 0) }),
    ]),
  ]);

  const corpo = [
    el('div', { class: 'saude__head' }, [
      el('span', { class: `saude__dot saude__dot--${tone}` }),
      el('strong', { text: `Saúde do sistema — ${SAUDE_TXT[s.estado] || s.estado}` }),
    ]),
    grid,
  ];
  if ((s.problemas || []).length) {
    corpo.push(el('ul', { class: 'saude__probs' }, s.problemas.map((p) => el('li', { text: p }))));
  }
  if ((s.alertas_recentes || []).length) {
    corpo.push(el('details', { class: 'saude__log' }, [
      el('summary', { text: `Ver alertas recentes (${s.alertas_recentes.length})` }),
      el('pre', { class: 'saude__pre', text: s.alertas_recentes.join('\n') }),
    ]));
  }
  host.append(el('div', { class: `bezel saude saude--${tone}` }, [el('div', { class: 'bezel__in' }, corpo)]));
}

function renderKpis() {
  const host = document.getElementById('admin-kpis');
  if (!host) return;
  const o = adminState.overview;
  clear(host);
  if (!o) { host.append(el('p', { class: 'kpi__foot', text: 'Carregando…' })); return; }

  host.append(
    admTile({
      label: 'Receita recorrente (MRR)', value: reais(o.mrr_centavos), iconName: 'wallet',
      foot: `${num(o.pagantes)} conta(s) pagante(s)`,
    }),
    admTile({
      label: 'Receita no mês', value: reais(o.receita_mes_centavos), iconName: 'spark',
      foot: `Total já recebido: ${reais(o.receita_total_centavos)}`,
    }),
    admTile({
      label: 'Contas', value: num(o.total_contas), iconName: 'building',
      foot: `${num(o.ativas_30d)} ativas em 30 dias`,
    }),
    admTile({
      label: 'Novas contas (30d)', value: num(o.novas_30d), iconName: 'users',
      foot: o.em_trial > 0 ? `${num(o.em_trial)} em teste agora` : 'Nenhuma em teste',
    }),
    admTile({
      label: 'Leads na plataforma', value: num(o.total_leads), iconName: 'target',
    }),
    admTile({
      label: 'Pedidos de plano', value: num(o.pedidos_plano), iconName: 'mail',
      foot: `${num(o.pedidos_plano_30d)} nos últimos 30 dias`,
    }),
    admTile({
      label: 'Uso de IA (30d)', value: num(o.ia_chamadas_30d), iconName: 'bolt',
      foot: `${num(o.ia_tokens_30d)} tokens`,
    }),
    admTile({
      label: 'Alertas de segurança (7d)', value: num(o.alertas_seguranca_7d || 0), iconName: 'shield',
      foot: (o.alertas_seguranca_7d || 0) > 0 ? 'Alguém fuçou o sistema' : 'Tudo tranquilo',
      tone: (o.alertas_seguranca_7d || 0) > 0 ? 'warn' : undefined,
    }),
  );
}

function barList(host, linhas, { fmt = num } = {}) {
  clear(host);
  const max = Math.max(1, ...linhas.map((l) => Number(l.valor) || 0));
  if (!linhas.length) {
    host.append(el('p', { class: 'kpi__foot', text: 'Sem dados ainda.' }));
    return;
  }
  const wrap = el('div', { class: 'revbars' });
  for (const l of linhas) {
    const v = Number(l.valor) || 0;
    const w = Math.max(2, Math.round((v / max) * 100));
    wrap.append(el('div', { class: 'revbar' }, [
      el('span', { class: 'revbar__label', text: l.label }),
      el('span', { class: 'revbar__track' }, [
        el('span', { class: 'revbar__fill', attrs: { style: `width:${w}%` } }),
      ]),
      el('span', { class: 'revbar__val', text: fmt(v) }),
    ]));
  }
  host.append(wrap);
}

function renderRevenue() {
  const host = document.getElementById('admin-revenue');
  const chip = document.getElementById('admin-rev-chip');
  if (!host) return;
  const pts = adminState.revenue?.points || [];
  if (chip) chip.textContent = pts.length ? `Total: ${reais(adminState.revenue.total_centavos)}` : 'Faturas pagas';
  if (!pts.length) {
    clear(host);
    host.append(emptyState({
      title: 'Sem receita registrada',
      text: 'Quando uma fatura for paga, o mês aparece aqui.',
      iconName: 'wallet',
    }));
    return;
  }
  barList(host, pts.map((p) => ({ label: p.mes, valor: p.centavos })), { fmt: reais });
}

function renderStatus() {
  const host = document.getElementById('admin-status');
  if (!host) return;
  const porStatus = adminState.overview?.por_status || {};
  const linhas = Object.entries(porStatus)
    .map(([k, v]) => ({ label: STATUS_LABEL[k] || k, valor: v }))
    .sort((a, b) => b.valor - a.valor);
  barList(host, linhas);
}

function contaRow(a) {
  const nomeCell = el('td', {}, [
    el('div', { class: 'admin-acct' }, [
      el('span', { class: 'avatar avatar--sm', text: initials(a.name) }),
      el('div', { class: 'admin-acct__txt' }, [
        el('strong', {}, [
          document.createTextNode(a.name || '—'),
          a.is_owner ? el('span', { class: 'ownertag', text: 'você' }) : null,
        ]),
        el('small', { text: a.email }),
      ]),
    ]),
  ]);

  const planoCell = el('td', {}, [
    el('span', { class: `pill pill--${a.plano}`, text: PLAN_LABEL[a.plano] || a.plano }),
    a.status && a.status !== 'gratuito'
      ? el('span', { class: 'admin-substatus', text: STATUS_LABEL[a.status] || a.status })
      : null,
  ]);

  return el('tr', { class: 'admin-row', attrs: { 'data-admin-open': a.id } }, [
    nomeCell,
    planoCell,
    el('td', { class: 'num', text: num(a.n_leads) }),
    el('td', { class: 'num', text: brl(a.pipeline) }),
    el('td', { text: desde(a.ultimo_visto) }),
    el('td', { class: 'acts' }, [
      el('button', {
        class: 'btn btn--quiet btn--xs', attrs: { type: 'button', 'data-admin-open': a.id },
      }, [icon('eye'), el('span', { class: 'btn__txt', text: 'Ver' })]),
    ]),
  ]);
}

function renderAccounts() {
  const body = document.getElementById('admin-accounts');
  if (!body) return;
  clear(body);
  const items = adminState.accounts?.items || [];
  if (!items.length) {
    body.append(emptyRow(6, adminState.q ? 'Nenhuma conta encontrada.' : 'Nenhuma conta ainda.'));
    return;
  }
  for (const a of items) body.append(contaRow(a));
}

function renderInterests() {
  const host = document.getElementById('admin-interests');
  const chip = document.getElementById('admin-interests-chip');
  if (!host) return;
  const items = adminState.interests?.items || [];
  if (chip) chip.textContent = `${num(adminState.interests?.total || 0)} no total`;
  clear(host);
  if (!items.length) {
    host.append(emptyState({
      title: 'Nenhum pedido ainda',
      text: 'Quando alguém pedir o Pro ou o Empresa, aparece aqui com o contato.',
      iconName: 'mail',
    }));
    return;
  }
  const lista = el('ul', { class: 'admin-interests' });
  for (const p of items) {
    lista.append(el('li', { class: 'admin-interest' }, [
      el('span', { class: `pill pill--${p.plan}`, text: PLAN_LABEL[p.plan] || p.plan }),
      el('div', { class: 'admin-interest__txt' }, [
        el('strong', { text: p.name || p.email }),
        el('small', { text: [p.company, p.email, p.phone].filter(Boolean).join(' · ') }),
        p.message ? el('p', { class: 'admin-interest__msg', text: p.message }) : null,
      ]),
      el('span', { class: 'admin-interest__when', text: dataCurta(p.created_at) }),
    ]));
  }
  host.append(lista);
}

const EVENTO_LABEL = {
  decoy_path: 'Acesso a caminho-isca',
  honeytoken: 'Uso de chave-isca',
};

function renderSecurity() {
  const host = document.getElementById('admin-security');
  const chip = document.getElementById('admin-sec-chip');
  if (!host) return;
  const items = adminState.security?.items || [];
  const total = adminState.security?.total || 0;
  if (chip) chip.textContent = total > 0 ? `${num(total)} no total` : 'Nenhum';
  clear(host);
  if (!items.length) {
    host.append(emptyState({
      title: 'Nenhuma isca acionada',
      text: 'É exatamente o que se espera. Se alguém começar a fuçar o sistema, aparece aqui.',
      iconName: 'shield',
    }));
    return;
  }
  const lista = el('ul', { class: 'admin-events' });
  for (const e of items) {
    lista.append(el('li', { class: 'admin-event' }, [
      el('span', { class: 'admin-event__ico' }, [icon('shield')]),
      el('div', { class: 'admin-event__txt' }, [
        el('strong', { text: EVENTO_LABEL[e.kind] || e.kind }),
        el('small', { text: `${e.path || e.detail || ''} · IP ${e.ip || '—'}` }),
        e.user_agent ? el('small', { class: 'admin-event__ua', text: e.user_agent }) : null,
      ]),
      el('span', { class: 'admin-event__when', text: dataCurta(e.created_at) }),
    ]));
  }
  host.append(lista);
}

export function renderAdmin() {
  renderSaude();
  renderKpis();
  renderRevenue();
  renderStatus();
  renderAccounts();
  renderInterests();
  renderSecurity();
}

function statTile(label, value) {
  return el('div', { class: 'admin-stat' }, [
    el('span', { class: 'admin-stat__label', text: label }),
    el('strong', { class: 'admin-stat__value', text: value }),
  ]);
}

function renderDetail(d) {
  const body = document.getElementById('admin-modal-body');
  const titulo = document.getElementById('admin-modal-title');
  if (!body) return;
  if (titulo) titulo.textContent = d.name || 'Conta';
  clear(body);

  body.append(el('div', { class: 'admin-dethead' }, [
    el('span', { class: 'avatar avatar--lg', text: initials(d.name) }),
    el('div', {}, [
      el('h3', {}, [
        document.createTextNode(d.name || '—'),
        d.is_owner ? el('span', { class: 'ownertag', text: 'você' }) : null,
      ]),
      el('small', { class: 'admin-detmail', text: d.email }),
      el('div', { class: 'admin-detbadges' }, [
        el('span', { class: `pill pill--${d.plano}`, text: PLAN_LABEL[d.plano] || d.plano }),
        el('span', { class: 'admin-substatus', text: STATUS_LABEL[d.status] || d.status }),
        el('span', { class: 'admin-substatus', text: d.auth_provider === 'google' ? 'Login Google' : 'Login por senha' }),
        d.email_verified ? null : el('span', { class: 'admin-substatus admin-substatus--warn', text: 'E-mail não verificado' }),
      ]),
    ]),
  ]));

  const grid = el('div', { class: 'admin-stats' });
  grid.append(
    statTile('Criada em', dataCurta(d.created_at)),
    statTile('Última atividade', desde(d.ultimo_visto)),
    statTile('Leads', num(d.n_leads)),
    statTile('Pipeline aberto', brl(d.pipeline)),
    statTile('Total ganho', brl(d.ganho_total)),
    statTile('Atividades (30d)', num(d.atividades_30d)),
    statTile('Chamadas de IA', num(d.ia_chamadas)),
    statTile('Tokens de IA', num(d.ia_tokens)),
  );
  if (d.vigente && d.current_period_end) {
    grid.append(statTile('Pago até', dataCurta(d.current_period_end)));
  }
  body.append(grid);

  const funil = (d.por_status || []).filter((s) => s.total > 0);
  if (funil.length) {
    body.append(el('h4', { class: 'admin-subtitle', text: 'Funil desta conta' }));
    const wrap = el('div', {});
    barList(wrap, funil.map((s) => ({ label: `${s.status} (${s.total})`, valor: s.valor })), { fmt: brl });
    body.append(wrap);
  }

  body.append(el('h4', { class: 'admin-subtitle', text: 'Faturas' }));
  if (!d.faturas?.length) {
    body.append(el('p', { class: 'kpi__foot', text: 'Nenhuma fatura registrada.' }));
  } else {
    const tabela = el('div', { class: 'tablewrap' }, [
      el('table', { class: 'table' }, [
        el('thead', {}, [el('tr', {}, [
          el('th', { attrs: { scope: 'col' }, text: 'Data' }),
          el('th', { attrs: { scope: 'col' }, text: 'Plano' }),
          el('th', { class: 'num', attrs: { scope: 'col' }, text: 'Valor' }),
          el('th', { attrs: { scope: 'col' }, text: 'Situação' }),
        ])]),
        el('tbody', {}, d.faturas.map((f) => el('tr', {}, [
          el('td', { text: dataCurta(f.paid_at || f.created_at) }),
          el('td', { text: PLAN_LABEL[f.plan] || f.plan || '—' }),
          el('td', { class: 'num', text: reais(f.centavos) }),
          el('td', { text: f.paid_at ? 'Paga' : (f.status || '—') }),
        ]))),
      ]),
    ]);
    body.append(tabela);
  }
}

async function abrirDetalhe(id) {
  const modal = document.getElementById('admin-modal');
  const body = document.getElementById('admin-modal-body');
  if (!modal || !body) return;
  clear(body);
  body.append(el('p', { class: 'kpi__foot', text: 'Carregando…' }));
  openModal(modal);
  try {
    const d = await api.adminAccount(id);
    renderDetail(d);
  } catch (err) {
    clear(body);
    body.append(el('p', { class: 'formerr', text: err?.message || 'Não foi possível abrir a conta.' }));
  }
}

let buscaTimer = null;

export function wireAdmin() {
  document.getElementById('admin-refresh')?.addEventListener('click', async () => {
    await loadAdmin();
    renderAdmin();
  });

  const busca = document.getElementById('admin-q');
  busca?.addEventListener('input', () => {
    adminState.q = busca.value.trim();
    clearTimeout(buscaTimer);
    buscaTimer = setTimeout(loadAccounts, 250);
  });

  document.getElementById('admin-accounts')?.addEventListener('click', (e) => {
    const alvo = e.target.closest('[data-admin-open]');
    if (alvo) abrirDetalhe(alvo.dataset.adminOpen);
  });
}
