import * as api from './api.js';
import { el, icon, clear, toast, emptyState } from './ui.js';
import { store, hooks, quando, relativo } from './store.js';

const SEV_ICONE = { info: 'info', alerta: 'alert', sucesso: 'check-circle', erro: 'alert' };

function destino(notificacao) {
  if (notificacao.ref_type === 'lead' && notificacao.ref_id) return `#/lead/${notificacao.ref_id}`;
  if (notificacao.ref_type === 'proposal') return '#/propostas';
  if (notificacao.ref_type === 'automation') return '#/automacoes';
  return '#/dashboard';
}

export async function loadNotifications() {
  try {
    store.notifications = await api.notifications();
  } catch (err) {
    if (err?.status !== 401) store.notifications = { unread: 0, items: [] };
  }
}

export function renderNotifications() {
  const contador = document.getElementById('notif-count');
  const lista = document.getElementById('notif-list');
  if (!contador || !lista) return;

  const { unread, items } = store.notifications;
  contador.textContent = unread > 99 ? '99+' : String(unread);
  contador.hidden = unread === 0;
  document.getElementById('notif-btn')?.setAttribute(
    'aria-label', unread ? `Notificações: ${unread} ${unread === 1 ? 'não lida' : 'não lidas'}` : 'Notificações',
  );

  clear(lista);
  if (!items.length) {
    lista.append(emptyState({
      title: 'Nada por aqui',
      text: 'Avisos sobre propostas, tarefas e negócios aparecem aqui.',
      iconName: 'bell',
    }));
    return;
  }

  for (const item of items) {
    lista.append(el('a', {
      class: `notif notif--${item.severity}${item.read_at ? '' : ' is-unread'}`,
      attrs: { href: destino(item), 'data-notif': item.id },
    }, [
      el('span', { class: 'notif__ico' }, [icon(SEV_ICONE[item.severity] || 'info')]),
      el('span', { class: 'notif__txt' }, [
        el('strong', { text: item.title }),
        item.body ? el('span', { text: item.body }) : null,
        el('small', { text: relativo(item.created_at) }),
      ].filter(Boolean)),
    ]));
  }
}

function abrirPainel(abrir) {
  const painel = document.getElementById('notif-panel');
  const botao = document.getElementById('notif-btn');
  if (!painel || !botao) return;
  painel.hidden = !abrir;
  botao.setAttribute('aria-expanded', String(abrir));
}

const ICONE_GRUPO = { leads: 'users', propostas: 'doc', atividades: 'clock', campos: 'sliders' };

let temporizador = null;
let ultimaBusca = '';
let itensVisiveis = [];
let selecionado = -1;

function fecharResultados() {
  const caixa = document.getElementById('search-results');
  if (!caixa) return;
  caixa.hidden = true;
  document.getElementById('global-search')?.setAttribute('aria-expanded', 'false');
  itensVisiveis = [];
  selecionado = -1;
}

function marcarSelecionado() {
  itensVisiveis.forEach((no, indice) => {
    no.classList.toggle('is-sel', indice === selecionado);
    no.setAttribute('aria-selected', String(indice === selecionado));
  });
  const ativo = itensVisiveis[selecionado];
  document.getElementById('global-search')?.setAttribute('aria-activedescendant', ativo?.id || '');
  ativo?.scrollIntoView({ block: 'nearest' });
}

function renderResultados(dados) {
  const caixa = document.getElementById('search-results');
  const dentro = document.getElementById('search-results-in');
  if (!caixa || !dentro) return;
  clear(dentro);
  itensVisiveis = [];
  selecionado = -1;

  if (!dados.groups.length) {
    dentro.append(el('p', { class: 'results__empty', text: `Nada encontrado para “${dados.query}”.` }));
    caixa.hidden = false;
    document.getElementById('global-search')?.setAttribute('aria-expanded', 'true');
    return;
  }

  let indice = 0;
  for (const grupo of dados.groups) {
    dentro.append(el('p', { class: 'results__group', attrs: { role: 'presentation' } }, [
      icon(ICONE_GRUPO[grupo.kind] || 'search'),
      el('span', { text: grupo.label }),
    ]));
    for (const item of grupo.items) {
      const no = el('a', {
        class: 'result',
        attrs: { href: item.route, role: 'option', id: `res-${indice}`, 'aria-selected': 'false' },
      }, [
        el('span', { class: 'result__txt' }, [
          el('strong', { text: item.title }),
          item.subtitle ? el('span', { text: item.subtitle }) : null,
        ].filter(Boolean)),
        el('span', { class: 'result__meta', text: item.meta }),
      ]);
      dentro.append(no);
      itensVisiveis.push(no);
      indice += 1;
    }
  }
  caixa.hidden = false;
  document.getElementById('global-search')?.setAttribute('aria-expanded', 'true');
}

