import * as api from './api.js';
import { el, icon, clear, brl, parseAmount, toast, openModal, closeModal, confirmar, emptyState } from './ui.js';
import { store, hooks, relativo } from './store.js';
import { cartaoProposta } from './lead.js';

export async function loadProposals() {
  try {
    store.proposals = await api.listProposals();
  } catch (err) {
    if (err?.status !== 401) store.proposals = [];
  }
}

function resumo() {
  const alvo = document.getElementById('prop-summary');
  clear(alvo);
  const itens = store.proposals;
  if (!itens.length) return;

  const emAberto = itens.filter((p) => ['Enviada', 'Visualizada'].includes(p.status));
  const aceitas = itens.filter((p) => p.status === 'Aceita');
  const vistas = itens.filter((p) => p.viewed_at && p.status !== 'Aceita' && p.status !== 'Recusada');
  const decididas = itens.filter((p) => ['Aceita', 'Recusada'].includes(p.status));

  const cartoes = [
    { rotulo: 'Em aberto', valor: brl(emAberto.reduce((s, p) => s + p.total, 0)),
      pe: `${emAberto.length} proposta(s) esperando resposta`, ico: 'clock', tom: 'info' },
    { rotulo: 'Abertas pelo cliente', valor: String(vistas.length),
      pe: 'viram a proposta e ainda não responderam', ico: 'eye', tom: 'atencao' },
    { rotulo: 'Aceitas', valor: brl(aceitas.reduce((s, p) => s + p.total, 0)),
      pe: `${aceitas.length} de ${decididas.length} respondidas`, ico: 'trophy', tom: 'ok' },
  ];

  for (const c of cartoes) {
    alvo.append(el('div', { class: `bezel sumcard sumcard--${c.tom} tilt3d` }, [
      el('div', { class: 'bezel__in sumcard__in' }, [
        el('span', { class: 'sumcard__ico' }, [icon(c.ico)]),
        el('span', { class: 'sumcard__txt' }, [
          el('small', { text: c.rotulo }),
          el('strong', { text: c.valor }),
          el('span', { text: c.pe }),
        ]),
      ]),
    ]));
  }
}

export function renderProposals() {
  resumo();
  const lista = document.getElementById('prop-list');
  clear(lista);

  const filtro = store.propFilter;
  const itens = filtro === 'todas'
    ? store.proposals
    : store.proposals.filter((p) => p.status === filtro);

  if (!itens.length) {
    lista.append(emptyState({
      title: store.proposals.length ? 'Nenhuma proposta neste filtro' : 'Nenhuma proposta ainda',
      text: store.proposals.length
        ? 'Troque o filtro acima.'
        : 'Monte uma proposta com itens e valores, envie por link e veja quando o cliente abrir.',
      iconName: 'doc',
      action: store.proposals.length ? null : { label: 'Criar a primeira', onClick: () => abrirProposta(null) },
    }));
    return;
  }
  for (const p of itens) lista.append(cartaoProposta(p));
}

function linhaItem(item = {}) {
  return el('div', { class: 'item' }, [
    el('input', { class: 'item__desc', attrs: { type: 'text', maxlength: 200, placeholder: 'Descrição', value: item.description || '', 'aria-label': 'Descrição do item' } }),
    el('input', { class: 'item__qty', attrs: { type: 'text', inputmode: 'decimal', value: item.qty ?? 1, 'aria-label': 'Quantidade' } }),
    el('input', { class: 'item__price', attrs: { type: 'text', inputmode: 'decimal', value: item.unit_price ?? '', placeholder: '0,00', 'aria-label': 'Valor unitário' } }),
    el('output', { class: 'item__total', text: brl(item.total || 0) }),
    el('button', { class: 'iconbtn iconbtn--xs', attrs: { type: 'button', 'data-rm-item': '', 'aria-label': 'Remover item' } }, [icon('close')]),
  ]);
}

function lerItens() {
  return [...document.querySelectorAll('#prop-items .item')].map((linha) => ({
    description: linha.querySelector('.item__desc').value.trim(),
    qty: parseAmount(linha.querySelector('.item__qty').value) ?? 0,
    unit_price: parseAmount(linha.querySelector('.item__price').value) ?? 0,
  }));
}

