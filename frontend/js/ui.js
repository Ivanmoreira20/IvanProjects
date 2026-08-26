const SVG_NS = 'http://www.w3.org/2000/svg';

export function el(tag, props = {}, children = []) {
  const node = document.createElement(tag);
  if (props.class) node.className = props.class;
  if (props.text != null) node.textContent = String(props.text);
  if (props.attrs) {
    for (const [k, v] of Object.entries(props.attrs)) {
      if (v === false || v == null) continue;
      node.setAttribute(k, v === true ? '' : String(v));
    }
  }
  if (props.on) {
    for (const [evt, fn] of Object.entries(props.on)) node.addEventListener(evt, fn);
  }
  for (const child of [].concat(children)) {
    if (child == null) continue;
    node.append(child);
  }
  return node;
}

export function icon(name, cls = 'ico') {
  const svg = document.createElementNS(SVG_NS, 'svg');
  svg.setAttribute('class', cls);
  svg.setAttribute('aria-hidden', 'true');
  const use = document.createElementNS(SVG_NS, 'use');
  use.setAttribute('href', `#i-${name}`);
  svg.append(use);
  return svg;
}

function svgEl(tag, attrs = {}) {
  const node = document.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, String(v));
  return node;
}

export function clear(node) {
  if (node) node.replaceChildren();
}

const brlFmt = new Intl.NumberFormat('pt-BR', {
  style: 'currency', currency: 'BRL', minimumFractionDigits: 0, maximumFractionDigits: 0,
});
const numFmt = new Intl.NumberFormat('pt-BR');

export function brl(value) {
  const n = Number(value);
  return brlFmt.format(Number.isFinite(n) ? n : 0);
}

export function num(value) {
  const n = Number(value);
  return numFmt.format(Number.isFinite(n) ? n : 0);
}

export function pct(value, digits = 1) {
  const n = Number(value);
  return `${(Number.isFinite(n) ? n : 0).toFixed(digits).replace('.', ',')}%`;
}

export function compact(value) {
  const n = Number(value) || 0;
  const abs = Math.abs(n);
  if (abs >= 1e6) return `${(n / 1e6).toFixed(1).replace('.', ',')} mi`;
  if (abs >= 1e3) return `${Math.round(n / 1e3)} mil`;
  return numFmt.format(Math.round(n));
}

export function initials(name) {
  const parts = String(name || '').trim().split(/\s+/).filter(Boolean);
  if (!parts.length) return '··';
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
}

export function parseAmount(input) {
  if (typeof input === 'number') return Number.isFinite(input) && input >= 0 ? input : null;
  let s = String(input ?? '').trim();
  if (!s) return null;
  s = s.replace(/\s/g, '').replace(/^R\$/i, '');
  if (!/^[\d.,]+$/.test(s)) return null;

  const hasDot = s.includes('.');
  const hasComma = s.includes(',');
  if (hasDot && hasComma) {

    s = s.lastIndexOf(',') > s.lastIndexOf('.')
      ? s.replace(/\./g, '').replace(',', '.')
      : s.replace(/,/g, '');
  } else if (hasComma) {
    s = s.replace(/\./g, '').replace(',', '.');
  } else if (hasDot) {

    const tail = s.slice(s.lastIndexOf('.') + 1);
    if (tail.length === 3 && s.split('.').every((p, i) => i === 0 || p.length === 3)) {
      s = s.replace(/\./g, '');
    }
  }

  const n = parseFloat(s);
  if (!Number.isFinite(n) || n < 0) return null;
  return Math.round(n * 100) / 100;
}

const THEME_KEY = 'vertex_theme';

export function readTheme() {
  try {
    const saved = localStorage.getItem(THEME_KEY);
    if (saved === 'light' || saved === 'dark') return saved;
  } catch {  }
  return 'dark';
}

