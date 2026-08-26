import * as api from './api.js';
import { el, icon, clear, toast, emptyState } from './ui.js';

const state = { contatos: [], campanhas: [] };

const CONSENT_LABEL = { subscribed: 'Inscrito', unsubscribed: 'Descadastrado', pending: 'Sem opt-in' };

export async function loadMarketing() {
  const [ct, cp] = await Promise.allSettled([api.mktContatos(), api.mktCampaigns()]);
  state.contatos = ct.status === 'fulfilled' ? (ct.value.items || []) : [];
  state.campanhas = cp.status === 'fulfilled' ? (cp.value.items || []) : [];
  const falha = [ct, cp].find((r) => r.status === 'rejected');
  if (falha) toast(falha.reason?.message || 'Falha ao carregar o marketing.', 'error');
}

function contatoRow(c) {
  const suprimido = c.suprimido === 1;
  const inscrito = c.consent === 'subscribed' && !suprimido;
  const rotulo = suprimido ? 'Suprimido' : (CONSENT_LABEL[c.consent] || c.consent);
  const acao = el('button', {
    class: `btn btn--sm ${inscrito ? 'btn--ghost' : 'btn--primary'}`,
    text: inscrito ? 'Descadastrar' : 'Inscrever',
    attrs: { 'data-email': c.email, 'data-acao': inscrito ? 'unsub' : 'sub' },
  });
  return el('tr', {}, [
    el('td', {}, [
      el('strong', { text: c.name || '—' }),
      el('small', { class: 'mkt-mail', text: c.email }),
    ]),
    el('td', { text: c.company || '—' }),
    el('td', {}, [el('span', { class: `mkt-badge mkt-badge--${inscrito ? 'ok' : 'off'}`, text: rotulo })]),
    el('td', { class: 'mkt-td-acao' }, [acao]),
  ]);
}

function renderContatos() {
  const host = document.getElementById('mkt-contatos');
  const chip = document.getElementById('mkt-contatos-chip');
  if (!host) return;
  const subs = state.contatos.filter((c) => c.consent === 'subscribed' && c.suprimido !== 1).length;
  if (chip) chip.textContent = `${subs} inscrito(s) de ${state.contatos.length}`;
  clear(host);
  if (!state.contatos.length) {
    host.append(emptyState({
      title: 'Nenhum contato com e-mail',
      text: 'Cadastre clientes com e-mail no CRM. Depois marque quem deu consentimento para receber campanhas.',
      iconName: 'users',
    }));
    return;
  }
  const tabela = el('table', { class: 'mkt-table' }, [
    el('thead', {}, [el('tr', {}, [
      el('th', { text: 'Contato' }), el('th', { text: 'Empresa' }),
      el('th', { text: 'Consentimento' }), el('th', { text: '' }),
    ])]),
    el('tbody', { attrs: { id: 'mkt-contatos-body' } }, state.contatos.map(contatoRow)),
  ]);
  host.append(tabela);
}

async function alternarConsent(email, acao) {
  try {
    await api.mktConsent(email, acao === 'sub' ? 'subscribed' : 'unsubscribed');
    const c = state.contatos.find((x) => x.email === email);
    if (c) { c.consent = acao === 'sub' ? 'subscribed' : 'unsubscribed'; if (acao === 'sub') c.suprimido = 0; }
    renderContatos();
  } catch (e) {
    toast(e?.message || 'Não deu para atualizar o consentimento.', 'error');
  }
}

const STATUS_CAMP = {
  draft: 'Rascunho', queued: 'Na fila', sending: 'Enviando', sent: 'Enviada',
  paused: 'Pausada', cancelled: 'Cancelada', failed: 'Falhou', scheduled: 'Agendada',
};