function recalcular() {
  let subtotal = 0;
  for (const linha of document.querySelectorAll('#prop-items .item')) {
    const qtd = parseAmount(linha.querySelector('.item__qty').value) ?? 0;
    const preco = parseAmount(linha.querySelector('.item__price').value) ?? 0;
    const total = qtd * preco;
    subtotal += total;
    linha.querySelector('.item__total').textContent = brl(total);
  }
  const desconto = parseAmount(document.getElementById('prop-discount').value) ?? 0;
  document.getElementById('prop-subtotal').textContent = brl(subtotal);
  document.getElementById('prop-total').textContent = brl(Math.max(0, subtotal - desconto));
}

export function abrirProposta(id, leadIdSugerido = null) {
  const modal = document.getElementById('prop-modal');
  const proposta = id ? store.proposals.find((p) => String(p.id) === String(id))
    || store.leadProposals.find((p) => String(p.id) === String(id)) : null;

  document.getElementById('prop-modal-title').textContent = proposta ? `Editar ${proposta.number}` : 'Nova proposta';
  document.getElementById('prop-error').hidden = true;
  document.getElementById('prop-id').value = proposta?.id || '';

  const seletor = document.getElementById('prop-lead');
  clear(seletor);
  const candidatos = (hooks.state?.leads || []).filter(
    (l) => !['Ganho', 'Perdido'].includes(l.status) || String(l.id) === String(proposta?.lead_id),
  );
  for (const lead of candidatos) {
    seletor.append(el('option', { attrs: { value: lead.id }, text: `${lead.name} — ${lead.company}` }));
  }
  const alvo = proposta?.lead_id || leadIdSugerido || candidatos[0]?.id;
  if (alvo) seletor.value = String(alvo);
  seletor.disabled = Boolean(proposta);

  document.getElementById('prop-title').value = proposta?.title || '';
  document.getElementById('prop-discount').value = proposta?.discount || '';
  document.getElementById('prop-terms').value = proposta?.terms || '';
  document.getElementById('prop-delivery').value = proposta?.delivery || '';
  document.getElementById('prop-notes').value = proposta?.notes || '';
  document.getElementById('prop-validity').value = proposta?.valid_until
    ? Math.max(0, Math.round((new Date(proposta.valid_until) - Date.now()) / 86400000))
    : 15;

  const itens = document.getElementById('prop-items');
  clear(itens);
  const linhas = proposta?.items?.length ? proposta.items : [{}];
  for (const item of linhas) itens.append(linhaItem(item));

  recalcular();
  openModal(modal, { focus: '#prop-title' });
}

async function salvarProposta(evento) {
  evento.preventDefault();
  const erro = document.getElementById('prop-error');
  const botao = document.getElementById('prop-save');
  erro.hidden = true;

  const id = document.getElementById('prop-id').value;
  const itens = lerItens().filter((i) => i.description);
  if (!itens.length) {
    erro.textContent = 'Adicione pelo menos um item com descrição.';
    erro.hidden = false;
    return;
  }
  const titulo = document.getElementById('prop-title').value.trim();
  if (!titulo) {
    erro.textContent = 'Dê um título para a proposta.';
    erro.hidden = false;
    return;
  }

  const corpo = {
    title: titulo,
    items: itens,
    discount: parseAmount(document.getElementById('prop-discount').value) ?? 0,
    terms: document.getElementById('prop-terms').value.trim(),
    delivery: document.getElementById('prop-delivery').value.trim(),
    notes: document.getElementById('prop-notes').value.trim(),
    validity_days: Number(document.getElementById('prop-validity').value) || 0,
  };

  botao.disabled = true;
  try {
    if (id) {
      await api.updateProposal(id, corpo);
      toast('Proposta atualizada.', 'success');
    } else {
      corpo.lead_id = Number(document.getElementById('prop-lead').value);
      if (!corpo.lead_id) throw new Error('Escolha o negócio da proposta.');
      await api.createProposal(corpo);
      toast('Proposta criada.', 'success');
    }
    closeModal();
    await recarregarTudo();
  } catch (err) {
    if (err?.status !== 401) {
      erro.textContent = err?.message || 'Não foi possível salvar a proposta.';
      erro.hidden = false;
    }
  } finally {
    botao.disabled = false;
  }
}

export function enviarProposta(id) {
  const proposta = store.proposals.find((p) => String(p.id) === String(id))
    || store.leadProposals.find((p) => String(p.id) === String(id));
  if (!proposta) return;

  const modal = document.getElementById('send-modal');
  document.getElementById('send-error').hidden = true;
  document.getElementById('send-link').value = proposta.public_url;
  document.getElementById('send-modal-title').textContent = `Enviar ${proposta.number}`;
  document.getElementById('send-message').value =
    `Olá! Segue a proposta “${proposta.title}” no valor de ${brl(proposta.total)}: ${proposta.public_url}`;
  modal.dataset.propId = proposta.id;
  document.getElementById('send-msg-field').hidden = true;
  modal.querySelector('input[value="link"]').checked = true;
  openModal(modal, { focus: '#send-copy' });
}