export function applyTheme(theme) {
  const light = theme === 'light';
  document.body.classList.toggle('light-theme', light);
  const meta = document.querySelector('meta[name="theme-color"]');
  if (meta) meta.setAttribute('content', light ? '#F4F1FA' : '#0A0710');
  document.querySelectorAll('input[name="theme"]').forEach((r) => {
    r.checked = r.value === (light ? 'light' : 'dark');
  });
}

export function saveTheme(theme) {
  try { localStorage.setItem(THEME_KEY, theme); } catch {  }
}

const TOAST_ICON = { success: 'check-circle', error: 'alert', info: 'info' };

export function toast(message, kind = 'info', ms = 4000) {
  const host = document.getElementById('toasts');
  if (!host) return;
  const node = el('div', { class: 'toast', attrs: { 'data-kind': kind } }, [
    icon(TOAST_ICON[kind] || 'info'),
    el('span', { text: message }),
  ]);
  host.append(node);
  const kill = () => {
    node.classList.add('is-out');
    node.addEventListener('animationend', () => node.remove(), { once: true });
    setTimeout(() => node.remove(), 600);
  };
  setTimeout(kill, ms);
}

const FOCUSABLE = 'a[href],button:not([disabled]),input:not([disabled]),select:not([disabled]),textarea:not([disabled]),[tabindex]:not([tabindex="-1"])';

let openModalEl = null;
let lastFocused = null;
let onCloseCb = null;

function focusables(root) {
  return Array.from(root.querySelectorAll(FOCUSABLE)).filter(
    (n) => n.offsetParent !== null || n === document.activeElement,
  );
}

function onModalKeydown(e) {
  if (!openModalEl) return;
  if (e.key === 'Escape') {
    e.preventDefault();
    closeModal();
    return;
  }
  if (e.key !== 'Tab') return;

  const items = focusables(openModalEl);
  if (!items.length) return;
  const first = items[0];
  const last = items[items.length - 1];
  const active = document.activeElement;

  if (e.shiftKey && (active === first || !openModalEl.contains(active))) {
    e.preventDefault();
    last.focus();
  } else if (!e.shiftKey && active === last) {
    e.preventDefault();
    first.focus();
  }
}

export function openModal(modal, { onClose, focus } = {}) {
  if (!modal) return;
  lastFocused = document.activeElement;
  onCloseCb = onClose || null;
  openModalEl = modal;
  modal.hidden = false;
  document.body.style.overflow = 'hidden';
  document.addEventListener('keydown', onModalKeydown, true);

  const target = (focus && modal.querySelector(focus)) || focusables(modal)[0];
  if (target) requestAnimationFrame(() => target.focus());
}

export function closeModal() {
  if (!openModalEl) return;
  const modal = openModalEl;
  openModalEl = null;
  document.removeEventListener('keydown', onModalKeydown, true);
  modal.hidden = true;
  document.body.style.overflow = '';
  if (lastFocused && document.contains(lastFocused)) lastFocused.focus();
  lastFocused = null;
  const cb = onCloseCb;
  onCloseCb = null;
  if (cb) cb();
}

export function isModalOpen() {
  return openModalEl !== null;
}

export function confirmar({
  titulo = 'Tem certeza?',
  texto = '',
  alvo = null,
  aviso = 'Esta ação não pode ser desfeita.',
  confirmar: rotuloSim = 'Confirmar',
  cancelar: rotuloNao = 'Cancelar',
} = {}) {
  const modal = document.getElementById('confirm-modal');
  const sim = document.getElementById('confirm-yes');
  const nao = document.getElementById('confirm-no');
  if (!modal || !sim || !nao) {

    return Promise.resolve(window.confirm(`${titulo}\n\n${texto}`));
  }

  document.getElementById('confirm-title').textContent = titulo;
  document.getElementById('confirm-text').textContent = texto;
  sim.textContent = rotuloSim;
  nao.textContent = rotuloNao;

  const avisoEl = document.getElementById('confirm-warn');
  avisoEl.textContent = aviso;
  avisoEl.hidden = !aviso;

  const alvoEl = document.getElementById('confirm-target');
  if (alvo && alvo.nome) {
    document.getElementById('confirm-target-name').textContent = alvo.nome;
    document.getElementById('confirm-target-meta').textContent = alvo.meta || '';
    alvoEl.hidden = false;
  } else {
    alvoEl.hidden = true;
  }

  return new Promise((resolve) => {
    let decidido = false;

    const responder = (valor) => {
      if (decidido) return;
      decidido = true;
      sim.removeEventListener('click', aoSim);
      resolve(valor);
      if (valor) closeModal();
    };

    const aoSim = () => responder(true);
    sim.addEventListener('click', aoSim);

    openModal(modal, { focus: '#confirm-no', onClose: () => responder(false) });
  });
}

