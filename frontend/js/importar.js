import * as api from './api.js';
import { el, clear, toast, openModal } from './ui.js';
import { hooks } from './store.js';

const CAMPOS = [
  { chave: 'name', rotulo: 'Nome', obrig: true },
  { chave: 'company', rotulo: 'Empresa', obrig: true },
  { chave: 'value', rotulo: 'Valor', obrig: false },
  { chave: 'email', rotulo: 'E-mail', obrig: false },
  { chave: 'phone', rotulo: 'Telefone', obrig: false },
  { chave: 'whatsapp', rotulo: 'WhatsApp', obrig: false },
  { chave: 'source', rotulo: 'Origem', obrig: false },
  { chave: 'owner', rotulo: 'Responsável', obrig: false },
  { chave: 'notes', rotulo: 'Observações', obrig: false },
];

const estado = { csv: '', colunas: [], mapping: {} };

function setErro(msg) {
  const e = document.getElementById('import-error');
  if (!e) return;
  e.textContent = msg || '';
  e.hidden = !msg;
}

function mostrarPasso(qual) {
  for (const p of ['file', 'preview', 'result']) {
    const n = document.getElementById(`import-step-${p}`);
    if (n) n.hidden = p !== qual;
  }
  const btn = document.getElementById('import-confirm-btn');
  if (btn) btn.hidden = qual !== 'preview';
}

export function openImport() {
  estado.csv = '';
  estado.colunas = [];
  estado.mapping = {};
  setErro('');
  const file = document.getElementById('import-file');
  if (file) file.value = '';
  const lbl = document.getElementById('import-file-label');
  if (lbl) lbl.textContent = 'Escolher arquivo CSV';
  mostrarPasso('file');
  openModal(document.getElementById('import-modal'));
}

async function aoEscolherArquivo(evento) {
  const arquivo = evento.target.files?.[0];
  if (!arquivo) return;
  setErro('');
  const lbl = document.getElementById('import-file-label');
  if (lbl) lbl.textContent = arquivo.name;
  if (arquivo.size > 2_000_000) {
    setErro('Arquivo grande demais (máximo 2 MB). Divida em partes.');
    return;
  }
  try {
    estado.csv = await arquivo.text();
  } catch {
    setErro('Não consegui ler o arquivo.');
    return;
  }
  await previsualizar(null);
}

async function previsualizar(mapping) {
  setErro('');
  const has_header = document.getElementById('import-has-header')?.checked ?? true;
  try {
    const d = await api.importPreview({ csv: estado.csv, mapping: mapping || {}, has_header });
    estado.colunas = d.colunas || [];
    estado.mapping = mapping || d.mapeamento_sugerido || {};
    renderPreview(d);
    mostrarPasso('preview');
  } catch (err) {
    if (err?.status === 401) return;
    setErro(err?.message || 'Não foi possível ler o CSV.');
    mostrarPasso('file');
  }
}