function campanhaRow(c) {
  const acoes = el('div', { class: 'mkt-acoes' });
  if (c.status === 'draft') {
    acoes.append(
      el('button', { class: 'btn btn--sm btn--ghost', text: 'Teste', attrs: { 'data-teste': c.id } }),
      el('button', { class: 'btn btn--sm btn--primary', text: 'Enviar', attrs: { 'data-enviar': c.id } }),
    );
  }
  return el('tr', {}, [
    el('td', {}, [el('strong', { text: c.name }), el('small', { class: 'mkt-mail', text: c.subject || '(sem assunto)' })]),
    el('td', {}, [el('span', { class: `mkt-badge mkt-badge--${c.status === 'sent' ? 'ok' : 'off'}`, text: STATUS_CAMP[c.status] || c.status })]),
    el('td', { text: String(c.total_dest || 0) }),
    el('td', { class: 'mkt-td-acao' }, [acoes]),
  ]);
}

function renderCampanhas() {
  const host = document.getElementById('mkt-campanhas');
  if (!host) return;
  clear(host);
  if (!state.campanhas.length) {
    host.append(emptyState({
      title: 'Nenhuma campanha ainda',
      text: 'Crie uma campanha acima, faça um envio de teste para você mesmo e, quando estiver pronta, dispare para os contatos inscritos.',
      iconName: 'mail',
    }));
    return;
  }
  host.append(el('table', { class: 'mkt-table' }, [
    el('thead', {}, [el('tr', {}, [
      el('th', { text: 'Campanha' }), el('th', { text: 'Status' }),
      el('th', { text: 'Destinatários' }), el('th', { text: '' }),
    ])]),
    el('tbody', {}, state.campanhas.map(campanhaRow)),
  ]));
}

async function criarCampanha() {
  const nome = document.getElementById('mkt-nome');
  const assunto = document.getElementById('mkt-assunto');
  const corpo = document.getElementById('mkt-corpo');
  if (!nome || !nome.value.trim()) { toast('Dê um nome à campanha.', 'error'); return; }
  try {
    await api.mktCreateCampaign({
      name: nome.value.trim(),
      subject: (assunto?.value || '').trim(),
      body_html: (corpo?.value || '').trim(),
    });
    nome.value = ''; if (assunto) assunto.value = ''; if (corpo) corpo.value = '';
    toast('Campanha criada como rascunho.', 'success');
    await loadMarketing();
    renderCampanhas();
  } catch (e) {
    toast(e?.message || 'Não deu para criar a campanha.', 'error');
  }
}

async function enviarTeste(id) {
  try {
    const r = await api.mktTest(id);
    toast(`E-mail de teste enviado para ${r.enviado_para}.`, 'success');
  } catch (e) {
    toast(e?.message || 'Falha no envio de teste.', 'error');
  }
}

async function enviarCampanha(id) {
  let n = 0;
  try { n = (await api.mktPreview(id)).elegiveis; } catch {  }
  if (!n) { toast('Nenhum contato inscrito bate com esta campanha.', 'error'); return; }

  if (!window.confirm(`Enviar esta campanha para ${n} contato(s) inscrito(s)?`)) return;
  try {
    const r = await api.mktSend(id);
    toast(`Campanha enfileirada para ${r.enfileirados} contato(s). O envio começa em instantes.`, 'success');
    await loadMarketing();
    renderCampanhas();
  } catch (e) {
    toast(e?.message || 'Não deu para disparar a campanha.', 'error');
  }
}

export function renderMarketing() {
  renderContatos();
  renderCampanhas();
}

export function wireMarketing() {
  const criar = document.getElementById('mkt-criar');
  if (criar && !criar.dataset.wired) {
    criar.dataset.wired = '1';
    criar.addEventListener('click', criarCampanha);
  }
  const page = document.getElementById('page-marketing');
  if (page && !page.dataset.wired) {
    page.dataset.wired = '1';
    page.addEventListener('click', (ev) => {
      const btn = ev.target.closest('button');
      if (!btn) return;
      if (btn.dataset.acao) alternarConsent(btn.dataset.email, btn.dataset.acao);
      else if (btn.dataset.teste) enviarTeste(btn.dataset.teste);
      else if (btn.dataset.enviar) enviarCampanha(btn.dataset.enviar);
    });
  }
}