let vselectSeq = 0;

export function vselect({
  options = [], value = null, onChange = null, ariaLabel = '', size = 'md',
} = {}) {
  const uid = `vsel-${(vselectSeq += 1)}`;
  let opts = options.slice();
  let current = value != null ? value : (opts[0] && opts[0].value);
  let open = false;
  let active = Math.max(0, opts.findIndex((o) => o.value === current));

  const caption = el('span', { class: 'vselect__cap' });
  const trigger = el('button', {
    class: 'vselect__btn',
    attrs: {
      type: 'button', role: 'combobox', 'aria-haspopup': 'listbox',
      'aria-expanded': 'false', 'aria-controls': `${uid}-list`,
      'aria-label': ariaLabel || undefined,
    },
  }, [caption, icon('chevron-down', 'ico vselect__chev')]);

  const list = el('ul', {
    class: 'vselect__list',
    attrs: { id: `${uid}-list`, role: 'listbox', 'aria-label': ariaLabel || undefined },
  });
  const pop = el('div', { class: 'vselect__pop', attrs: { hidden: true } }, [list]);
  const root = el('div', { class: `vselect vselect--${size}` }, [trigger, pop]);

  const optId = (i) => `${uid}-opt-${i}`;
  const find = (v) => opts.find((o) => o.value === v);

  function paintCaption() {
    const o = find(current);
    caption.textContent = o ? o.label : '—';
  }

  function paintList() {
    clear(list);
    opts.forEach((o, i) => {
      const li = el('li', {
        class: 'vselect__opt',
        attrs: { id: optId(i), role: 'option', 'aria-selected': o.value === current ? 'true' : 'false' },
        on: { click: () => choose(i), mousemove: () => setActive(i) },
      }, [
        el('span', { class: 'vselect__optmain' }, [
          el('span', { class: 'vselect__optlabel', text: o.label }),
          o.hint ? el('span', { class: 'vselect__opthint', text: o.hint }) : null,
        ]),
        o.value === current ? icon('check', 'ico vselect__optcheck') : null,
      ]);
      list.append(li);
    });
    paintActive();
  }

  function paintActive() {
    Array.from(list.children).forEach((li, i) => li.classList.toggle('is-active', open && i === active));
    trigger.setAttribute('aria-activedescendant', open ? optId(active) : '');
  }

  function setActive(i) {
    active = Math.max(0, Math.min(i, opts.length - 1));
    paintActive();
    const li = list.children[active];
    if (li) li.scrollIntoView({ block: 'nearest' });
  }

  function openPop() {
    if (open || !opts.length) return;
    open = true;
    pop.hidden = false;
    trigger.setAttribute('aria-expanded', 'true');
    root.classList.add('is-open');
    setActive(Math.max(0, opts.findIndex((o) => o.value === current)));
    document.addEventListener('pointerdown', onDocDown, true);
  }

  function closePop({ focus = false } = {}) {
    if (!open) return;
    open = false;
    pop.hidden = true;
    trigger.setAttribute('aria-expanded', 'false');
    trigger.setAttribute('aria-activedescendant', '');
    root.classList.remove('is-open');
    document.removeEventListener('pointerdown', onDocDown, true);
    if (focus) trigger.focus();
  }

  function choose(i) {
    const o = opts[i];
    if (!o) return;
    const changed = o.value !== current;
    current = o.value;
    paintCaption();
    paintList();
    closePop({ focus: true });
    if (changed && onChange) onChange(current);
  }

  function onDocDown(e) { if (!root.contains(e.target)) closePop(); }

  trigger.addEventListener('click', () => (open ? closePop({ focus: true }) : openPop()));
  trigger.addEventListener('keydown', (e) => {
    if (!open) {
      if (['ArrowDown', 'ArrowUp', 'Enter', ' '].includes(e.key)) { e.preventDefault(); openPop(); }
      return;
    }
    switch (e.key) {
      case 'Escape': e.preventDefault(); closePop({ focus: true }); break;
      case 'ArrowDown': e.preventDefault(); setActive(active + 1); break;
      case 'ArrowUp': e.preventDefault(); setActive(active - 1); break;
      case 'Home': e.preventDefault(); setActive(0); break;
      case 'End': e.preventDefault(); setActive(opts.length - 1); break;
      case 'Enter': case ' ': e.preventDefault(); choose(active); break;
      case 'Tab': closePop(); break;
      default: break;
    }
  });

  paintCaption();
  paintList();

  return {
    node: root,
    get value() { return current; },
    set value(v) { current = v; active = Math.max(0, opts.findIndex((o) => o.value === v)); paintCaption(); paintList(); },
    setOptions(next) {
      opts = next.slice();
      if (!find(current)) current = opts[0] && opts[0].value;
      active = Math.max(0, opts.findIndex((o) => o.value === current));
      paintCaption();
      paintList();
    },
  };
}