async function buscar(termo) {
  if (termo.length < 2) { fecharResultados(); return; }
  if (termo === ultimaBusca) return;
  ultimaBusca = termo;
  try {
    const dados = await api.search(termo);

    if (document.getElementById('global-search')?.value.trim() !== termo) return;
    renderResultados(dados);
  } catch (err) {
    if (err?.status !== 401) fecharResultados();
  }
}

export function wireNotify() {
  const botao = document.getElementById('notif-btn');
  const painel = document.getElementById('notif-panel');
  const campo = document.getElementById('global-search');
  const caixa = document.getElementById('search-results');

  botao?.addEventListener('click', async () => {
    const abrindo = painel.hidden;
    abrirPainel(abrindo);
    if (abrindo) {
      await loadNotifications();
      renderNotifications();
    }
  });

  document.getElementById('notif-readall')?.addEventListener('click', async () => {
    try {
      store.notifications = await api.readAllNotifications();
      renderNotifications();
    } catch (err) {
      if (err?.status !== 401) toast(err?.message || 'Não foi possível marcar como lidas.', 'error');
    }
  });

  painel?.addEventListener('click', async (evento) => {
    const alvo = evento.target.closest('[data-notif]');
    if (!alvo) return;
    abrirPainel(false);

    api.readNotification(alvo.dataset.notif)
      .then((dados) => { store.notifications = dados; renderNotifications(); })
      .catch(() => {  });
  });

  campo?.addEventListener('input', () => {
    const termo = campo.value.trim();
    document.getElementById('search-clear').hidden = !termo;
    clearTimeout(temporizador);

    temporizador = setTimeout(() => buscar(termo), 220);
  });

  campo?.addEventListener('keydown', (evento) => {
    if (evento.key === 'Escape') { fecharResultados(); campo.blur(); return; }
    if (!itensVisiveis.length) return;
    if (evento.key === 'ArrowDown') {
      evento.preventDefault();
      selecionado = (selecionado + 1) % itensVisiveis.length;
      marcarSelecionado();
    } else if (evento.key === 'ArrowUp') {
      evento.preventDefault();
      selecionado = (selecionado - 1 + itensVisiveis.length) % itensVisiveis.length;
      marcarSelecionado();
    } else if (evento.key === 'Enter' && selecionado >= 0) {
      evento.preventDefault();
      itensVisiveis[selecionado].click();
    }
  });

  caixa?.addEventListener('click', () => {
    fecharResultados();
    campo.value = '';
    ultimaBusca = '';
    document.getElementById('search-clear').hidden = true;
  });

  document.getElementById('search-clear')?.addEventListener('click', () => {
    campo.value = '';
    ultimaBusca = '';
    fecharResultados();
    document.getElementById('search-clear').hidden = true;
    campo.focus();
  });

  document.addEventListener('click', (evento) => {
    if (!evento.target.closest('.bellwrap')) abrirPainel(false);
    if (!evento.target.closest('#searchbox')) fecharResultados();
  });

  document.addEventListener('keydown', (evento) => {
    if (evento.key !== '/' || evento.ctrlKey || evento.metaKey || evento.altKey) return;
    const foco = document.activeElement;
    const digitando = foco && (foco.matches('input, textarea, select') || foco.isContentEditable);
    if (digitando) return;
    evento.preventDefault();
    campo?.focus();
  });
}