function renderPreview(d) {
  const resumo = document.getElementById('import-summary');
  clear(resumo);
  [
    { n: d.novos, t: 'entram', tom: 'ok' },
    { n: d.duplicados, t: 'duplicados', tom: 'atencao' },
    { n: d.com_erro, t: 'com erro', tom: 'ruim' },
  ].forEach((b) => resumo.append(el('div', { class: `impcard impcard--${b.tom}` }, [
    el('span', { class: 'impcard__n', text: String(b.n) }),
    el('span', { class: 'impcard__t', text: b.t }),
  ])));

  const mapa = document.getElementById('import-mapping');
  clear(mapa);
  for (const campo of CAMPOS) {
    const sel = el('select', { class: 'input', attrs: { 'data-map': campo.chave } });
    sel.append(el('option', { text: '— não importar —', attrs: { value: '' } }));
    for (const col of estado.colunas) {
      const opt = el('option', { text: col, attrs: { value: col } });
      if (estado.mapping[campo.chave] === col) opt.selected = true;
      sel.append(opt);
    }
    mapa.append(el('label', { class: 'impmap' }, [
      el('span', { class: 'impmap__lbl', text: campo.rotulo + (campo.obrig ? ' *' : '') }),
      sel,
    ]));
  }

  const tb = document.getElementById('import-sample');
  clear(tb);
  const tom = { novo: 'ok', duplicado: 'atencao', erro: 'ruim' };
  const rotulo = { novo: 'Novo', duplicado: 'Duplicado', erro: 'Erro' };
  for (const item of d.amostra || []) {
    tb.append(el('tr', {}, [
      el('td', { text: String(item.linha) }),
      el('td', { text: item.nome || '—' }),
      el('td', { text: item.empresa || '—' }),
      el('td', {}, [el('span', {
        class: `pill pill--${tom[item.estado] || 'neutra'}`,
        text: item.motivo || rotulo[item.estado] || item.estado,
      })]),
    ]));
  }

  const btn = document.getElementById('import-confirm-btn');
  if (btn) {
    btn.disabled = !d.novos && !d.duplicados;
    btn.textContent = d.novos ? `Importar ${d.novos} lead${d.novos === 1 ? '' : 's'}` : 'Importar';
  }
}

function lerMapeamento() {
  const mapa = {};
  document.querySelectorAll('#import-mapping [data-map]').forEach((s) => {
    if (s.value) mapa[s.dataset.map] = s.value;
  });
  return mapa;
}

async function aoMudarMapeamento() {
  const mapping = lerMapeamento();
  if (!mapping.name || !mapping.company) {
    setErro('Escolha quais colunas são o Nome e a Empresa.');
    const btn = document.getElementById('import-confirm-btn');
    if (btn) btn.disabled = true;
    return;
  }
  await previsualizar(mapping);
}

async function confirmar() {
  const mapping = lerMapeamento();
  if (!mapping.name || !mapping.company) {
    setErro('Escolha quais colunas são o Nome e a Empresa.');
    return;
  }
  const has_header = document.getElementById('import-has-header')?.checked ?? true;
  const pular = document.getElementById('import-skip-dups')?.checked ?? true;
  const btn = document.getElementById('import-confirm-btn');
  if (btn) btn.disabled = true;
  try {
    const d = await api.importConfirm({ csv: estado.csv, mapping, has_header, pular_duplicados: pular });
    const r = document.getElementById('import-result');
    clear(r);
    r.append(el('p', { class: 'import-done', text: `${d.inseridos} lead(s) importado(s) com sucesso.` }));
    if (d.pulados_duplicados) {
      r.append(el('p', { text: `${d.pulados_duplicados} duplicado(s) foram pulados.` }));
    }
    if (d.com_erro) {
      r.append(el('p', { text: `${d.com_erro} linha(s) tinham erro e ficaram de fora.` }));
    }

    if (d.barrados_limite) {
      r.append(el('p', {
        text: `${d.barrados_limite} linha(s) ficaram de fora porque a conta atingiu o limite de negócios. Fale com a gente para ampliar.`,
      }));
    }
    mostrarPasso('result');
    toast(`${d.inseridos} lead(s) importado(s).`, 'success');
    await hooks.refreshData({ quiet: true });
    hooks.renderCurrentPage();
  } catch (err) {
    if (err?.status !== 401) setErro(err?.message || 'Não foi possível importar.');
  } finally {
    if (btn) btn.disabled = false;
  }
}

export function wireImport() {
  document.getElementById('import-file')?.addEventListener('change', aoEscolherArquivo);
  document.getElementById('import-has-header')?.addEventListener('change', () => {
    if (estado.csv) previsualizar(estado.mapping);
  });
  document.getElementById('import-mapping')?.addEventListener('change', aoMudarMapeamento);
  document.getElementById('import-confirm-btn')?.addEventListener('click', confirmar);
}