export function emptyState({ title, text, iconName = 'inbox', action } = {}) {
  const kids = [
    el('div', { class: 'empty__ico' }, [icon(iconName)]),
    el('p', { class: 'empty__title', text: title || 'Sem dados' }),
  ];
  if (text) kids.push(el('p', { class: 'empty__text', text }));
  if (action) {

    const filhos = action.iconName === null
      ? [el('span', { text: action.label })]
      : [icon(action.iconName || 'plus'), el('span', { text: action.label })];
    kids.push(el('button', {
      class: 'btn btn--ghost',
      attrs: { type: 'button' },
      on: { click: action.onClick },
    }, filhos));
  }
  return el('div', { class: 'empty' }, kids);
}

export function emptyRow(colspan, message, text = '') {
  const kids = [
    el('div', { class: 'empty__ico' }, [icon('inbox')]),
    el('p', { class: 'empty__title', text: message }),
  ];
  if (text) kids.push(el('p', { class: 'empty__text', text }));
  return el('tr', {}, [
    el('td', { class: 'empty--row', attrs: { colspan } }, [
      el('div', { class: 'empty' }, kids),
    ]),
  ]);
}

export function seriesColors() {
  const cs = getComputedStyle(document.body);
  const out = [];
  for (let i = 1; i <= 8; i += 1) {
    const v = cs.getPropertyValue(`--series-${i}`).trim();
    if (v) out.push(v);
  }
  return out.length ? out : ['#8B5CF6', '#0D9488', '#D946EF', '#D97706', '#0284C7', '#F43F5E', '#6366F1', '#16A34A'];
}

export function themeInk() {
  const cs = getComputedStyle(document.body);
  return {
    grid: cs.getPropertyValue('--grid-line').trim() || 'rgba(255,255,255,.07)',
    axis: cs.getPropertyValue('--axis-ink').trim() || '#877D9C',
    accent: cs.getPropertyValue('--accent').trim() || '#C084FC',
    surface: cs.getPropertyValue('--panel').trim() || 'transparent',
  };
}

function fbFrame(target) {
  clear(target);
  const box = el('div', { class: 'fb' });
  target.append(box);
  return box;
}

