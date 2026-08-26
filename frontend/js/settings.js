import * as api from './api.js';
import { el, icon, clear, toast, openModal, closeModal, confirmar } from './ui.js';
import { store, hooks } from './store.js';
import { planoInclui } from './cobranca.js';

export async function loadSettings() {

  const temWa = planoInclui('whatsapp');
  const [motivos, campos, wa, modelos] = await Promise.allSettled([
    api.lossReasons(),
    api.customFields(),
    temWa ? api.waConfig() : Promise.resolve(null),
    temWa ? api.waTemplates() : Promise.resolve([]),
  ]);
  if (motivos.status === 'fulfilled') store.lossReasons = motivos.value;
  if (campos.status === 'fulfilled') store.customFields = campos.value;
  if (wa.status === 'fulfilled') store.waConfig = wa.value;
  if (modelos.status === 'fulfilled') store.waTemplates = modelos.value;
}

function renderLossReasons() {
  const alvo = document.getElementById('loss-list');
  if (!alvo) return;
  clear(alvo);

  for (const motivo of store.lossReasons) {
    alvo.append(el('div', { class: `rowitem${motivo.active ? '' : ' is-off'}` }, [
      el('span', { class: 'rowitem__txt' }, [
        el('strong', { text: motivo.label }),
        el('small', {
          text: motivo.used
            ? `${motivo.used} negócio(s) perdido(s) com este motivo`
            : 'ainda não usado',
        }),
      ]),
      el('div', { class: 'rowitem__acts' }, [
        el('button', {
          class: 'btn btn--quiet btn--xs',
          attrs: { type: 'button', 'data-loss-toggle': motivo.id },
          text: motivo.active ? 'Desativar' : 'Ativar',
        }),
        el('button', {
          class: 'iconbtn iconbtn--xs',
          attrs: {
            type: 'button', 'data-loss-del': motivo.id,
            'aria-label': `Apagar “${motivo.label}”`,

            disabled: motivo.used > 0,
            title: motivo.used ? 'Em uso: desative em vez de apagar' : 'Apagar',
          },
        }, [icon('trash')]),
      ]),
    ]));
  }
}

async function criarMotivo(evento) {
  evento.preventDefault();
  const campo = document.getElementById('loss-new');
  const erro = document.getElementById('loss-error');
  const label = campo.value.trim();
  erro.hidden = true;
  if (!label) return;

  try {
    store.lossReasons = await api.createLossReason(label);
    campo.value = '';
    renderLossReasons();
    toast('Motivo adicionado.', 'success');
  } catch (err) {
    if (err?.status !== 401) {
      erro.textContent = err?.message || 'Não foi possível adicionar.';
      erro.hidden = false;
    }
  }
}

const TIPO_ROTULO = {
  texto: 'Texto', numero: 'Número', moeda: 'Valor em reais', data: 'Data',
  lista: 'Lista (uma)', multipla: 'Lista (várias)', sim_nao: 'Sim / Não',
  email: 'E-mail', telefone: 'Telefone',
};

function renderCustomFields() {
  const alvo = document.getElementById('field-list');
  if (!alvo) return;
  clear(alvo);

  if (!store.customFields.length) {
    alvo.append(el('p', { class: 'panel__body', text: 'Nenhum campo criado. O cadastro de lead usa só os campos padrão.' }));
    return;
  }

  for (const campo of store.customFields) {
    alvo.append(el('div', { class: `rowitem${campo.active ? '' : ' is-off'}` }, [
      el('span', { class: 'rowitem__txt' }, [
        el('strong', {}, [
          document.createTextNode(campo.label),
          campo.required ? el('span', { class: 'req', text: 'obrigatório' }) : null,
        ].filter(Boolean)),
        el('small', {
          text: `${TIPO_ROTULO[campo.type] || campo.type}`
            + (campo.options?.length ? ` · ${campo.options.length} opções` : '')
            + ` · chave: ${campo.key}`,
        }),
      ]),
      el('div', { class: 'rowitem__acts' }, [
        el('button', { class: 'btn btn--quiet btn--xs', attrs: { type: 'button', 'data-field-toggle': campo.id }, text: campo.active ? 'Desativar' : 'Ativar' }),
        el('button', { class: 'btn btn--quiet btn--xs', attrs: { type: 'button', 'data-field-edit': campo.id } }, [icon('pencil'), el('span', { text: 'Editar' })]),
        el('button', { class: 'iconbtn iconbtn--xs', attrs: { type: 'button', 'data-field-del': campo.id, 'aria-label': `Apagar “${campo.label}”` } }, [icon('trash')]),
      ]),
    ]));
  }
}

