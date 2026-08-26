import * as api from './api.js';
import {
  el, icon, clear, toast, initials, openModal, closeModal, confirmar, vselect,
} from './ui.js';

const ROLE_LABEL = { admin: 'Admin', gestor: 'Gestor', vendedor: 'Vendedor' };
const ROLE_DESC = {
  admin: 'Controle total, inclusive cobrança',
  gestor: 'Vê tudo e gerencia a equipe',
  vendedor: 'Vê só os leads dele + os sem dono',
};

const INVITE_OPTS = ['vendedor', 'gestor', 'admin'].map((r) => ({
  value: r, label: ROLE_LABEL[r], hint: ROLE_DESC[r],
}));

let inviteSelect = null;

const equipeState = {
  org: null,
  invites: [],
  audit: [],
};

let pendingInvite = null;
let afterAcceptCb = null;

export async function loadEquipe() {
  try {
    equipeState.org = await api.org();
  } catch (e) {
    toast(e?.message || 'Falha ao carregar a equipe.', 'error');
    return;
  }
  if (equipeState.org.can_manage_team) {
    try { equipeState.invites = (await api.listInvites()).items || []; }
    catch { equipeState.invites = []; }
    try { equipeState.audit = (await api.orgAudit()).items || []; }
    catch { equipeState.audit = []; }
  } else {
    equipeState.invites = [];
    equipeState.audit = [];
  }
}

function tempoRelativo(iso) {
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return '';
  const seg = Math.max(0, Math.floor((Date.now() - t) / 1000));
  if (seg < 60) return 'agora';
  const min = Math.floor(seg / 60);
  if (min < 60) return `há ${min} min`;
  const h = Math.floor(min / 60);
  if (h < 24) return `há ${h} h`;
  const d = Math.floor(h / 24);
  if (d < 7) return `há ${d} d`;
  return new Date(t).toLocaleDateString('pt-BR', { day: '2-digit', month: 'short' });
}

function auditRow(ev) {
  return el('div', { class: 'auditlog__row' }, [
    el('span', { class: 'auditlog__txt', text: ev.texto || '' }),
    el('time', { class: 'auditlog__when', text: tempoRelativo(ev.created_at) }),
  ]);
}

function avatarMembro(m) {
  const node = el('span', { class: 'avatar avatar--sm', text: initials(m.name) });
  const url = api.avatarUrl(m.user_id, m.avatar);
  if (url) {
    node.style.backgroundImage = `url("${url}")`;
    node.classList.add('avatar--foto');
  }
  return node;
}

function memberRow(m, org) {
  const meu = org.my_role;
  const ehDono = m.user_id === org.owner_user_id || (m.is_me && org.is_account_owner);
  const controlavel = org.can_manage_team && !m.is_me && !ehDono
    && !(m.role === 'admin' && meu !== 'admin');

  const info = el('div', { class: 'admin-acct' }, [
    avatarMembro(m),
    el('div', { class: 'admin-acct__txt' }, [
      el('strong', {}, [
        document.createTextNode(m.name || '—'),
        m.is_me ? el('span', { class: 'ownertag', text: 'você' }) : null,
        ehDono ? el('span', { class: 'ownertag ownertag--dono', text: 'dono' }) : null,
      ]),
      el('small', { text: m.email }),
    ]),
  ]);

  let acao;
  if (controlavel) {

    const papeis = meu === 'admin' ? ['admin', 'gestor', 'vendedor'] : ['gestor', 'vendedor'];
    const select = vselect({
      size: 'sm',
      ariaLabel: `Papel de ${m.name}`,
      value: m.role,
      options: papeis.map((p) => ({ value: p, label: ROLE_LABEL[p], hint: ROLE_DESC[p] })),
      onChange: (v) => mudarPapel(m.user_id, v),
    });
    acao = el('div', { class: 'member__acts' }, [
      select.node,
      el('button', {
        class: 'iconbtn iconbtn--danger', attrs: { type: 'button', title: 'Remover da equipe' },
        on: { click: () => removerMembro(m) },
      }, [icon('trash')]),
    ]);
  } else {
    acao = el('span', { class: `pill pill--${m.role}`, text: ROLE_LABEL[m.role] || m.role });
  }

  return el('div', { class: 'member' }, [info, acao]);
}

function inviteRow(inv) {
  return el('div', { class: 'invite' }, [
    el('div', { class: 'invite__txt' }, [
      el('strong', { text: `Convite de ${ROLE_LABEL[inv.role] || inv.role}` }),
      el('small', { text: inv.email ? `Para ${inv.email}` : 'Link aberto — vale para uma pessoa' }),
    ]),
    el('button', {
      class: 'btn btn--quiet btn--xs', attrs: { type: 'button' },
      on: { click: () => revogar(inv.id) },
    }, [el('span', { text: 'Revogar' })]),
  ]);
}