function fbDonut(target, { segments = [], total = 0, currency = true } = {}) {
  const box = fbFrame(target);
  const sum = segments.reduce((a, s) => a + (Number(s.value) || 0), 0);
  if (!segments.length || sum <= 0) return fbEmpty(target, { message: 'Sem dados de segmento' });

  const R = 62;
  const C = 2 * Math.PI * R;
  const GAP = 4;
  const svg = svgEl('svg', {
    class: 'fb__svg', viewBox: '0 0 200 200', role: 'img',
    'aria-label': `Distribuição por segmento, ${segments.length} categorias`,
  });

  let offset = 0;
  for (const s of segments) {
    const frac = (Number(s.value) || 0) / sum;
    const len = Math.max(frac * C - GAP, 0.5);
    const ring = svgEl('circle', {
      class: 'fb__slice', cx: 100, cy: 100, r: R,
      fill: 'none', stroke: s.color, 'stroke-width': 22,
      'stroke-dasharray': `${len} ${C - len}`,
      'stroke-dashoffset': -offset,
      transform: 'rotate(-90 100 100)',
      'stroke-linecap': 'butt',
    });
    const t = svgEl('title');
    t.textContent = `${s.label}: ${currency ? brl(s.value) : num(s.value)}`;
    ring.append(t);
    svg.append(ring);
    offset += frac * C;
  }

  const lbl = svgEl('text', { class: 'fb__centerlbl', x: 100, y: 92, 'text-anchor': 'middle' });
  lbl.textContent = 'Total';
  const val = svgEl('text', { class: 'fb__center', x: 100, y: 116, 'text-anchor': 'middle', 'font-size': 19 });
  val.textContent = currency ? brl(total || sum) : num(total || sum);
  svg.append(lbl, val);
  box.append(svg);

  const vao = (R - 11) * 2 * 0.88;
  const larg = () => {
    try { return val.getComputedTextLength(); } catch (_) { return val.textContent.length * 11; }
  };
  if (larg() > vao) {
    val.setAttribute('font-size', String(Math.max(12, Math.floor(19 * (vao / larg())))));
    if (larg() > vao) {
      const curto = compact(total || sum);
      val.textContent = currency ? `R$ ${curto}` : curto;
      val.setAttribute('font-size', '19');
      if (larg() > vao) {
        val.setAttribute('font-size', String(Math.max(12, Math.floor(19 * (vao / larg())))));
      }
    }
  }
}

function fbBars(target, { series = [], currency = true } = {}) {
  const box = fbFrame(target);
  if (!series.length) return fbEmpty(target, { message: 'Sem dados no período' });

  const W = 640; const H = 280; const PL = 56; const PB = 34; const PT = 16;
  const max = Math.max(...series.map((s) => Number(s.value) || 0), 1);
  const plotW = W - PL - 12;
  const plotH = H - PB - PT;
  const step = plotW / series.length;
  const bw = Math.min(step * 0.55, 46);
  const ink = themeInk();

  const svg = svgEl('svg', {
    class: 'fb__svg', viewBox: `0 0 ${W} ${H}`, preserveAspectRatio: 'xMidYMid meet',
    role: 'img', 'aria-label': 'Vendas por mês',
  });

  for (let i = 0; i <= 4; i += 1) {
    const y = PT + (plotH / 4) * i;
    svg.append(svgEl('line', { class: 'fb__grid', x1: PL, y1: y, x2: W - 12, y2: y }));
    const t = svgEl('text', { class: 'fb__axis', x: PL - 10, y: y + 3.5, 'text-anchor': 'end' });
    t.textContent = compact(max * (1 - i / 4));
    svg.append(t);
  }

  series.forEach((s, i) => {
    const v = Number(s.value) || 0;
    const h = Math.max((v / max) * plotH, v > 0 ? 3 : 0);
    const x = PL + step * i + (step - bw) / 2;
    const y = PT + plotH - h;
    const r = Math.min(4, bw / 2, h);

    const d = `M${x} ${y + h} L${x} ${y + r} Q${x} ${y} ${x + r} ${y} L${x + bw - r} ${y} Q${x + bw} ${y} ${x + bw} ${y + r} L${x + bw} ${y + h} Z`;
    const bar = svgEl('path', { class: 'fb__bar', d, fill: ink.accent });
    const t = svgEl('title');
    t.textContent = `${s.label}: ${currency ? brl(v) : num(v)}`;
    bar.append(t);
    svg.append(bar);

    const lab = svgEl('text', { class: 'fb__axis', x: x + bw / 2, y: H - 12, 'text-anchor': 'middle' });
    lab.textContent = s.label;
    svg.append(lab);
  });

  box.append(svg);
}