function abrirCampo(id) {
  const campo = id ? store.customFields.find((c) => String(c.id) === String(id)) : null;
  const modal = document.getElementById('field-modal');
  const tipo = document.getElementById('field-type');

  document.getElementById('field-modal-title').textContent = campo ? 'Editar campo' : 'Novo campo';
  document.getElementById('field-error').hidden = true;
  document.getElementById('field-id').value = campo?.id || '';
  document.getElementById('field-label').value = campo?.label || '';
  document.getElementById('field-desc').value = campo?.description || '';
  document.getElementById('field-required').checked = Boolean(campo?.required);
  tipo.value = campo?.type || 'texto';

  tipo.disabled = Boolean(campo);
  document.getElementById('field-type-hint').textContent = campo
    ? 'O tipo não pode ser alterado: já existem valores gravados neste formato.'
    : 'Escolha com atenção — o tipo não pode ser alterado depois.';

  document.getElementById('field-options').value = (campo?.options || []).join('\n');
  sincronizarOpcoes();
  openModal(modal, { focus: '#field-label' });
}

function sincronizarOpcoes() {
  const tipo = document.getElementById('field-type').value;
  document.getElementById('field-options-wrap').hidden = !['lista', 'multipla'].includes(tipo);
}

async function salvarCampo(evento) {
  evento.preventDefault();
  const erro = document.getElementById('field-error');
  const botao = document.getElementById('field-save');
  erro.hidden = true;

  const id = document.getElementById('field-id').value;
  const label = document.getElementById('field-label').value.trim();
  if (!label) {
    erro.textContent = 'Dê um nome ao campo.';
    erro.hidden = false;
    return;
  }
  const opcoes = document.getElementById('field-options').value
    .split('\n').map((o) => o.trim()).filter(Boolean);

  const corpo = {
    label,
    description: document.getElementById('field-desc').value.trim(),
    required: document.getElementById('field-required').checked,
    options: opcoes,
  };

  botao.disabled = true;
  try {
    if (id) store.customFields = await api.updateCustomField(id, corpo);
    else store.customFields = await api.createCustomField({ ...corpo, type: document.getElementById('field-type').value });
    toast(id ? 'Campo atualizado.' : 'Campo criado.', 'success');
    closeModal();
    renderCustomFields();
    await hooks.refreshData({ quiet: true });
  } catch (err) {
    if (err?.status !== 401) {
      erro.textContent = err?.message || 'Não foi possível salvar o campo.';
      erro.hidden = false;
    }
  } finally {
    botao.disabled = false;
  }
}

async function apagarCampo(id) {
  const campo = store.customFields.find((c) => String(c.id) === String(id));
  if (!campo) return;

  let preenchidos = 0;
  try {
    preenchidos = (await api.customFieldUsage(id)).preenchidos;
  } catch {  }

  const ok = await confirmar({
    titulo: 'Apagar este campo?',
    texto: preenchidos
      ? `${preenchidos} lead(s) têm este campo preenchido. O que foi digitado neles some junto.`
      : 'O campo sai do cadastro de leads. Nenhum lead o preencheu ainda.',
    alvo: { nome: campo.label, meta: TIPO_ROTULO[campo.type] || campo.type },
    confirmar: 'Apagar campo',
  });
  if (!ok) return;

  try {
    store.customFields = await api.deleteCustomField(id);
    toast('Campo apagado.', 'info');
    renderCustomFields();
    await hooks.refreshData({ quiet: true });
  } catch (err) {
    if (err?.status !== 401) toast(err?.message || 'Não foi possível apagar.', 'error');
  }
}

const WA_TOM = { conectado: 'ok', erro: 'ruim', desconectado: 'neutra' };