async function confirmarEnvio(evento) {
  evento.preventDefault();
  const modal = document.getElementById('send-modal');
  const erro = document.getElementById('send-error');
  const botao = document.getElementById('send-confirm');
  const id = modal.dataset.propId;
  const canal = modal.querySelector('input[name="send-channel"]:checked')?.value || 'link';
  erro.hidden = true;
  botao.disabled = true;

  try {
    await api.sendProposal(id, { channel: canal, message: document.getElementById('send-message').value.trim() });
    toast(canal === 'whatsapp' ? 'Proposta enviada no WhatsApp.' : 'Proposta marcada como enviada.', 'success');
    closeModal();
    await recarregarTudo();
  } catch (err) {
    if (err?.status !== 401) {
      erro.textContent = err?.message || 'Não foi possível enviar.';
      erro.hidden = false;
    }
  } finally {
    botao.disabled = false;
  }
}

export async function apagarProposta(id) {
  const proposta = store.proposals.find((p) => String(p.id) === String(id))
    || store.leadProposals.find((p) => String(p.id) === String(id));
  if (!proposta) return;

  const ok = await confirmar({
    titulo: 'Excluir esta proposta?',
    texto: 'Ela sai do histórico do negócio, e o link que o cliente tem deixa de abrir.',
    alvo: { nome: proposta.title, meta: `${proposta.number} · ${brl(proposta.total)} · ${proposta.status}` },
    confirmar: 'Excluir proposta',
  });
  if (!ok) return;

  try {
    await api.deleteProposal(id);
    toast('Proposta excluída.', 'info');
    await recarregarTudo();
  } catch (err) {
    if (err?.status !== 401) toast(err?.message || 'Não foi possível excluir.', 'error');
  }
}

async function recarregarTudo() {
  await loadProposals();
  if (store.leadId) {
    const { loadLead, renderLead } = await import('./lead.js');
    await loadLead(store.leadId);
    await hooks.refreshData({ quiet: true });
    renderLead();
  } else {
    await hooks.refreshData({ quiet: true });
  }
  hooks.renderCurrentPage();
}

export function wireProposals() {
  document.getElementById('prop-form')?.addEventListener('submit', salvarProposta);
  document.getElementById('send-form')?.addEventListener('submit', confirmarEnvio);
  document.getElementById('new-proposal-btn')?.addEventListener('click', () => abrirProposta(null));
  document.getElementById('prop-add-item')?.addEventListener('click', () => {
    document.getElementById('prop-items').append(linhaItem());
    recalcular();
  });

  document.getElementById('prop-modal')?.addEventListener('input', recalcular);
  document.getElementById('prop-modal')?.addEventListener('click', (evento) => {
    const remover = evento.target.closest('[data-rm-item]');
    if (!remover) return;
    const itens = document.getElementById('prop-items');

    if (itens.children.length > 1) remover.closest('.item').remove();
    else itens.replaceChildren(linhaItem());
    recalcular();
  });

  document.getElementById('send-modal')?.addEventListener('change', (evento) => {
    if (evento.target.name !== 'send-channel') return;
    document.getElementById('send-msg-field').hidden = evento.target.value !== 'whatsapp';
  });

  document.getElementById('send-copy')?.addEventListener('click', async () => {
    const campo = document.getElementById('send-link');
    try {
      await navigator.clipboard.writeText(campo.value);
      toast('Link copiado.', 'success');
    } catch {

      campo.select();
      toast('Copie o link selecionado (Ctrl+C).', 'info');
    }
  });

  const pagina = document.getElementById('page-propostas');
  pagina?.addEventListener('click', async (evento) => {
    const filtro = evento.target.closest('[data-prop]');
    if (filtro) {
      store.propFilter = filtro.dataset.prop;
      pagina.querySelectorAll('[data-prop]').forEach((b) => b.classList.toggle('is-active', b === filtro));
      renderProposals();
      return;
    }
    const editar = evento.target.closest('[data-prop-edit]');
    if (editar) { abrirProposta(editar.dataset.propEdit); return; }
    const enviar = evento.target.closest('[data-prop-send]');
    if (enviar) { enviarProposta(enviar.dataset.propSend); return; }
    const apagar = evento.target.closest('[data-prop-del]');
    if (apagar) { await apagarProposta(apagar.dataset.propDel); }
  });
}