function fbLine(target, { points = [], currency = true } = {}) {
  const box = fbFrame(target);
  if (!points.length) return fbEmpty(target, { message: 'Sem dados no período' });

  const W = 640; const H = 280; const PL = 56; const PB = 34; const PT = 16;
  const max = Math.max(...points.map((p) => Number(p.value) || 0), 1);
  const plotW = W - PL - 12;
  const plotH = H - PB - PT;
  const ink = themeInk();
  const xAt = (i) => (points.length === 1 ? PL + plotW / 2 : PL + (plotW / (points.length - 1)) * i);
  const yAt = (v) => PT + plotH - ((Number(v) || 0) / max) * plotH;

  const svg = svgEl('svg', {
    class: 'fb__svg', viewBox: `0 0 ${W} ${H}`, preserveAspectRatio: 'xMidYMid meet',
    role: 'img', 'aria-label': 'Receita por mês',
  });

  for (let i = 0; i <= 4; i += 1) {
    const y = PT + (plotH / 4) * i;
    svg.append(svgEl('line', { class: 'fb__grid', x1: PL, y1: y, x2: W - 12, y2: y }));
    const t = svgEl('text', { class: 'fb__axis', x: PL - 10, y: y + 3.5, 'text-anchor': 'end' });
    t.textContent = compact(max * (1 - i / 4));
    svg.append(t);
  }

  const gid = `fbgrad-${Math.random().toString(36).slice(2, 8)}`;
  const defs = svgEl('defs');
  const grad = svgEl('linearGradient', { id: gid, x1: 0, y1: 0, x2: 0, y2: 1 });
  grad.append(
    svgEl('stop', { offset: '0%', 'stop-color': ink.accent, 'stop-opacity': '.34' }),
    svgEl('stop', { offset: '100%', 'stop-color': ink.accent, 'stop-opacity': '0' }),
  );
  defs.append(grad);
  svg.append(defs);

  const line = points.map((p, i) => `${i ? 'L' : 'M'}${xAt(i)} ${yAt(p.value)}`).join(' ');
  svg.append(svgEl('path', {
    d: `${line} L${xAt(points.length - 1)} ${PT + plotH} L${xAt(0)} ${PT + plotH} Z`,
    fill: `url(#${gid})`, stroke: 'none',
  }));
  svg.append(svgEl('path', { d: line, fill: 'none', stroke: ink.accent, 'stroke-width': 2, 'stroke-linecap': 'round', 'stroke-linejoin': 'round' }));

  points.forEach((p, i) => {
    const dot = svgEl('circle', { cx: xAt(i), cy: yAt(p.value), r: 4.5, fill: ink.accent, stroke: 'var(--panel)', 'stroke-width': 2 });
    const t = svgEl('title');
    t.textContent = `${p.label}: ${currency ? brl(p.value) : num(p.value)}`;
    dot.append(t);
    svg.append(dot);

    if (points.length <= 12) {
      const lab = svgEl('text', { class: 'fb__axis', x: xAt(i), y: H - 12, 'text-anchor': 'middle' });
      lab.textContent = p.label;
      svg.append(lab);
    }
  });

  box.append(svg);
}

function fbEmpty(target, { message = 'Sem dados', icon: iconName = 'inbox' } = {}) {
  clear(target);
  target.append(emptyState({ title: message, iconName }));
}

function fbDestroy(target) {
  clear(target);
}

export const chartsFallback = {
  donut: fbDonut,
  bars: fbBars,
  line: fbLine,
  empty: fbEmpty,
  destroy: fbDestroy,
};

export const sceneFallback = {
  mount() {  },
  unmount() {  },
};