function renderWa() {
  const conf = store.waConfig;
  const estado = document.getElementById('wa-state');
  if (!estado || !conf) return;

  estado.className = `statusdot statusdot--${WA_TOM[conf.status] || 'neutra'}`;
  estado.querySelector('span').textContent = {
    conectado: 'Conectado', erro: 'Com erro', desconectado: 'Não conectado',
  }[conf.status] || conf.status;

  document.getElementById('wa-phone-id').value = conf.phone_number_id || '';
  document.getElementById('wa-waba-id').value = conf.waba_id || '';
  document.getElementById('wa-display').value = conf.display_phone || '';
  document.getElementById('wa-webhook-url').textContent = conf.webhook_url || '—';

  const erro = document.getElementById('wa-config-error');
  if (conf.last_error) {
    erro.textContent = conf.last_error;
    erro.hidden = false;
  } else {
    erro.hidden = true;
  }

  renderTemplates();
}

function renderTemplates() {
  const alvo = document.getElementById('template-list');
  if (!alvo) return;
  clear(alvo);

  if (!store.waTemplates.length) {
    alvo.append(el('p', { class: 'panel__body', text: 'Nenhum modelo cadastrado.' }));
    return;
  }
  for (const modelo of store.waTemplates) {
    alvo.append(el('div', { class: 'rowitem' }, [
      el('span', { class: 'rowitem__txt' }, [
        el('strong', { text: modelo.name }),
        el('small', { text: `${modelo.language} · ${modelo.category} — ${modelo.body.slice(0, 90)}` }),
      ]),
      el('div', { class: 'rowitem__acts' }, [
        el('button', { class: 'iconbtn iconbtn--xs', attrs: { type: 'button', 'data-tpl-del': modelo.id, 'aria-label': `Apagar “${modelo.name}”` } }, [icon('trash')]),
      ]),
    ]));
  }
}

async function salvarWa(evento) {
  evento.preventDefault();
  const erro = document.getElementById('wa-config-error');
  erro.hidden = true;
  try {
    store.waConfig = await api.saveWaConfig({
      phone_number_id: document.getElementById('wa-phone-id').value.trim(),
      waba_id: document.getElementById('wa-waba-id').value.trim(),
      display_phone: document.getElementById('wa-display').value.trim(),
    });
    renderWa();
    toast('Configuração salva. Use "Testar conexão" para confirmar.', 'success');
  } catch (err) {
    if (err?.status !== 401) {
      erro.textContent = err?.message || 'Não foi possível salvar.';
      erro.hidden = false;
    }
  }
}

async function testarWa() {
  const botao = document.getElementById('wa-check');
  botao.disabled = true;
  try {
    const resultado = await api.checkWa();
    store.waConfig = await api.waConfig();
    renderWa();
    if (resultado.ok) {
      toast(`Conectado${resultado.display_phone ? ` como ${resultado.display_phone}` : ''}.`, 'success');
    } else {
      toast(resultado.error || 'Não foi possível conectar.', 'error');
    }
  } catch (err) {
    if (err?.status !== 401) toast(err?.message || 'Falha ao testar.', 'error');
  } finally {
    botao.disabled = false;
  }
}

async function salvarTemplate(evento) {
  evento.preventDefault();
  const erro = document.getElementById('tpl-error');
  erro.hidden = true;
  const corpo = {
    name: document.getElementById('tpl-name').value.trim(),
    language: document.getElementById('tpl-lang').value.trim() || 'pt_BR',
    category: document.getElementById('tpl-cat').value,
    body: document.getElementById('tpl-body').value.trim(),
  };
  if (!corpo.name || !corpo.body) {
    erro.textContent = 'Preencha o nome e o texto do modelo.';
    erro.hidden = false;
    return;
  }
  try {
    await api.createWaTemplate(corpo);
    store.waTemplates = await api.waTemplates();

    store.automationMeta = null;
    closeModal();
    renderTemplates();
    toast('Modelo salvo.', 'success');
  } catch (err) {
    if (err?.status !== 401) {
      erro.textContent = err?.message || 'Não foi possível salvar o modelo.';
      erro.hidden = false;
    }
  }
}

export function renderSettings() {
  renderLossReasons();
  renderCustomFields();
  renderWa();
}

