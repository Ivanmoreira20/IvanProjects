const tasks = new Set();
let rafId = 0;
let lastT = 0;

function loop(t) {
  rafId = requestAnimationFrame(loop);

  const dt = lastT ? Math.min((t - lastT) / 1000, 1 / 20) : 1 / 60;
  lastT = t;
  for (const fn of tasks) {
    try {
      fn(dt, t);
    } catch (err) {

      console.error('[helios] task error', err);
      tasks.delete(fn);
    }
  }
  if (!tasks.size) stopLoop();
}

function startLoop() {
  if (rafId || document.hidden || !tasks.size) return;
  lastT = 0;
  rafId = requestAnimationFrame(loop);
}

function stopLoop() {
  if (!rafId) return;
  cancelAnimationFrame(rafId);
  rafId = 0;
  lastT = 0;
}

export function addTask(fn) {
  tasks.add(fn);
  startLoop();
}

export function removeTask(fn) {
  tasks.delete(fn);
  if (!tasks.size) stopLoop();
}

document.addEventListener('visibilitychange', () => {
  if (document.hidden) stopLoop();
  else startLoop();
});

const mqReduce =
  typeof matchMedia === 'function'
    ? matchMedia('(prefers-reduced-motion: reduce)')
    : null;

const reduceListeners = new Set();

export function prefersReducedMotion() {
  return !!(mqReduce && mqReduce.matches);
}

export function onReducedMotionChange(fn) {
  reduceListeners.add(fn);
  return () => reduceListeners.delete(fn);
}

if (mqReduce) {
  const emit = () => reduceListeners.forEach((f) => f(mqReduce.matches));
  if (mqReduce.addEventListener) mqReduce.addEventListener('change', emit);
  else if (mqReduce.addListener) mqReduce.addListener(emit);
}

export const isCoarsePointer =
  typeof matchMedia === 'function' ? matchMedia('(pointer: coarse)').matches : false;

let webglOk = null;

export function hasWebGL() {
  if (webglOk !== null) return webglOk;
  try {
    const c = document.createElement('canvas');
    const gl =
      c.getContext('webgl2') ||
      c.getContext('webgl') ||
      c.getContext('experimental-webgl');
    webglOk = !!(gl && typeof gl.getParameter === 'function');

    if (gl) {
      const lose = gl.getExtension('WEBGL_lose_context');
      if (lose) lose.loseContext();
    }
  } catch (_) {
    webglOk = false;
  }
  return webglOk;
}

const MAX_GL_CONTEXTS = 6;
let liveContexts = 0;

export function canOpenContext() {
  return hasWebGL() && liveContexts < MAX_GL_CONTEXTS;
}
export function acquireContext() {
  liveContexts++;
}
export function releaseContext() {
  liveContexts = Math.max(0, liveContexts - 1);
}

export function dpr() {
  return Math.min(window.devicePixelRatio || 1, 2);
}

const FALLBACK_DARK = {
  accent: '#8B5CF6',
  accent2: '#E879F9',
  accent3: '#22D3EE',
  textPrimary: '#EDE9FE',
  textSecondary: '#A1A1AA',
  textMuted: '#71717A',
  surface: '#0B0A0F',
  border: '#2A2438',
  good: '#34D399',
  danger: '#FB7185',
};

const FALLBACK_LIGHT = {
  accent: '#7C3AED',
  accent2: '#C026D3',
  accent3: '#0891B2',
  textPrimary: '#1E1B2E',
  textSecondary: '#57536E',
  textMuted: '#8B879E',
  surface: '#FFFFFF',
  border: '#E4E1EC',
  good: '#059669',
  danger: '#E11D48',
};

const TOKEN_ALIASES = {
  accent: ['--accent', '--primary-color', '--accent-1', '--violet', '--brand'],
  accent2: [
    '--accent-2',
    '--accent-secondary',
    '--magenta',
    '--primary-light',
    '--accent-alt',
  ],
  accent3: ['--accent-3', '--cyan', '--info-color', '--accent-tertiary'],
  textPrimary: ['--text-primary', '--text', '--fg'],
  textSecondary: ['--text-secondary', '--text-2', '--muted-foreground'],
  textMuted: ['--text-muted', '--text-3', '--text-dim'],
  surface: ['--bg-card', '--surface', '--card', '--bg-elevated', '--bg-matte', '--bg-dark'],
  border: ['--border-color', '--border', '--hairline', '--border-light'],
  good: ['--success-color', '--good', '--positive', '--success'],
  danger: ['--danger-color', '--danger', '--negative', '--error-color'],
};