export function renderEquipe() {
  const org = equipeState.org;
  if (!org) return;

  const sub = document.getElementById('equipe-sub');
  if (sub) sub.textContent = `${org.name} · você é ${ROLE_LABEL[org.my_role] || org.my_role}`;

  const mount = document.getElementById('invite-role-mount');
  if (mount) {
    const permitidas = org.my_role === 'admin'
      ? INVITE_OPTS
      : INVITE_OPTS.filter((o) => o.value !== 'admin');
    if (!inviteSelect) {
      inviteSelect = vselect({ options: permitidas, value: 'vendedor', ariaLabel: 'Papel do convidado' });
      mount.append(inviteSelect.node);
    } else {
      const atual = inviteSelect.value;
      inviteSelect.setOptions(permitidas);
      if (permitidas.some((o) => o.value === atual)) inviteSelect.value = atual;
    }
  }

  const count = document.getElementById('equipe-count');
  if (count) count.textContent = `${org.members.length} pessoa(s)`;

  const lista = document.getElementById('member-list');
  if (lista) {
    clear(lista);
    for (const m of org.members) lista.append(memberRow(m, org));
  }

  const invBox = document.getElementById('invite-list');
  if (invBox) {
    clear(invBox);
    if (!equipeState.invites.length) {
      invBox.append(el('p', { class: 'kpi__foot', text: 'Nenhum convite pendente.' }));
    } else {
      for (const inv of equipeState.invites) invBox.append(inviteRow(inv));
    }
  }

  const auditBox = document.getElementById('audit-list');
  if (auditBox) {
    clear(auditBox);
    if (!equipeState.audit.length) {
      auditBox.append(el('p', { class: 'auditlog__empty', text: 'Nada por aqui ainda. As ações da equipe aparecem assim que acontecem.' }));
    } else {
      for (const ev of equipeState.audit) auditBox.append(auditRow(ev));
    }
  }
}

async function gerarConvite() {
  const err = document.getElementById('invite-error');
  if (err) err.hidden = true;
  const role = (inviteSelect && inviteSelect.value) || 'vendedor';
  try {
    const r = await api.createInvite(role);
    const box = document.getElementById('invite-fresh');
    const input = document.getElementById('invite-link');
    if (input) input.value = r.link;
    if (box) box.hidden = false;
    equipeState.invites = (await api.listInvites()).items || [];
    renderEquipe();
    toast('Convite gerado. Copie o link agora.', 'success');
  } catch (e) {
    if (err) { err.textContent = e.message || 'Não foi possível gerar o convite.'; err.hidden = false; }
  }
}

async function revogar(id) {
  try {
    await api.revokeInvite(id);
    equipeState.invites = (await api.listInvites()).items || [];
    renderEquipe();
  } catch (e) {
    toast(e.message || 'Não foi possível revogar.', 'error');
  }
}

async function mudarPapel(userId, role) {
  try {
    equipeState.org = await api.changeMemberRole(userId, role);
    renderEquipe();
    toast('Papel atualizado.', 'success');
  } catch (e) {
    toast(e.message || 'Não foi possível mudar o papel.', 'error');

    await loadEquipe();
    renderEquipe();
  }
}

async function removerMembro(m) {
  const ok = await confirmar({
    titulo: 'Remover da equipe',
    texto: `Remover ${m.name} da equipe? Os leads dele voltam a ficar “sem dono”, e a pessoa cai numa conta própria vazia.`,
    alvo: { nome: m.name, meta: ROLE_LABEL[m.role] || m.role },
    aviso: 'A pessoa perde o acesso a esta equipe.',
    confirmar: 'Remover',
  });
  if (!ok) return;
  try {
    equipeState.org = await api.removeMember(m.user_id);
    renderEquipe();
    toast(`${m.name} foi removido da equipe.`, 'info');
  } catch (e) {
    toast(e.message || 'Não foi possível remover.', 'error');
  }
}

function copiarLink() {
  const input = document.getElementById('invite-link');
  if (!input || !input.value) return;
  const done = () => toast('Link copiado.', 'success');
  if (navigator.clipboard?.writeText) {
    navigator.clipboard.writeText(input.value).then(done).catch(() => { input.select(); });
  } else {
    input.select();
    try { document.execCommand('copy'); done(); } catch {  }
  }
}

export function setPendingInvite(token) { pendingInvite = token || null; }
export function temConvitePendente() { return !!pendingInvite; }

export function abrirConviteSePendente() {
  if (!pendingInvite) return;
  const err = document.getElementById('convite-modal-error');
  if (err) err.hidden = true;
  openModal(document.getElementById('convite-modal'), { focus: '#convite-accept' });
}

async function aceitar() {
  if (!pendingInvite) return;
  const err = document.getElementById('convite-modal-error');
  try {
    const r = await api.acceptInvite(pendingInvite);
    pendingInvite = null;
    closeModal();
    toast(`Você entrou na equipe ${r.org_name} como ${ROLE_LABEL[r.role] || r.role}.`, 'success');
    if (location.hash.startsWith('#/convite')) location.hash = '#/dashboard';
    if (afterAcceptCb) await afterAcceptCb();
  } catch (e) {
    if (err) { err.textContent = e.message || 'Não foi possível aceitar o convite.'; err.hidden = false; }
  }
}

export function wireEquipe({ afterAccept } = {}) {
  afterAcceptCb = afterAccept || null;

  document.getElementById('invite-form')?.addEventListener('submit', (e) => {
    e.preventDefault();
    gerarConvite();
  });
  document.getElementById('invite-copy')?.addEventListener('click', copiarLink);
  document.getElementById('convite-accept')?.addEventListener('click', aceitar);
}