export function wireSettings() {
  document.getElementById('loss-form')?.addEventListener('submit', criarMotivo);
  document.getElementById('field-form')?.addEventListener('submit', salvarCampo);
  document.getElementById('field-type')?.addEventListener('change', sincronizarOpcoes);
  document.getElementById('new-field-btn')?.addEventListener('click', () => abrirCampo(null));
  document.getElementById('wa-config-form')?.addEventListener('submit', salvarWa);
  document.getElementById('wa-check')?.addEventListener('click', testarWa);
  document.getElementById('tpl-form')?.addEventListener('submit', salvarTemplate);
  document.getElementById('new-template-btn')?.addEventListener('click', () => {
    document.getElementById('tpl-error').hidden = true;
    document.getElementById('tpl-form').reset();
    document.getElementById('tpl-lang').value = 'pt_BR';
    openModal(document.getElementById('tpl-modal'), { focus: '#tpl-name' });
  });

  document.getElementById('wa-disconnect')?.addEventListener('click', async () => {
    const ok = await confirmar({
      titulo: 'Desconectar o WhatsApp?',
      texto: 'O envio para de funcionar. As mensagens já trocadas continuam no histórico dos leads.',
      aviso: '',
      confirmar: 'Desconectar',
    });
    if (!ok) return;
    try {
      store.waConfig = await api.disconnectWa();
      renderWa();
      toast('WhatsApp desconectado.', 'info');
    } catch (err) {
      if (err?.status !== 401) toast(err?.message || 'Não foi possível desconectar.', 'error');
    }
  });

  document.getElementById('page-config')?.addEventListener('click', async (evento) => {
    const alternarMotivo = evento.target.closest('[data-loss-toggle]');
    if (alternarMotivo) {
      const motivo = store.lossReasons.find((m) => String(m.id) === String(alternarMotivo.dataset.lossToggle));
      if (!motivo) return;
      try {
        store.lossReasons = await api.updateLossReason(motivo.id, { active: !motivo.active });
        renderLossReasons();
      } catch (err) {
        if (err?.status !== 401) toast(err?.message || 'Não foi possível alterar.', 'error');
      }
      return;
    }

    const apagarMotivo = evento.target.closest('[data-loss-del]');
    if (apagarMotivo) {
      const motivo = store.lossReasons.find((m) => String(m.id) === String(apagarMotivo.dataset.lossDel));
      if (!motivo) return;
      const ok = await confirmar({
        titulo: 'Apagar este motivo?',
        texto: 'Ele sai da lista oferecida ao marcar uma perda.',
        alvo: { nome: motivo.label, meta: 'nenhum negócio usa este motivo' },
        confirmar: 'Apagar',
      });
      if (!ok) return;
      try {
        store.lossReasons = await api.deleteLossReason(motivo.id);
        renderLossReasons();
        toast('Motivo apagado.', 'info');
      } catch (err) {
        if (err?.status !== 401) toast(err?.message || 'Não foi possível apagar.', 'error');
      }
      return;
    }

    const editarCampo = evento.target.closest('[data-field-edit]');
    if (editarCampo) { abrirCampo(editarCampo.dataset.fieldEdit); return; }

    const alternarCampo = evento.target.closest('[data-field-toggle]');
    if (alternarCampo) {
      const campo = store.customFields.find((c) => String(c.id) === String(alternarCampo.dataset.fieldToggle));
      if (!campo) return;
      try {
        store.customFields = await api.updateCustomField(campo.id, { active: !campo.active });
        renderCustomFields();
        await hooks.refreshData({ quiet: true });
      } catch (err) {
        if (err?.status !== 401) toast(err?.message || 'Não foi possível alterar.', 'error');
      }
      return;
    }

    const apagarCampoBtn = evento.target.closest('[data-field-del]');
    if (apagarCampoBtn) { await apagarCampo(apagarCampoBtn.dataset.fieldDel); return; }

    const apagarTpl = evento.target.closest('[data-tpl-del]');
    if (apagarTpl) {
      const modelo = store.waTemplates.find((t) => String(t.id) === String(apagarTpl.dataset.tplDel));
      if (!modelo) return;
      const ok = await confirmar({
        titulo: 'Apagar este modelo?',
        texto: 'Automações que usam este modelo passam a falhar até serem corrigidas.',
        alvo: { nome: modelo.name, meta: modelo.language },
        confirmar: 'Apagar modelo',
      });
      if (!ok) return;
      try {
        await api.deleteWaTemplate(modelo.id);
        store.waTemplates = await api.waTemplates();
        store.automationMeta = null;
        renderTemplates();
      } catch (err) {
        if (err?.status !== 401) toast(err?.message || 'Não foi possível apagar.', 'error');
      }
    }
  });
}