let colorProbe = null;

export function normalizeColor(str, fallback = '#8B5CF6') {
  const s = String(str || '').trim();
  if (!s) return fallback;
  try {
    if (!colorProbe) colorProbe = document.createElement('canvas').getContext('2d');
    colorProbe.fillStyle = '#000000';
    colorProbe.fillStyle = s;
    const out = colorProbe.fillStyle;

    if (typeof out === 'string' && out.startsWith('rgba')) {
      const m = out.match(/rgba?\(([^)]+)\)/);
      if (m) {
        const p = m[1].split(',').map((v) => parseFloat(v));
        return `rgb(${p[0] | 0}, ${p[1] | 0}, ${p[2] | 0})`;
      }
    }
    return out || fallback;
  } catch (_) {
    return fallback;
  }
}

export function isLightTheme() {
  return document.body ? document.body.classList.contains('light-theme') : false;
}

let cachedTokens = null;

export function tokens() {
  if (cachedTokens) return cachedTokens;
  const light = isLightTheme();
  const base = light ? FALLBACK_LIGHT : FALLBACK_DARK;
  const out = { light };
  let csBody = null;
  let csRoot = null;
  try {
    csBody = document.body ? getComputedStyle(document.body) : null;
    csRoot = getComputedStyle(document.documentElement);
  } catch (_) {

  }
  const read = (name) => {
    let v = csBody ? csBody.getPropertyValue(name) : '';
    if (!v || !v.trim()) v = csRoot ? csRoot.getPropertyValue(name) : '';
    return v && v.trim() ? v.trim() : '';
  };
  for (const key of Object.keys(TOKEN_ALIASES)) {
    let found = '';
    for (const alias of TOKEN_ALIASES[key]) {
      const v = read(alias);
      if (v) {
        found = v;
        break;
      }
    }
    out[key] = normalizeColor(found || base[key], base[key]);
  }
  cachedTokens = out;
  return out;
}

const themeListeners = new Set();
let themeQueued = false;

function flushTheme() {
  themeQueued = false;
  cachedTokens = null;
  const t = tokens();
  themeListeners.forEach((fn) => {
    try {
      fn(t);
    } catch (err) {
      console.error('[helios] theme listener', err);
    }
  });
}

export function onThemeChange(fn) {
  themeListeners.add(fn);
  return () => themeListeners.delete(fn);
}

if (typeof MutationObserver === 'function') {
  const startObserver = () => {
    if (!document.body) return;
    new MutationObserver(() => {
      if (themeQueued) return;
      themeQueued = true;
      requestAnimationFrame(flushTheme);
    }).observe(document.body, { attributes: true, attributeFilter: ['class'] });

    new MutationObserver(() => {
      if (themeQueued) return;
      themeQueued = true;
      requestAnimationFrame(flushTheme);
    }).observe(document.documentElement, {
      attributes: true,
      attributeFilter: ['class', 'data-theme'],
    });
  };
  if (document.body) startObserver();
  else document.addEventListener('DOMContentLoaded', startObserver, { once: true });
}

export class Spring {
  constructor(value = 0, { response = 0.4, damping = 1, velocity = 0 } = {}) {
    this.value = value;
    this.target = value;
    this.velocity = velocity;
    this.response = response;
    this.damping = damping;
    this.epsilon = 1e-4;
  }

  set(v) {
    this.value = v;
    this.target = v;
    this.velocity = 0;
    return this;
  }

  to(target, opts) {
    this.target = target;
    if (opts) {
      if (opts.response != null) this.response = opts.response;
      if (opts.damping != null) this.damping = opts.damping;
      if (opts.velocity != null) this.velocity = opts.velocity;
    }
    return this;
  }

  get settled() {
    return (
      Math.abs(this.value - this.target) < this.epsilon &&
      Math.abs(this.velocity) < this.epsilon
    );
  }

  step(dt) {
    if (this.settled) {
      this.value = this.target;
      this.velocity = 0;
      return false;
    }
    const w = (2 * Math.PI) / Math.max(this.response, 1e-3);
    const k = w * w;
    const c = 2 * this.damping * w;

    let remaining = Math.min(dt, 1 / 20);
    const h = 1 / 240;
    while (remaining > 0) {
      const s = Math.min(h, remaining);
      remaining -= s;
      const a = -k * (this.value - this.target) - c * this.velocity;
      this.velocity += a * s;
      this.value += this.velocity * s;
    }
    if (this.settled) {
      this.value = this.target;
      this.velocity = 0;
      return false;
    }
    return true;
  }
}

export function disposeObject(root) {
  if (!root) return;
  const seenGeo = new Set();
  const seenMat = new Set();
  root.traverse((obj) => {
    if (obj.geometry && !seenGeo.has(obj.geometry)) {
      seenGeo.add(obj.geometry);
      obj.geometry.dispose();
    }
    const mats = Array.isArray(obj.material) ? obj.material : obj.material ? [obj.material] : [];
    for (const m of mats) {
      if (!m || seenMat.has(m)) continue;
      seenMat.add(m);
      for (const key of Object.keys(m)) {
        const v = m[key];
        if (v && v.isTexture && !v.__heliosShared) v.dispose();
      }
      m.dispose();
    }
  });
  if (root.parent) root.parent.remove(root);
  root.clear && root.clear();
}

export function killRenderer(renderer) {
  if (!renderer) return;
  try {
    renderer.dispose();
    renderer.forceContextLoss && renderer.forceContextLoss();
  } catch (_) {

  }
  const el = renderer.domElement;
  if (el && el.parentNode) el.parentNode.removeChild(el);
  releaseContext();
}

const texCache = new Map();

export function radialTexture(THREE, key = 'soft', stops = null) {
  const id = 'radial:' + key;
  if (texCache.has(id)) return texCache.get(id);
  const size = 128;
  const c = document.createElement('canvas');
  c.width = c.height = size;
  const g = c.getContext('2d');
  const grad = g.createRadialGradient(size / 2, size / 2, 0, size / 2, size / 2, size / 2);
  const list = stops || [
    [0, 'rgba(255,255,255,1)'],
    [0.25, 'rgba(255,255,255,0.55)'],
    [0.55, 'rgba(255,255,255,0.14)'],
    [1, 'rgba(255,255,255,0)'],
  ];
  for (const [p, col] of list) grad.addColorStop(p, col);
  g.fillStyle = grad;
  g.fillRect(0, 0, size, size);
  const tex = new THREE.CanvasTexture(c);
  tex.colorSpace = THREE.SRGBColorSpace;
  tex.__heliosShared = true;
  texCache.set(id, tex);
  return tex;
}

export function disposeSharedTextures() {
  for (const t of texCache.values()) {
    t.__heliosShared = false;
    t.dispose();
  }
  texCache.clear();
}

const LOCALE = (document.documentElement.lang || navigator.language || 'pt-BR').trim();

const DEFAULT_CURRENCY = 'BRL';

const fmtCache = new Map();

function nf(opts) {
  const key = JSON.stringify(opts);
  if (!fmtCache.has(key)) {
    try {
      fmtCache.set(key, new Intl.NumberFormat(LOCALE, opts));
    } catch (_) {
      fmtCache.set(key, new Intl.NumberFormat('pt-BR', { maximumFractionDigits: 2 }));
    }
  }
  return fmtCache.get(key);
}

export function formatValue(value, currency, { compact = false } = {}) {
  const v = Number(value);
  if (!isFinite(v)) return '—';

  const cur = currency === true ? DEFAULT_CURRENCY : currency;

  const isIso = typeof cur === 'string' && /^[A-Za-z]{3}$/.test(cur.trim());
  const base = compact
    ? { notation: 'compact', maximumFractionDigits: 1, minimumFractionDigits: 0 }
    : { maximumFractionDigits: v % 1 === 0 ? 0 : 2, minimumFractionDigits: 0 };
  if (isIso) {
    try {
      return nf({ style: 'currency', currency: cur.trim().toUpperCase(), ...base }).format(v);
    } catch (_) {

    }
  }
  const text = nf(base).format(v);
  if (cur && !isIso) return `${String(cur).trim()} ${text}`;
  return text;
}

export function formatPercent(part, whole) {
  if (!whole) return '0%';
  const p = (part / whole) * 100;
  return nf({ maximumFractionDigits: p < 10 ? 1 : 0 }).format(p) + '%';
}

export const clamp = (v, a, b) => (v < a ? a : v > b ? b : v);
export const lerp = (a, b, t) => a + (b - a) * t;

export function css(el, styles) {
  for (const k in styles) {
    const v = styles[k];
    if (v == null) continue;
    if (k.startsWith('--')) el.style.setProperty(k, String(v));
    else el.style[k] = typeof v === 'number' && k !== 'zIndex' && k !== 'opacity' ? v + 'px' : v;
  }
  return el;
}

export function el(tag, styles, parent) {
  const node = document.createElement(tag);
  if (styles) css(node, styles);
  if (parent) parent.appendChild(node);
  return node;
}

export const FONT_STACK = 'inherit';
