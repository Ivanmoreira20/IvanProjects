import * as THREE from '../vendor/three.module.min.js';
import {
  addTask,
  removeTask,
  Spring,
  tokens,
  onThemeChange,
  prefersReducedMotion,
  onReducedMotionChange,
  hasWebGL,
  canOpenContext,
  acquireContext,
  killRenderer,
  disposeObject,
  dpr,
  clamp,
  formatValue,
  formatPercent,
  css,
  el as mkEl,
  isCoarsePointer,
} from '../vendor/helios-core.js';
import { makeRingSector, makeChamferBar, makeGlowFloor } from '../vendor/helios-geometry.js';

const instances = new WeakMap();

const TILT = 0.368;
const BAR_AZ = -0.38;
const BAR_EL = 0.30;
const LINE_AZ = -0.20;
const LINE_EL = 0.26;

const HIT_SLOP = 26;

const MIN_CENTRO = 13;

function buildSurface(el, { legend = false } = {}) {
  if (getComputedStyle(el).position === 'static') el.style.position = 'relative';

  const root = mkEl('div', {
    position: 'absolute',
    inset: '0',
    display: 'flex',
    gap: '12px',
    alignItems: 'stretch',
    minWidth: 0,
    minHeight: 0,
  });
  root.className = 'h3d-root';

  const plot = mkEl(
    'div',
    { position: 'relative', flex: '1 1 auto', minWidth: 0, minHeight: 0, overflow: 'hidden' },
    root
  );
  plot.className = 'h3d-plot';

  const labels = mkEl('div', { position: 'absolute', inset: '0', pointerEvents: 'none' }, plot);
  labels.className = 'h3d-labels';

  const tip = mkEl(
    'div',
    {
      position: 'absolute',
      top: '0',
      left: '0',
      pointerEvents: 'none',
      opacity: '0',
      transform: 'translate3d(-9999px,-9999px,0) scale(.96)',
      transformOrigin: 'center bottom',
      padding: '8px 11px',
      borderRadius: '12px',
      fontSize: '12px',
      lineHeight: '1.35',
      whiteSpace: 'nowrap',
      zIndex: '4',
      transition: 'opacity 140ms cubic-bezier(.23,1,.32,1), scale 140ms cubic-bezier(.23,1,.32,1)',
      backdropFilter: 'blur(14px) saturate(160%)',
      webkitBackdropFilter: 'blur(14px) saturate(160%)',
    },
    plot
  );
  tip.className = 'h3d-tip';
  tip.setAttribute('role', 'status');

  let legendEl = null;
  if (legend) {
    legendEl = mkEl(
      'div',
      { flex: '0 0 auto', display: 'flex', minWidth: 0, minHeight: 0, overflow: 'hidden' },
      root
    );
    legendEl.className = 'h3d-legend';
  }

  el.appendChild(root);
  return { root, plot, labels, tip, legend: legendEl };
}

function buildSrTable(rows, headers) {
  const t = mkEl('table', {
    position: 'absolute',
    width: '1px',
    height: '1px',
    overflow: 'hidden',
    clipPath: 'inset(50%)',
    whiteSpace: 'nowrap',
    border: '0',
    padding: '0',
    margin: '-1px',
  });
  const thead = mkEl('thead', null, t);
  const htr = mkEl('tr', null, thead);
  headers.forEach((h) => (mkEl('th', null, htr).textContent = h));
  const tbody = mkEl('tbody', null, t);
  rows.forEach((r) => {
    const tr = mkEl('tr', null, tbody);
    r.forEach((c) => (mkEl('td', null, tr).textContent = c));
  });
  return t;
}

function makeLabel(parent, kind) {
  const n = mkEl(
    'div',
    {
      position: 'absolute',
      top: '0',
      left: '0',
      pointerEvents: 'none',
      willChange: 'transform',
      whiteSpace: 'nowrap',
      transform: 'translate3d(-9999px,-9999px,0)',
    },
    parent
  );
  n.className = 'h3d-label h3d-label--' + kind;
  return n;
}

function place(node, x, y, extra = '') {
  node.style.transform = `translate3d(${Math.round(x)}px, ${Math.round(y)}px, 0) ${extra}`;
}

function sanitize(list, valueKey) {
  const out = [];
  for (const raw of list || []) {
    if (!raw) continue;
    const v = Number(raw[valueKey]);
    out.push({
      label: raw.label == null ? '' : String(raw.label),
      value: isFinite(v) ? v : 0,
      color: raw.color,
      raw,
    });
  }
  return out;
}

function niceTicks(max, count = 4) {
  if (!isFinite(max) || max <= 0) return { ticks: [0], top: 1 };
  const rough = max / count;
  const mag = Math.pow(10, Math.floor(Math.log10(rough)));
  const norm = rough / mag;
  const step = (norm <= 1 ? 1 : norm <= 2 ? 2 : norm <= 5 ? 5 : 10) * mag;
  const top = Math.ceil(max / step) * step;
  const ticks = [];
  for (let v = 0; v <= top + step * 0.5; v += step) ticks.push(Number(v.toFixed(10)));
  return { ticks, top };
}

function thinIndices(total, maxLabels) {
  if (total <= maxLabels) return Array.from({ length: total }, (_, i) => i);
  const out = new Set([0, total - 1]);
  const step = (total - 1) / (maxLabels - 1);
  for (let i = 1; i < maxLabels - 1; i++) out.add(Math.round(i * step));
  return [...out].sort((a, b) => a - b);
}

function monotoneSample(ys, perSegment) {
  const n = ys.length;
  if (n < 2) return ys.slice();
  const d = [];
  for (let i = 0; i < n - 1; i++) d.push(ys[i + 1] - ys[i]);
  const m = new Array(n);
  m[0] = d[0];
  m[n - 1] = d[n - 2];
  for (let i = 1; i < n - 1; i++) {
    if (d[i - 1] * d[i] <= 0) m[i] = 0;
    else m[i] = (d[i - 1] + d[i]) / 2;
  }
  for (let i = 0; i < n - 1; i++) {
    if (d[i] === 0) {
      m[i] = 0;
      m[i + 1] = 0;
      continue;
    }
    const a = m[i] / d[i];
    const b = m[i + 1] / d[i];
    const s = Math.hypot(a, b);
    if (s > 3) {
      m[i] = ((3 / s) * a) * d[i];
      m[i + 1] = ((3 / s) * b) * d[i];
    }
  }
  const out = [];
  for (let i = 0; i < n - 1; i++) {
    for (let k = 0; k < perSegment; k++) {
      const t = k / perSegment;
      const t2 = t * t;
      const t3 = t2 * t;
      const h00 = 2 * t3 - 3 * t2 + 1;
      const h10 = t3 - 2 * t2 + t;
      const h01 = -2 * t3 + 3 * t2;
      const h11 = t3 - t2;
      out.push(h00 * ys[i] + h10 * m[i] + h01 * ys[i + 1] + h11 * m[i + 1]);
    }
  }
  out.push(ys[n - 1]);
  return out;
}

function mixHex(a, b, t) {
  const ca = new THREE.Color(a);
  const cb = new THREE.Color(b);
  return ca.lerp(cb, t).getStyle();
}

class GLBase {
  constructor(plot) {
    this.plot = plot;
    this.ok = false;
    if (!canOpenContext()) return;
    try {
      this.renderer = new THREE.WebGLRenderer({
        antialias: true,
        alpha: true,
        powerPreference: 'high-performance',
        failIfMajorPerformanceCaveat: false,
      });
    } catch (_) {
      return;
    }
    acquireContext();
    this.ok = true;
    this.renderer.setPixelRatio(dpr());

    this.renderer.toneMapping = THREE.NoToneMapping;
    this.renderer.outputColorSpace = THREE.SRGBColorSpace;
    this.renderer.setClearAlpha(0);
    const c = this.renderer.domElement;
    css(c, { position: 'absolute', inset: '0', width: '100%', height: '100%', display: 'block' });
    plot.insertBefore(c, plot.firstChild);

    this.scene = new THREE.Scene();
    this.camera = new THREE.OrthographicCamera(-1, 1, 1, -1, -100, 100);
    this.raycaster = new THREE.Raycaster();
    this.w = 0;
    this.h = 0;
    this.dirty = true;

    this.onLost = (e) => {

      e.preventDefault();
      this.contextLost = true;
      if (this.onContextLost) this.onContextLost();
    };
    c.addEventListener('webglcontextlost', this.onLost, false);
  }

  setSize(w, h) {
    if (!this.ok || w <= 0 || h <= 0) return false;
    this.w = w;
    this.h = h;
    this.renderer.setPixelRatio(dpr());
    this.renderer.setSize(w, h, false);
    this.dirty = true;
    return true;
  }

  project(v3) {
    const p = v3.clone().project(this.camera);
    return { x: ((p.x + 1) / 2) * this.w, y: ((1 - p.y) / 2) * this.h };
  }

  ndc(x, y) {
    return new THREE.Vector2((x / this.w) * 2 - 1, -((y / this.h) * 2 - 1));
  }

  addLights(t) {
    const key = new THREE.DirectionalLight(0xffffff, t.light ? 2.2 : 2.6);
    key.position.set(-2.4, 4.2, 3.6);
    const fill = new THREE.DirectionalLight(new THREE.Color(t.accent), t.light ? 0.5 : 0.95);
    fill.position.set(3.2, -1.2, 2.2);
    const rim = new THREE.DirectionalLight(new THREE.Color(t.accent2), t.light ? 0.45 : 1.15);
    rim.position.set(1.6, 2.0, -4.0);
    const amb = new THREE.AmbientLight(0xffffff, t.light ? 1.25 : 0.55);
    const hemi = new THREE.HemisphereLight(
      new THREE.Color(t.accent2),
      new THREE.Color(t.accent),
      t.light ? 0.35 : 0.5
    );
    this.scene.add(key, fill, rim, amb, hemi);
    this.lights = { key, fill, rim, amb, hemi };
  }

  recolorLights(t) {
    if (!this.lights) return;
    this.lights.key.intensity = t.light ? 2.2 : 2.6;
    this.lights.fill.color.set(t.accent);
    this.lights.fill.intensity = t.light ? 0.5 : 0.95;
    this.lights.rim.color.set(t.accent2);
    this.lights.rim.intensity = t.light ? 0.45 : 1.15;
    this.lights.amb.intensity = t.light ? 1.25 : 0.55;
    this.lights.hemi.color.set(t.accent2);
    this.lights.hemi.groundColor.set(t.accent);
    this.lights.hemi.intensity = t.light ? 0.35 : 0.5;
    this.dirty = true;
  }

  render() {
    if (!this.ok || this.contextLost || this.w <= 0 || this.h <= 0) return;
    this.renderer.render(this.scene, this.camera);
  }

  dispose() {
    if (!this.ok) return;
    this.renderer.domElement.removeEventListener('webglcontextlost', this.onLost);
    disposeObject(this.scene);
    killRenderer(this.renderer);
    this.ok = false;
  }
}

class C2DBase {
  constructor(plot) {
    this.plot = plot;
    this.canvas = mkEl(
      'canvas',
      { position: 'absolute', inset: '0', width: '100%', height: '100%', display: 'block' },
      null
    );
    plot.insertBefore(this.canvas, plot.firstChild);
    this.ctx = this.canvas.getContext('2d');
    this.w = 0;
    this.h = 0;
    this.ok = !!this.ctx;
  }
  setSize(w, h) {
    if (w <= 0 || h <= 0) return false;
    const r = dpr();
    this.w = w;
    this.h = h;
    this.canvas.width = Math.round(w * r);
    this.canvas.height = Math.round(h * r);
    this.ctx.setTransform(r, 0, 0, r, 0, 0);
    return true;
  }
  clear() {
    this.ctx.save();
    this.ctx.setTransform(1, 0, 0, 1, 0, 0);
    this.ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);
    this.ctx.restore();
  }
  dispose() {
    if (this.canvas.parentNode) this.canvas.parentNode.removeChild(this.canvas);
    this.canvas.width = this.canvas.height = 0;
  }
}

class DonutGL extends GLBase {
  constructor(plot, data) {
    super(plot);
    if (!this.ok) return;
    this.data = data;
    const t = tokens();
    this.addLights(t);

    this.group = new THREE.Group();
    this.group.rotation.x = -TILT;
    this.scene.add(this.group);

    const OUTER = 1.0;
    const INNER = 0.60;
    const DEPTH = 0.30;
    this.OUTER = OUTER;
    this.INNER = INNER;

    this.slices = data.slices.map((s, i) => {
      const geo = makeRingSector(THREE, {
        inner: INNER,
        outer: OUTER,
        height: DEPTH,
        chamfer: 0.035,
      });
      const mat = new THREE.MeshPhysicalMaterial({
        color: new THREE.Color(s.color),
        roughness: 0.34,
        metalness: 0.08,
        clearcoat: 0.85,
        clearcoatRoughness: 0.22,
        emissive: new THREE.Color(s.color),
        emissiveIntensity: 0,
        sheen: 0.25,
        sheenColor: new THREE.Color(t.accent2),
      });
      const mesh = new THREE.Mesh(geo, mat);
      mesh.userData.index = i;
      this.group.add(mesh);
      return {
        mesh,
        geo,
        mat,
        base: new THREE.Color(s.color),
        theta: new Spring(0, { response: 0.62, damping: 0.86 }),
        pop: new Spring(0, { response: 0.34, damping: 0.78 }),
        lift: new Spring(0, { response: 0.3, damping: 1 }),
        thetaStart: s.thetaStart,
        thetaLen: s.thetaLen,
        mid: s.thetaStart + s.thetaLen / 2,
        lastTheta: -1,
      };
    });

    this.halo = new THREE.Mesh(
      new THREE.PlaneGeometry(OUTER * 3.4, OUTER * 3.4),
      new THREE.MeshBasicMaterial({
        map: makeHalo(t),
        transparent: true,
        depthWrite: false,
        blending: t.light ? THREE.NormalBlending : THREE.AdditiveBlending,
        opacity: t.light ? 0.3 : 0.62,
      })
    );
    this.halo.position.z = -0.5;
    this.group.add(this.halo);

    const reduced = prefersReducedMotion();
    this.slices.forEach((s, i) => {
      if (reduced) s.theta.set(s.thetaLen);
      else {
        s.theta.value = 0.0001;
        s.delay = i * 0.055;
        s.elapsed = 0;
      }
    });
    this.reduced = reduced;
    this.hover = -1;
  }

  layout() {
    if (!this.ok) return;

    const pad = 1.14;
    const halfW = this.OUTER * pad;
    const halfH = (this.OUTER * Math.cos(TILT) + 0.3 * Math.sin(TILT)) * pad;
    const aspect = this.w / Math.max(this.h, 1);
    let vw = halfW;
    let vh = halfH;
    if (halfW / halfH > aspect) vh = halfW / aspect;
    else vw = halfH * aspect;
    const c = this.camera;
    c.left = -vw;
    c.right = vw;
    c.top = vh;
    c.bottom = -vh;
    c.position.set(0, 0, 10);
    c.lookAt(0, 0, 0);
    c.updateProjectionMatrix();
    this.dirty = true;
  }

  frame(dt) {
    if (!this.ok) return false;
    let moving = false;
    for (const s of this.slices) {
      if (!this.reduced && s.delay != null) {
        s.elapsed += dt;
        if (s.elapsed >= s.delay) {
          s.theta.to(s.thetaLen);
          s.delay = null;
        } else moving = true;
      }
      if (s.theta.step(dt)) moving = true;
      if (s.pop.step(dt)) moving = true;
      if (s.lift.step(dt)) moving = true;

      const th = s.theta.value;
      if (Math.abs(th - s.lastTheta) > 0.0012) {
        s.geo.updateSector(s.thetaStart, Math.max(th, 0.0006));
        s.lastTheta = th;
        this.dirty = true;
      }
      const p = s.pop.value;
      if (p > 0.0005 || s.mesh.position.lengthSq() > 0) {
        s.mesh.position.set(Math.cos(s.mid) * p * 0.075, Math.sin(s.mid) * p * 0.075, p * 0.05);
        this.dirty = true;
      }
      const li = s.lift.value;
      if (Math.abs(s.mat.emissiveIntensity - li * 0.4) > 0.002) {
        s.mat.emissiveIntensity = li * 0.4;
        this.dirty = true;
      }
    }
    if (this.dirty) {
      this.render();
      this.dirty = false;
    }
    return moving;
  }

  hit(x, y) {
    if (!this.ok) return -1;
    this.raycaster.setFromCamera(this.ndc(x, y), this.camera);
    const hits = this.raycaster.intersectObjects(
      this.slices.map((s) => s.mesh),
      false
    );
    return hits.length ? hits[0].object.userData.index : -1;
  }

  setHover(i) {
    if (i === this.hover) return;
    this.hover = i;
    this.slices.forEach((s, k) => {
      s.pop.to(k === i ? 1 : 0);
      s.lift.to(k === i ? 1 : 0);
    });
    this.dirty = true;
  }

  centerPx() {
    return this.project(new THREE.Vector3(0, 0, 0.15));
  }

  holePx() {
    const a = this.project(new THREE.Vector3(-this.INNER, 0, 0.15));
    const b = this.project(new THREE.Vector3(this.INNER, 0, 0.15));
    return Math.abs(b.x - a.x);
  }

  recolor(t) {
    this.recolorLights(t);
    this.slices.forEach((s) => {
      s.mat.sheenColor.set(t.accent2);
    });
    if (this.halo) {
      const m = this.halo.material;
      if (m.map) m.map.dispose();
      m.map = makeHalo(t);
      m.blending = t.light ? THREE.NormalBlending : THREE.AdditiveBlending;
      m.opacity = t.light ? 0.3 : 0.62;
      m.needsUpdate = true;
    }
    this.dirty = true;
  }
}

function makeHalo(t) {
  const size = 256;
  const c = document.createElement('canvas');
  c.width = c.height = size;
  const g = c.getContext('2d');
  const grad = g.createRadialGradient(size / 2, size / 2, size * 0.12, size / 2, size / 2, size / 2);
  const a = new THREE.Color(t.accent).getStyle();
  const b = new THREE.Color(t.accent2).getStyle();
  grad.addColorStop(0, b.replace('rgb(', 'rgba(').replace(')', ',0.55)'));
  grad.addColorStop(0.42, a.replace('rgb(', 'rgba(').replace(')', ',0.22)'));
  grad.addColorStop(1, a.replace('rgb(', 'rgba(').replace(')', ',0)'));
  g.fillStyle = grad;
  g.fillRect(0, 0, size, size);
  const tex = new THREE.CanvasTexture(c);
  tex.colorSpace = THREE.SRGBColorSpace;
  return tex;
}

class Donut2D extends C2DBase {
  constructor(plot, data) {
    super(plot);
    this.data = data;
    this.hover = -1;
    this.reduced = prefersReducedMotion();
    this.grow = new Spring(this.reduced ? 1 : 0, { response: 0.7, damping: 0.9 });
    if (!this.reduced) this.grow.to(1);
    this.dirty = true;
  }
  layout() {
    this.dirty = true;
  }
  geom() {
    const cx = this.w / 2;
    const cy = this.h / 2;
    const R = Math.min(this.w, this.h * 1.6) * 0.42;
    return { cx, cy, R: Math.max(R, 10), r: Math.max(R * 0.6, 6), depth: Math.max(R * 0.13, 4) };
  }
  frame(dt) {
    const moving = this.grow.step(dt);
    if (!moving && !this.dirty) return false;
    this.dirty = false;
    this.draw();
    return moving;
  }
  draw() {
    const ctx = this.ctx;
    const t = tokens();
    this.clear();
    const { cx, cy, R, r, depth } = this.geom();
    const g = this.grow.value;
    const ring = (sl, radius, inner, oy, alphaMul, popOut) => {
      const a0 = sl.thetaStart;
      const a1 = sl.thetaStart + sl.thetaLen * g;
      if (a1 - a0 < 0.0008) return;
      const mid = (a0 + a1) / 2;
      const ox = popOut ? Math.cos(mid) * 6 : 0;
      const oyy = popOut ? Math.sin(mid) * 6 : 0;
      ctx.globalAlpha = alphaMul;
      ctx.beginPath();
      ctx.arc(cx + ox, cy + oy + oyy, radius, a0, a1, false);
      ctx.arc(cx + ox, cy + oy + oyy, inner, a1, a0, true);
      ctx.closePath();
      ctx.fill();
    };

    this.data.slices.forEach((sl, i) => {
      const dark = mixHex(sl.color, '#000000', 0.55);
      for (let k = depth; k > 0; k -= 1.5) {
        ctx.fillStyle = dark;
        ring(sl, R, r, k, 0.5, i === this.hover);
      }
    });

    this.data.slices.forEach((sl, i) => {
      const grad = ctx.createLinearGradient(cx - R, cy - R, cx + R, cy + R);
      grad.addColorStop(0, mixHex(sl.color, '#ffffff', 0.24));
      grad.addColorStop(1, mixHex(sl.color, '#000000', 0.12));
      ctx.fillStyle = grad;
      ring(sl, R, r, 0, 1, i === this.hover);
      if (i === this.hover) {
        ctx.strokeStyle = t.textPrimary;
        ctx.globalAlpha = 0.5;
        ctx.lineWidth = 1.5;
        ctx.stroke();
      }
    });
    ctx.globalAlpha = 1;
  }
  hit(x, y) {
    const { cx, cy, R, r } = this.geom();
    const dx = x - cx;
    const dy = y - cy;
    const d = Math.hypot(dx, dy);
    if (d < r - 4 || d > R + 6) return -1;
    let a = Math.atan2(dy, dx);
    const slices = this.data.slices;
    for (let i = 0; i < slices.length; i++) {
      const s = slices[i];
      for (let k = -1; k <= 1; k++) {
        const aa = a + k * Math.PI * 2;
        if (aa >= s.thetaStart && aa <= s.thetaStart + s.thetaLen) return i;
      }
    }
    return -1;
  }
  setHover(i) {
    if (i === this.hover) return;
    this.hover = i;
    this.dirty = true;
  }
  centerPx() {
    const { cx, cy } = this.geom();
    return { x: cx, y: cy };
  }
  holePx() {
    return this.geom().r * 2;
  }
  recolor() {
    this.dirty = true;
  }
}

function axonCamera(camera, az, elev, halfW, halfH, cx, cy, cz) {
  const d = 40;
  camera.position.set(
    cx + Math.sin(az) * Math.cos(elev) * d,
    cy + Math.sin(elev) * d,
    cz + Math.cos(az) * Math.cos(elev) * d
  );
  camera.up.set(0, 1, 0);
  camera.lookAt(cx, cy, cz);
  camera.left = -halfW;
  camera.right = halfW;
  camera.top = halfH;
  camera.bottom = -halfH;
  camera.near = -200;
  camera.far = 200;
  camera.updateProjectionMatrix();
}

class BarsGL extends GLBase {
  constructor(plot, data) {
    super(plot);
    if (!this.ok) return;
    this.data = data;
    const t = tokens();
    this.addLights(t);

    const n = data.series.length;
    const SPAN = 10;
    const slot = SPAN / n;
    const bw = slot * 0.58;
    const bd = Math.min(bw * 1.05, 0.9);
    this.SPAN = SPAN;
    this.slot = slot;
    this.HMAX = 5.2;
    this.top = data.top;

    this.group = new THREE.Group();
    this.scene.add(this.group);

    const reduced = prefersReducedMotion();
    this.reduced = reduced;

    this.bars = data.series.map((s, i) => {
      const geo = makeChamferBar(THREE, { width: bw, depth: bd, chamfer: Math.min(bw, bd) * 0.14 });
      const c = new THREE.Color(s.color);
      const mat = new THREE.MeshPhysicalMaterial({
        color: c,
        roughness: 0.3,
        metalness: 0.1,
        clearcoat: 0.9,
        clearcoatRoughness: 0.18,
        emissive: c.clone(),
        emissiveIntensity: 0,
      });
      const mesh = new THREE.Mesh(geo, mat);
      mesh.position.x = -SPAN / 2 + slot * (i + 0.5);
      this.group.add(mesh);
      const target = (s.value / (data.top || 1)) * this.HMAX;
      const sp = new Spring(reduced ? target : 0.0001, { response: 0.58, damping: 0.82 });
      return { mesh, geo, mat, sp, target, delay: reduced ? null : i * 0.045, elapsed: 0, last: -1, lift: new Spring(0, { response: 0.28, damping: 1 }) };
    });

    this.grid = new THREE.Group();
    this.group.add(this.grid);
    this.buildGrid(t);

    this.floor = new THREE.Mesh(
      makeGlowFloor(THREE, { width: SPAN * 1.3, depth: 5.5 }),
      new THREE.MeshBasicMaterial({
        vertexColors: true,
        color: new THREE.Color(t.accent),
        transparent: true,
        opacity: t.light ? 0.1 : 0.3,
        depthWrite: false,
        blending: t.light ? THREE.NormalBlending : THREE.AdditiveBlending,
      })
    );
    this.floor.position.y = -0.005;
    this.group.add(this.floor);

    this.hover = -1;
  }

  buildGrid(t) {
    disposeObject(this.grid);
    this.grid = new THREE.Group();
    this.group.add(this.grid);
    const pts = [];
    const zBack = -2.4;
    for (const v of this.data.ticks) {
      const y = (v / (this.top || 1)) * this.HMAX;
      pts.push(-this.SPAN / 2 - 0.3, y, zBack, this.SPAN / 2 + 0.3, y, zBack);
      pts.push(-this.SPAN / 2 - 0.3, y, zBack, -this.SPAN / 2 - 0.3, y, 2.4);
    }
    const g = new THREE.BufferGeometry();
    g.setAttribute('position', new THREE.BufferAttribute(new Float32Array(pts), 3));
    const m = new THREE.LineBasicMaterial({
      color: new THREE.Color(t.border),
      transparent: true,
      opacity: t.light ? 0.85 : 0.55,
    });
    this.grid.add(new THREE.LineSegments(g, m));
  }

  layout() {
    if (!this.ok) return;
    const cy = this.HMAX * 0.5;
    const need = { w: this.SPAN * 0.62, h: this.HMAX * 0.72 };
    const aspect = this.w / Math.max(this.h, 1);
    let halfW = need.w;
    let halfH = need.h;
    if (halfW / halfH > aspect) halfH = halfW / aspect;
    else halfW = halfH * aspect;
    axonCamera(this.camera, BAR_AZ, BAR_EL, halfW, halfH, 0, cy, 0);
    this.dirty = true;
  }

  frame(dt) {
    if (!this.ok) return false;
    let moving = false;
    for (const b of this.bars) {
      if (b.delay != null) {
        b.elapsed += dt;
        if (b.elapsed >= b.delay) {
          b.sp.to(b.target);
          b.delay = null;
        } else moving = true;
      }
      if (b.sp.step(dt)) moving = true;
      if (b.lift.step(dt)) moving = true;
      if (Math.abs(b.sp.value - b.last) > 0.0015) {
        b.geo.updateHeight(Math.max(b.sp.value, 0.0008));
        b.last = b.sp.value;
        this.dirty = true;
      }
      const li = b.lift.value;
      if (Math.abs(b.mat.emissiveIntensity - li * 0.38) > 0.002) {
        b.mat.emissiveIntensity = li * 0.38;
        this.dirty = true;
      }
    }
    if (this.dirty) {
      this.render();
      this.dirty = false;
    }
    return moving;
  }

  topPx(i) {
    const b = this.bars[i];
    const v = new THREE.Vector3(b.mesh.position.x, b.sp.value, 0);
    return this.project(v);
  }
  basePx(i) {
    const b = this.bars[i];
    return this.project(new THREE.Vector3(b.mesh.position.x, 0, 1.1));
  }
  tickPx(v) {
    const y = (v / (this.top || 1)) * this.HMAX;
    return this.project(new THREE.Vector3(-this.SPAN / 2 - 0.4, y, -2.4));
  }

  hit(x) {
    let best = -1;
    let bd = Infinity;
    for (let i = 0; i < this.bars.length; i++) {
      const p = this.project(new THREE.Vector3(this.bars[i].mesh.position.x, 0.4, 0));
      const d = Math.abs(p.x - x);
      if (d < bd) {
        bd = d;
        best = i;
      }
    }
    const maxD = Math.max((this.w / this.bars.length) * 0.62, HIT_SLOP);
    return bd <= maxD ? best : -1;
  }

  setHover(i) {
    if (i === this.hover) return;
    this.hover = i;
    this.bars.forEach((b, k) => b.lift.to(k === i ? 1 : 0));
    this.dirty = true;
  }

  recolor(t) {
    this.recolorLights(t);
    this.buildGrid(t);
    if (this.floor) {
      this.floor.material.color.set(t.accent);
      this.floor.material.opacity = t.light ? 0.1 : 0.3;
      this.floor.material.blending = t.light ? THREE.NormalBlending : THREE.AdditiveBlending;
      this.floor.material.needsUpdate = true;
    }
    this.bars.forEach((b, i) => {
      const c = new THREE.Color(this.data.series[i].color);
      b.mat.color.copy(c);
      b.mat.emissive.copy(c);
    });
    this.dirty = true;
  }
}

class Bars2D extends C2DBase {
  constructor(plot, data) {
    super(plot);
    this.data = data;
    this.hover = -1;
    this.reduced = prefersReducedMotion();
    this.springs = data.series.map((s, i) => {
      const target = s.value / (data.top || 1);
      const sp = new Spring(this.reduced ? target : 0.0001, { response: 0.58, damping: 0.82 });
      return { sp, target, delay: this.reduced ? null : i * 0.045, elapsed: 0 };
    });
    this.dirty = true;
  }
  layout() {
    this.dirty = true;
  }
  metrics() {
    const padL = 46;
    const padR = 14;
    const padT = 22;
    const padB = 30;
    const iso = { dx: Math.min(this.w * 0.018, 12), dy: Math.min(this.w * 0.012, 8) };
    const plotW = Math.max(this.w - padL - padR - iso.dx, 10);
    const plotH = Math.max(this.h - padT - padB - iso.dy, 10);
    const n = this.data.series.length;
    const slot = plotW / n;
    return { padL, padR, padT, padB, iso, plotW, plotH, slot, bw: slot * 0.58, base: padT + plotH };
  }
  frame(dt) {
    let moving = false;
    for (const s of this.springs) {
      if (s.delay != null) {
        s.elapsed += dt;
        if (s.elapsed >= s.delay) {
          s.sp.to(s.target);
          s.delay = null;
        } else moving = true;
      }
      if (s.sp.step(dt)) moving = true;
    }
    if (!moving && !this.dirty) return false;
    this.dirty = false;
    this.draw();
    return moving;
  }
  draw() {
    const ctx = this.ctx;
    const t = tokens();
    this.clear();
    const m = this.metrics();

    ctx.strokeStyle = t.border;
    ctx.globalAlpha = t.light ? 0.9 : 0.55;
    ctx.lineWidth = 1;
    for (const v of this.data.ticks) {
      const y = m.base - (v / (this.data.top || 1)) * m.plotH;
      ctx.beginPath();
      ctx.moveTo(m.padL, Math.round(y) + 0.5);
      ctx.lineTo(m.padL + m.plotW + m.iso.dx, Math.round(y) + 0.5);
      ctx.stroke();
    }
    ctx.globalAlpha = 1;

    this.data.series.forEach((s, i) => {
      const h = this.springs[i].sp.value * m.plotH;
      if (h < 0.5) return;
      const x = m.padL + m.slot * i + (m.slot - m.bw) / 2;
      const y = m.base - h;
      const { dx, dy } = m.iso;
      const c = s.color;

      ctx.fillStyle = mixHex(c, '#000000', 0.42);
      ctx.beginPath();
      ctx.moveTo(x + m.bw, y);
      ctx.lineTo(x + m.bw + dx, y - dy);
      ctx.lineTo(x + m.bw + dx, m.base - dy);
      ctx.lineTo(x + m.bw, m.base);
      ctx.closePath();
      ctx.fill();

      ctx.fillStyle = mixHex(c, '#ffffff', i === this.hover ? 0.45 : 0.3);
      ctx.beginPath();
      ctx.moveTo(x, y);
      ctx.lineTo(x + dx, y - dy);
      ctx.lineTo(x + m.bw + dx, y - dy);
      ctx.lineTo(x + m.bw, y);
      ctx.closePath();
      ctx.fill();

      const g = ctx.createLinearGradient(x, y, x, m.base);
      g.addColorStop(0, mixHex(c, '#ffffff', i === this.hover ? 0.22 : 0.1));
      g.addColorStop(1, mixHex(c, '#000000', 0.2));
      ctx.fillStyle = g;
      ctx.fillRect(x, y, m.bw, h);
    });
  }
  topPx(i) {
    const m = this.metrics();
    const h = this.springs[i].sp.value * m.plotH;
    return { x: m.padL + m.slot * i + m.slot / 2 + m.iso.dx / 2, y: m.base - h - m.iso.dy };
  }
  basePx(i) {
    const m = this.metrics();
    return { x: m.padL + m.slot * i + m.slot / 2, y: m.base + 6 };
  }
  tickPx(v) {
    const m = this.metrics();
    return { x: m.padL - 8, y: m.base - (v / (this.data.top || 1)) * m.plotH };
  }
  hit(x) {
    const m = this.metrics();
    const i = Math.floor((x - m.padL) / m.slot);
    return i >= 0 && i < this.data.series.length ? i : -1;
  }
  setHover(i) {
    if (i === this.hover) return;
    this.hover = i;
    this.dirty = true;
  }
  recolor() {
    this.dirty = true;
  }
}

class LineGL extends GLBase {
  constructor(plot, data) {
    super(plot);
    if (!this.ok) return;
    this.data = data;
    const t = tokens();
    this.addLights(t);

    this.SPAN = 10;
    this.HMAX = 4.6;
    this.top = data.top;
    this.min = data.min;

    this.group = new THREE.Group();
    this.scene.add(this.group);

    const n = data.points.length;
    const xs = data.points.map((_, i) => -this.SPAN / 2 + (this.SPAN * i) / Math.max(n - 1, 1));
    const ys = data.points.map((p) => this.yOf(p.value));
    this.xs = xs;
    this.ys = ys;

    const PER = 16;
    const sy = monotoneSample(ys, PER);
    const sx = [];
    for (let i = 0; i < n - 1; i++)
      for (let k = 0; k < PER; k++) sx.push(xs[i] + ((xs[i + 1] - xs[i]) * k) / PER);
    sx.push(xs[n - 1]);
    this.curvePts = sx.map((x, i) => new THREE.Vector3(x, sy[i], 0));

    const curve = new THREE.CatmullRomCurve3(this.curvePts, false, 'centripetal', 0.0);
    const TUB = Math.max(this.curvePts.length - 1, 8);
    this.tubeGeo = new THREE.TubeGeometry(curve, TUB, 0.085, 10, false);
    const accent = new THREE.Color(t.accent);
    const accent2 = new THREE.Color(t.accent2);
    this.tubeMat = new THREE.MeshPhysicalMaterial({
      color: accent,
      roughness: 0.24,
      metalness: 0.12,
      clearcoat: 1,
      clearcoatRoughness: 0.12,
      emissive: accent2,
      emissiveIntensity: t.light ? 0.08 : 0.34,
    });
    this.tube = new THREE.Mesh(this.tubeGeo, this.tubeMat);
    this.tube.scale.z = 0.42;
    this.group.add(this.tube);
    this.tubeIndexCount = this.tubeGeo.index.count;

    this.curtain = this.buildCurtain(t);
    this.group.add(this.curtain);

    this.floor = new THREE.Mesh(
      makeGlowFloor(THREE, { width: this.SPAN * 1.35, depth: 5 }),
      new THREE.MeshBasicMaterial({
        vertexColors: true,
        color: accent.clone(),
        transparent: true,
        opacity: t.light ? 0.12 : 0.34,
        depthWrite: false,
        blending: t.light ? THREE.NormalBlending : THREE.AdditiveBlending,
      })
    );
    this.floor.position.y = -0.02;
    this.group.add(this.floor);

    this.buildGrid(t);

    const sphereGeo = new THREE.SphereGeometry(0.13, 18, 14);
    this.sphereGeo = sphereGeo;
    this.dots = data.points.map((p, i) => {
      const mat = new THREE.MeshPhysicalMaterial({
        color: new THREE.Color(p.color || t.accent2),
        roughness: 0.18,
        metalness: 0.05,
        clearcoat: 1,
        emissive: new THREE.Color(p.color || t.accent2),
        emissiveIntensity: t.light ? 0.15 : 0.5,
      });
      const mesh = new THREE.Mesh(sphereGeo, mat);
      mesh.position.set(xs[i], ys[i], 0);
      mesh.scale.setScalar(0.0001);
      this.group.add(mesh);
      return { mesh, mat, sp: new Spring(0, { response: 0.36, damping: 0.72 }) };
    });

    const dropGeo = new THREE.BufferGeometry();
    dropGeo.setAttribute('position', new THREE.BufferAttribute(new Float32Array(6), 3));
    this.drop = new THREE.Line(
      dropGeo,
      new THREE.LineBasicMaterial({
        color: new THREE.Color(t.textSecondary),
        transparent: true,
        opacity: 0,
      })
    );
    this.group.add(this.drop);

    this.reduced = prefersReducedMotion();
    this.reveal = new Spring(this.reduced ? 1 : 0, { response: 0.95, damping: 1 });
    if (!this.reduced) this.reveal.to(1);
    else this.dots.forEach((d) => d.sp.set(1));
    this.dropA = new Spring(0, { response: 0.2, damping: 1 });
    this.hover = -1;
  }

  yOf(v) {
    const range = this.top - this.min || 1;
    return ((v - this.min) / range) * this.HMAX * 0.86 + this.HMAX * 0.07;
  }

  buildCurtain(t) {
    const pts = this.curvePts;
    const n = pts.length;
    const pos = new Float32Array(n * 2 * 3);
    const uv = new Float32Array(n * 2 * 2);
    for (let i = 0; i < n; i++) {
      const p = pts[i];
      pos[i * 6] = p.x;
      pos[i * 6 + 1] = p.y;
      pos[i * 6 + 2] = 0;
      pos[i * 6 + 3] = p.x;
      pos[i * 6 + 4] = 0;
      pos[i * 6 + 5] = 0;
      uv[i * 4] = i / (n - 1);
      uv[i * 4 + 1] = 1;
      uv[i * 4 + 2] = i / (n - 1);
      uv[i * 4 + 3] = 0;
    }
    const idx = [];
    for (let i = 0; i < n - 1; i++) {
      const a = i * 2;
      idx.push(a, a + 1, a + 3, a, a + 3, a + 2);
    }
    const g = new THREE.BufferGeometry();
    g.setAttribute('position', new THREE.BufferAttribute(pos, 3));
    g.setAttribute('uv', new THREE.BufferAttribute(uv, 2));
    g.setIndex(idx);
    this.curtainGeo = g;
    this.curtainIndexCount = idx.length;
    const m = new THREE.MeshBasicMaterial({
      map: verticalFade(t),
      transparent: true,
      side: THREE.DoubleSide,
      depthWrite: false,
      opacity: t.light ? 0.4 : 0.75,
      blending: t.light ? THREE.NormalBlending : THREE.AdditiveBlending,
    });
    this.curtainMat = m;
    return new THREE.Mesh(g, m);
  }

  buildGrid(t) {
    if (this.grid) disposeObject(this.grid);
    this.grid = new THREE.Group();
    this.group.add(this.grid);
    const pts = [];
    const zBack = -2.2;
    for (const v of this.data.ticks) {
      const y = this.yOf(v);
      pts.push(-this.SPAN / 2 - 0.3, y, zBack, this.SPAN / 2 + 0.3, y, zBack);
    }
    const g = new THREE.BufferGeometry();
    g.setAttribute('position', new THREE.BufferAttribute(new Float32Array(pts), 3));
    this.grid.add(
      new THREE.LineSegments(
        g,
        new THREE.LineBasicMaterial({
          color: new THREE.Color(t.border),
          transparent: true,
          opacity: t.light ? 0.85 : 0.5,
        })
      )
    );
  }

  layout() {
    if (!this.ok) return;
    const cy = this.HMAX * 0.5;
    let halfW = this.SPAN * 0.6;
    let halfH = this.HMAX * 0.72;
    const aspect = this.w / Math.max(this.h, 1);
    if (halfW / halfH > aspect) halfH = halfW / aspect;
    else halfW = halfH * aspect;
    axonCamera(this.camera, LINE_AZ, LINE_EL, halfW, halfH, 0, cy, 0);
    this.dirty = true;
  }

  frame(dt) {
    if (!this.ok) return false;
    let moving = false;
    if (this.reveal.step(dt)) moving = true;
    const r = clamp(this.reveal.value, 0, 1);
    const tubeCount = Math.max(Math.floor((this.tubeIndexCount * r) / 6) * 6, 0);
    if (tubeCount !== this.lastTube) {
      this.tubeGeo.setDrawRange(0, tubeCount);
      this.curtainGeo.setDrawRange(0, Math.floor((this.curtainIndexCount * r) / 6) * 6);
      this.lastTube = tubeCount;
      this.dirty = true;
    }

    this.dots.forEach((d, i) => {
      const at = this.dots.length > 1 ? i / (this.dots.length - 1) : 0;
      if (!this.reduced && d.sp.target === 0 && r >= at - 0.001) d.sp.to(1);
      if (d.sp.step(dt)) moving = true;
      const s = d.sp.value * (this.hover === i ? 1.65 : 1);
      if (Math.abs(d.mesh.scale.x - s) > 0.001) {
        d.mesh.scale.setScalar(Math.max(s, 0.0001));
        this.dirty = true;
      }
    });
    if (this.dropA.step(dt)) moving = true;
    if (Math.abs(this.drop.material.opacity - this.dropA.value * 0.55) > 0.003) {
      this.drop.material.opacity = this.dropA.value * 0.55;
      this.dirty = true;
    }
    if (this.dirty) {
      this.render();
      this.dirty = false;
    }
    return moving;
  }

  pointPx(i) {
    return this.project(new THREE.Vector3(this.xs[i], this.ys[i], 0));
  }
  basePx(i) {
    return this.project(new THREE.Vector3(this.xs[i], 0, 1.0));
  }
  tickPx(v) {
    return this.project(new THREE.Vector3(-this.SPAN / 2 - 0.35, this.yOf(v), -2.2));
  }

  hit(x) {
    let best = -1;
    let bd = Infinity;
    for (let i = 0; i < this.xs.length; i++) {
      const p = this.pointPx(i);
      const d = Math.abs(p.x - x);
      if (d < bd) {
        bd = d;
        best = i;
      }
    }
    return bd <= Math.max(this.w / Math.max(this.xs.length, 1), HIT_SLOP) ? best : -1;
  }

  setHover(i) {
    if (i === this.hover) return;
    this.hover = i;
    this.dropA.to(i >= 0 ? 1 : 0);
    if (i >= 0) {
      const p = this.drop.geometry.attributes.position;
      p.setXYZ(0, this.xs[i], 0, 0);
      p.setXYZ(1, this.xs[i], this.ys[i], 0);
      p.needsUpdate = true;
      this.drop.geometry.computeBoundingSphere();
    }
    this.dirty = true;
  }

  recolor(t) {
    this.recolorLights(t);
    this.tubeMat.color.set(t.accent);
    this.tubeMat.emissive.set(t.accent2);
    this.tubeMat.emissiveIntensity = t.light ? 0.08 : 0.34;
    if (this.curtainMat.map) this.curtainMat.map.dispose();
    this.curtainMat.map = verticalFade(t);
    this.curtainMat.opacity = t.light ? 0.4 : 0.75;
    this.curtainMat.blending = t.light ? THREE.NormalBlending : THREE.AdditiveBlending;
    this.curtainMat.needsUpdate = true;
    this.floor.material.color.set(t.accent);
    this.floor.material.opacity = t.light ? 0.12 : 0.34;
    this.floor.material.blending = t.light ? THREE.NormalBlending : THREE.AdditiveBlending;
    this.floor.material.needsUpdate = true;
    this.dots.forEach((d, i) => {
      const c = new THREE.Color(this.data.points[i].color || t.accent2);
      d.mat.color.copy(c);
      d.mat.emissive.copy(c);
      d.mat.emissiveIntensity = t.light ? 0.15 : 0.5;
    });
    this.drop.material.color.set(t.textSecondary);
    this.buildGrid(t);
    this.dirty = true;
  }
}

function verticalFade(t) {
  const c = document.createElement('canvas');
  c.width = 4;
  c.height = 128;
  const g = c.getContext('2d');
  const grad = g.createLinearGradient(0, 0, 0, 128);
  const a = new THREE.Color(t.accent2).getStyle().replace('rgb(', 'rgba(').replace(')', ',');
  const b = new THREE.Color(t.accent).getStyle().replace('rgb(', 'rgba(').replace(')', ',');
  grad.addColorStop(0, a + (t.light ? '0.55)' : '0.85)'));
  grad.addColorStop(0.55, b + (t.light ? '0.18)' : '0.3)'));
  grad.addColorStop(1, b + '0)');
  g.fillStyle = grad;
  g.fillRect(0, 0, 4, 128);
  const tex = new THREE.CanvasTexture(c);
  tex.colorSpace = THREE.SRGBColorSpace;

  tex.flipY = true;
  return tex;
}

class Line2D extends C2DBase {
  constructor(plot, data) {
    super(plot);
    this.data = data;
    this.hover = -1;
    this.reduced = prefersReducedMotion();
    this.reveal = new Spring(this.reduced ? 1 : 0, { response: 0.95, damping: 1 });
    if (!this.reduced) this.reveal.to(1);
    this.dirty = true;
  }
  layout() {
    this.dirty = true;
  }
  metrics() {
    const padL = 46;
    const padR = 16;
    const padT = 20;
    const padB = 28;
    return {
      padL,
      padT,
      plotW: Math.max(this.w - padL - padR, 10),
      plotH: Math.max(this.h - padT - padB, 10),
      base: padT + Math.max(this.h - padT - padB, 10),
    };
  }
  pointPx(i) {
    const m = this.metrics();
    const n = this.data.points.length;
    const range = this.data.top - this.data.min || 1;
    return {
      x: m.padL + (m.plotW * i) / Math.max(n - 1, 1),
      y: m.base - ((this.data.points[i].value - this.data.min) / range) * m.plotH * 0.9 - m.plotH * 0.05,
    };
  }
  basePx(i) {
    return { x: this.pointPx(i).x, y: this.metrics().base + 6 };
  }
  tickPx(v) {
    const m = this.metrics();
    const range = this.data.top - this.data.min || 1;
    return { x: m.padL - 8, y: m.base - ((v - this.data.min) / range) * m.plotH * 0.9 - m.plotH * 0.05 };
  }
  frame(dt) {
    const moving = this.reveal.step(dt);
    if (!moving && !this.dirty) return false;
    this.dirty = false;
    this.draw();
    return moving;
  }
  draw() {
    const ctx = this.ctx;
    const t = tokens();
    this.clear();
    const m = this.metrics();
    const n = this.data.points.length;
    ctx.strokeStyle = t.border;
    ctx.globalAlpha = t.light ? 0.9 : 0.55;
    ctx.lineWidth = 1;
    for (const v of this.data.ticks) {
      const y = this.tickPx(v).y;
      ctx.beginPath();
      ctx.moveTo(m.padL, Math.round(y) + 0.5);
      ctx.lineTo(m.padL + m.plotW, Math.round(y) + 0.5);
      ctx.stroke();
    }
    ctx.globalAlpha = 1;

    const PER = 12;
    const ys = this.data.points.map((_, i) => this.pointPx(i).y);
    const sy = monotoneSample(ys, PER);
    const sx = [];
    for (let i = 0; i < n - 1; i++) {
      const a = this.pointPx(i).x;
      const b = this.pointPx(i + 1).x;
      for (let k = 0; k < PER; k++) sx.push(a + ((b - a) * k) / PER);
    }
    sx.push(this.pointPx(n - 1).x);
    const r = clamp(this.reveal.value, 0, 1);
    const cut = Math.max(Math.floor(sx.length * r), 1);

    const grad = ctx.createLinearGradient(0, m.padT, 0, m.base);
    grad.addColorStop(0, mixHex(t.accent2, t.surface, 0.25));
    grad.addColorStop(1, t.surface);
    ctx.globalAlpha = t.light ? 0.35 : 0.55;
    ctx.fillStyle = grad;
    ctx.beginPath();
    ctx.moveTo(sx[0], m.base);
    for (let i = 0; i < cut; i++) ctx.lineTo(sx[i], sy[i]);
    ctx.lineTo(sx[cut - 1], m.base);
    ctx.closePath();
    ctx.fill();
    ctx.globalAlpha = 1;

    ctx.strokeStyle = t.accent;
    ctx.lineWidth = 2.5;
    ctx.lineJoin = 'round';
    ctx.lineCap = 'round';
    ctx.beginPath();
    for (let i = 0; i < cut; i++) (i ? ctx.lineTo : ctx.moveTo).call(ctx, sx[i], sy[i]);
    ctx.stroke();

    for (let i = 0; i < n; i++) {
      const at = n > 1 ? i / (n - 1) : 0;
      if (r < at - 0.001) continue;
      const p = this.pointPx(i);
      ctx.fillStyle = this.data.points[i].color || t.accent2;
      ctx.beginPath();
      ctx.arc(p.x, p.y, i === this.hover ? 6 : 4, 0, Math.PI * 2);
      ctx.fill();
      ctx.strokeStyle = t.surface;
      ctx.lineWidth = 2;
      ctx.stroke();
    }
    if (this.hover >= 0) {
      const p = this.pointPx(this.hover);
      ctx.strokeStyle = t.textSecondary;
      ctx.globalAlpha = 0.4;
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(p.x, p.y);
      ctx.lineTo(p.x, m.base);
      ctx.stroke();
      ctx.globalAlpha = 1;
    }
  }
  hit(x) {
    const n = this.data.points.length;
    let best = -1;
    let bd = Infinity;
    for (let i = 0; i < n; i++) {
      const d = Math.abs(this.pointPx(i).x - x);
      if (d < bd) {
        bd = d;
        best = i;
      }
    }
    return bd <= Math.max(this.metrics().plotW / Math.max(n, 1), HIT_SLOP) ? best : -1;
  }
  setHover(i) {
    if (i === this.hover) return;
    this.hover = i;
    this.dirty = true;
  }
  recolor() {
    this.dirty = true;
  }
}

class Chart {
  constructor(el, kind, opts) {
    this.el = el;
    this.kind = kind;
    this.opts = opts;
    this.dead = false;
    this.tickCount = 0;
    this.detachedFrames = 0;

    const useLegend = kind === 'donut' && opts.legend !== false;
    const s = buildSurface(el, { legend: useLegend });
    Object.assign(this, s);

    this.labelNodes = [];
    this.buildRenderer();
    this.buildOverlay();

    this.ro = new ResizeObserver(() => this.layout());
    this.ro.observe(this.plot);

    this.onPointerMove = (e) => this.pointer(e);
    this.onPointerLeave = () => this.setHover(-1);
    this.plot.addEventListener('pointermove', this.onPointerMove);
    this.plot.addEventListener('pointerleave', this.onPointerLeave);
    this.plot.addEventListener('pointerdown', this.onPointerMove);

    this.offTheme = onThemeChange((t) => {
      this.renderer.recolor && this.renderer.recolor(t);
      this.paintOverlay(t);
      this.wake();
    });
    this.offReduce = onReducedMotionChange(() => this.wake());

    this.tick = (dt) => this.frame(dt);
    this.layout();
    this.wake();
  }

  buildRenderer() {
    const gl = hasWebGL() && canOpenContext();
    const Klass = {
      donut: gl ? DonutGL : Donut2D,
      bars: gl ? BarsGL : Bars2D,
      line: gl ? LineGL : Line2D,
    }[this.kind];
    let r = new Klass(this.plot, this.opts);

    if (!r.ok) {
      try {
        r.dispose && r.dispose();
      } catch (_) {}
      const Fallback = { donut: Donut2D, bars: Bars2D, line: Line2D }[this.kind];
      r = new Fallback(this.plot, this.opts);
    }
    this.renderer = r;
    this.usesGL = r instanceof GLBase;
    if (this.usesGL) {
      r.onContextLost = () => this.rebuildAs2D();
    }
  }

  rebuildAs2D() {
    if (this.dead) return;
    try {
      this.renderer.dispose();
    } catch (_) {}
    const Fallback = { donut: Donut2D, bars: Bars2D, line: Line2D }[this.kind];
    this.renderer = new Fallback(this.plot, this.opts);
    this.usesGL = false;
    this.layout();
    this.wake();
  }

  buildOverlay() {
    const t = tokens();
    const o = this.opts;
    this.labelNodes = [];

    if (this.kind === 'donut') {

      const center = mkEl(
        'div',
        {
          position: 'absolute',
          top: '0',
          left: '0',
          pointerEvents: 'none',
          textAlign: 'center',
          transform: 'translate3d(-9999px,-9999px,0)',
          willChange: 'transform',
        },
        this.labels
      );
      center.className = 'h3d-center';
      const cv = mkEl('div', { fontWeight: '600', letterSpacing: '-0.02em' }, center);
      const cl = mkEl('div', { letterSpacing: '0.14em', textTransform: 'uppercase' }, center);
      cv.className = 'h3d-center__value';
      cl.className = 'h3d-center__label';
      cl.textContent = o.centerLabel || 'Total';
      this.centerNode = center;
      this.centerValue = cv;
      this.centerLabel = cl;
      this.setCenter(null);

      this.legendItems = o.slices.map((s, i) => {
        const item = mkEl(
          'button',
          {
            display: 'flex',
            alignItems: 'center',
            gap: '9px',
            width: '100%',
            padding: '5px 7px',
            border: '0',
            borderRadius: '10px',
            background: 'transparent',
            font: 'inherit',
            textAlign: 'left',
            cursor: 'pointer',
            minWidth: 0,
            transition: 'background-color 160ms cubic-bezier(.23,1,.32,1)',
          },
          this.legend
        );
        item.className = 'h3d-legend__item';
        item.type = 'button';
        const sw = mkEl(
          'span',
          {
            flex: '0 0 auto',
            width: '10px',
            height: '10px',
            borderRadius: '4px',
            background: s.color,
            boxShadow: '0 0 0 1px rgba(0,0,0,.18) inset',
          },
          item
        );
        sw.className = 'h3d-legend__swatch';
        const lab = mkEl(
          'span',
          { flex: '1 1 auto', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' },
          item
        );
        lab.className = 'h3d-legend__label';
        lab.textContent = s.label;
        const val = mkEl('span', { flex: '0 0 auto', fontVariantNumeric: 'tabular-nums' }, item);
        val.className = 'h3d-legend__value';
        val.textContent = formatPercent(s.value, o.total);
        item.addEventListener('pointerenter', () => this.setHover(i, true));
        item.addEventListener('pointerleave', () => this.setHover(-1));
        item.addEventListener('focus', () => this.setHover(i, true));
        item.addEventListener('blur', () => this.setHover(-1));
        item.setAttribute(
          'aria-label',
          `${s.label}: ${formatValue(s.value, o.currency)} (${formatPercent(s.value, o.total)})`
        );
        return { item, sw, lab, val };
      });
    }

    if (this.kind === 'bars' || this.kind === 'line') {
      const list = this.kind === 'bars' ? this.opts.series : this.opts.points;
      this.catIdx = thinIndices(list.length, this.kind === 'bars' ? 12 : 7);
      this.catNodes = this.catIdx.map((i) => {
        const n = makeLabel(this.labels, 'cat');
        n.textContent = list[i].label;
        css(n, { transform: 'translate3d(-9999px,-9999px,0)' });
        return { node: n, index: i };
      });
      this.tickNodes = this.opts.ticks.map((v) => {
        const n = makeLabel(this.labels, 'tick');
        n.textContent = formatValue(v, this.opts.currency, { compact: true });
        return { node: n, value: v };
      });

      this.showValues = this.kind === 'bars' && list.length <= 8;
      if (this.showValues) {
        this.valueNodes = list.map((s) => {
          const n = makeLabel(this.labels, 'value');
          n.textContent = formatValue(s.value, this.opts.currency, { compact: list.length > 5 });
          return n;
        });
      }
      if (this.kind === 'line') {

        this.lastNode = makeLabel(this.labels, 'value');
        this.lastNode.textContent = formatValue(list[list.length - 1].value, this.opts.currency, {
          compact: true,
        });
      }
    }

    this.el.setAttribute('role', 'img');
    this.el.setAttribute('aria-label', this.opts.ariaLabel || '');
    if (this.opts.srRows) {
      this.srTable = buildSrTable(this.opts.srRows, this.opts.srHeaders);
      this.root.appendChild(this.srTable);
    }

    this.paintOverlay(t);
  }

  setCenter(index) {
    if (!this.centerValue) return;
    const o = this.opts;
    if (index == null || index < 0) {
      this.centerRaw = { valor: o.total, rotulo: o.centerLabel || 'Total', cor: null };
    } else {
      const s = o.slices[index];
      this.centerRaw = { valor: s.value, rotulo: s.label, cor: s.color };
    }
    this.centerLabel.textContent = this.centerRaw.rotulo;
    this.centerLabel.style.color = this.centerRaw.cor || tokens().textMuted;
    this.fitCenter();
  }

  fitCenter() {
    const cv = this.centerValue;
    const raw = this.centerRaw;
    if (!cv || !raw) return;
    const cur = this.opts.currency;
    const base = this.centerBase || 26;
    const hole = this.renderer && this.renderer.holePx ? this.renderer.holePx() : 0;

    const limite = hole > 0 ? hole * 0.8 : 0;

    css(cv, { whiteSpace: 'nowrap', fontSize: base + 'px' });

    css(this.centerLabel, {
      maxWidth: limite > 0 ? Math.round(limite) + 'px' : 'none',
      overflow: 'hidden',
      textOverflow: 'ellipsis',
      whiteSpace: 'nowrap',
    });

    cv.textContent = formatValue(raw.valor, cur);
    if (limite <= 0 || cv.scrollWidth <= limite) return;
    const largoCheio = cv.scrollWidth;
    const fonteCheia = Math.max(MIN_CENTRO, Math.floor(base * (limite / largoCheio)));

    const CONFORTO = Math.round(base * 0.72);
    if (fonteCheia >= CONFORTO) {
      css(cv, { fontSize: fonteCheia + 'px' });
      return;
    }

    const cheio = cv.textContent;
    cv.textContent = formatValue(raw.valor, cur, { compact: true });
    css(cv, { fontSize: base + 'px' });
    const largoCurto = cv.scrollWidth;
    const fonteCurta = largoCurto <= limite
      ? base
      : Math.max(MIN_CENTRO, Math.floor(base * (limite / largoCurto)));
    if (largoCurto < largoCheio && fonteCurta > fonteCheia) {
      css(cv, { fontSize: fonteCurta + 'px' });
      return;
    }
    cv.textContent = cheio;
    css(cv, { fontSize: fonteCheia + 'px' });
  }

  paintOverlay(t) {
    const T = t || tokens();
    css(this.tip, {
      background: T.light ? 'rgba(255,255,255,.86)' : 'rgba(18,16,26,.82)',
      color: T.textPrimary,
      boxShadow: T.light
        ? '0 8px 28px -12px rgba(24,20,40,.34), 0 0 0 1px ' + T.border
        : '0 14px 40px -16px rgba(0,0,0,.85), 0 0 0 1px rgba(255,255,255,.08)',
    });
    if (this.centerValue) {
      css(this.centerValue, { color: T.textPrimary });
      css(this.centerLabel, { color: T.textMuted });
    }
    if (this.legend) {
      css(this.legend, { color: T.textSecondary });
      (this.legendItems || []).forEach((li) => {
        css(li.lab, { color: T.textSecondary });
        css(li.val, { color: T.textMuted });
      });
    }
    (this.catNodes || []).forEach((c) => css(c.node, { color: T.textMuted }));
    (this.tickNodes || []).forEach((c) => css(c.node, { color: T.textMuted }));
    (this.valueNodes || []).forEach((n) => css(n, { color: T.textSecondary, fontWeight: '600' }));
    if (this.lastNode) css(this.lastNode, { color: T.textPrimary, fontWeight: '600' });
  }

  layout() {
    if (this.dead) return;
    const w = this.plot.clientWidth;
    const h = this.plot.clientHeight;

    if (this.kind === 'donut' && this.legend) {
      const cw = this.el.clientWidth;
      const ch = this.el.clientHeight;
      const wide = cw >= 360 && cw / Math.max(ch, 1) >= 1.15;
      css(this.root, { flexDirection: wide ? 'row' : 'column' });
      css(this.legend, {
        flexDirection: 'column',
        justifyContent: 'center',
        gap: '2px',
        flexWrap: wide ? 'nowrap' : 'wrap',
        width: wide ? Math.round(Math.min(cw * 0.42, 190)) + 'px' : '100%',
        maxHeight: wide ? '100%' : Math.round(ch * 0.42) + 'px',
        fontSize: cw < 300 ? '11px' : '12px',
        overflowY: 'auto',
      });
      if (!wide) css(this.legend, { flexDirection: 'row', justifyContent: 'center' });
      (this.legendItems || []).forEach((li) =>
        css(li.item, { width: wide ? '100%' : 'auto', flex: wide ? '0 0 auto' : '0 1 auto' })
      );
    }

    const rw = this.plot.clientWidth;
    const rh = this.plot.clientHeight;
    if (rw <= 0 || rh <= 0) return;
    this.renderer.setSize(rw, rh);
    this.renderer.layout && this.renderer.layout();
    this.sizeText(rw);
    this.positionLabels(true);
    this.wake();
  }

  sizeText(w) {
    const small = w < 300;
    if (this.centerValue) {
      this.centerBase = small ? 18 : w < 420 ? 22 : 26;
      css(this.centerValue, { lineHeight: '1.1' });
      css(this.centerLabel, { fontSize: (small ? 9 : 10) + 'px', marginTop: '3px' });
      this.fitCenter();
    }
    (this.catNodes || []).forEach((c) => css(c.node, { fontSize: (small ? 9 : 10.5) + 'px' }));
    (this.tickNodes || []).forEach((c) => css(c.node, { fontSize: (small ? 9 : 10.5) + 'px' }));
    (this.valueNodes || []).forEach((n) => css(n, { fontSize: (small ? 9.5 : 11) + 'px' }));
    if (this.lastNode) css(this.lastNode, { fontSize: (small ? 10 : 12) + 'px' });
  }

  positionLabels(force) {
    const r = this.renderer;
    if (!r || r.w <= 0) return;
    if (this.kind === 'donut' && this.centerNode) {
      const c = r.centerPx();
      const bw = this.centerNode.offsetWidth || 0;
      const bh = this.centerNode.offsetHeight || 0;
      place(this.centerNode, c.x - bw / 2, c.y - bh / 2);
    }
    if (this.catNodes) {
      for (const c of this.catNodes) {
        const p = r.basePx(c.index);
        const bw = c.node.offsetWidth || 0;
        place(c.node, p.x - bw / 2, p.y);
      }
    }
    if (this.tickNodes) {
      for (const c of this.tickNodes) {
        const p = r.tickPx(c.value);
        const bw = c.node.offsetWidth || 0;
        const bh = c.node.offsetHeight || 0;
        place(c.node, p.x - bw, p.y - bh / 2);
      }
    }
    if (this.valueNodes) {
      this.valueNodes.forEach((n, i) => {
        const p = r.topPx(i);
        const bw = n.offsetWidth || 0;
        const bh = n.offsetHeight || 0;
        place(n, p.x - bw / 2, p.y - bh - 6);
      });
    }
    if (this.lastNode) {
      const i = this.opts.points.length - 1;
      const p = r.pointPx(i);
      const bw = this.lastNode.offsetWidth || 0;
      const bh = this.lastNode.offsetHeight || 0;
      place(this.lastNode, clamp(p.x - bw / 2, 2, r.w - bw - 2), p.y - bh - 10);
    }
  }

  pointer(e) {
    const rect = this.plot.getBoundingClientRect();
    const x = e.clientX - rect.left;
    const y = e.clientY - rect.top;
    this.lastPointer = { x, y };
    const i = this.kind === 'donut' ? this.renderer.hit(x, y) : this.renderer.hit(x, y);
    this.setHover(i);
  }

  setHover(i, fromLegend) {
    if (this.hoverIndex === i) {
      if (i >= 0 && !fromLegend) this.moveTip();
      return;
    }
    this.hoverIndex = i;
    this.renderer.setHover(i);
    if (this.kind === 'donut') this.setCenter(i);
    if (this.legendItems) {
      const T = tokens();
      this.legendItems.forEach((li, k) =>
        css(li.item, {
          background:
            k === i ? (T.light ? 'rgba(0,0,0,.05)' : 'rgba(255,255,255,.07)') : 'transparent',
        })
      );
    }
    if (i < 0) {
      css(this.tip, { opacity: '0' });
      this.tip.textContent = '';
    } else {
      this.fillTip(i);
      this.moveTip(fromLegend ? this.anchorFor(i) : null);
      css(this.tip, { opacity: '1' });
    }
    this.wake();
  }

  anchorFor(i) {
    const r = this.renderer;
    if (this.kind === 'donut') return r.centerPx();
    if (this.kind === 'bars') return r.topPx(i);
    return r.pointPx(i);
  }

  fillTip(i) {
    const o = this.opts;
    const T = tokens();
    this.tip.textContent = '';
    const row = mkEl('div', { display: 'flex', alignItems: 'center', gap: '8px' }, this.tip);
    const item =
      this.kind === 'donut' ? o.slices[i] : this.kind === 'bars' ? o.series[i] : o.points[i];
    mkEl(
      'span',
      {
        width: '8px',
        height: '8px',
        borderRadius: '3px',
        flex: '0 0 auto',
        background: item.color || T.accent,
      },
      row
    );
    const lab = mkEl('span', { color: T.textSecondary }, row);
    lab.textContent = item.label;
    const val = mkEl(
      'div',
      { fontWeight: '600', marginTop: '3px', color: T.textPrimary, fontVariantNumeric: 'tabular-nums' },
      this.tip
    );
    val.textContent =
      formatValue(item.value, o.currency) +
      (this.kind === 'donut' ? `  ·  ${formatPercent(item.value, o.total)}` : '');
  }

  moveTip(anchor) {
    const r = this.renderer;
    const p = anchor || this.lastPointer || { x: r.w / 2, y: r.h / 2 };
    const tw = this.tip.offsetWidth;
    const th = this.tip.offsetHeight;
    const x = clamp(p.x - tw / 2, 4, Math.max(r.w - tw - 4, 4));
    const y = clamp(p.y - th - 14, 4, Math.max(r.h - th - 4, 4));
    this.tip.style.transform = `translate3d(${Math.round(x)}px, ${Math.round(y)}px, 0)`;
  }

  wake() {
    if (this.dead || this.running) return;
    this.running = true;
    addTask(this.tick);
  }

  frame(dt) {
    if (this.dead) return;

    if ((++this.tickCount & 31) === 0) {
      if (!this.el.isConnected) {
        if (++this.detachedFrames >= 2) {
          destroy(this.el);
          return;
        }
      } else this.detachedFrames = 0;
    }
    const moving = this.renderer.frame(dt);
    if (moving) this.positionLabels();
    if (!moving) {
      this.positionLabels();
      this.running = false;
      removeTask(this.tick);
    }
  }

  destroy() {
    if (this.dead) return;
    this.dead = true;
    removeTask(this.tick);
    this.running = false;
    this.ro && this.ro.disconnect();
    this.plot.removeEventListener('pointermove', this.onPointerMove);
    this.plot.removeEventListener('pointerleave', this.onPointerLeave);
    this.plot.removeEventListener('pointerdown', this.onPointerMove);
    this.offTheme && this.offTheme();
    this.offReduce && this.offReduce();
    try {
      this.renderer.dispose();
    } catch (err) {
      console.error('[helios] dispose', err);
    }
    this.renderer = null;
    if (this.root && this.root.parentNode) this.root.parentNode.removeChild(this.root);
    this.el.removeAttribute('role');
    this.el.removeAttribute('aria-label');
    this.root = this.plot = this.labels = this.tip = this.legend = null;
    this.labelNodes = this.catNodes = this.tickNodes = this.valueNodes = null;
  }
}

function reset(el) {
  const prev = instances.get(el);
  if (prev) {
    prev.destroy();
    instances.delete(el);
  }

  el.querySelectorAll(':scope > .h3d-root, :scope > .h3d-empty').forEach((n) => n.remove());
}

export function donut(el, opts = {}) {
  if (!el) return;
  reset(el);
  const raw = sanitize(opts.segments, 'value').filter((s) => s.value > 0);
  const sum = raw.reduce((a, s) => a + s.value, 0);
  if (!raw.length || sum <= 0) {
    return empty(el, { message: 'Sem dados para exibir', icon: 'donut' });
  }
  const t = tokens();
  const total = Number(opts.total);
  const shownTotal = isFinite(total) && total > 0 ? total : sum;

  const gap = raw.length > 1 ? clamp(0.055 / raw.length + 0.012, 0.008, 0.045) : 0;
  const usable = Math.PI * 2 - gap * raw.length;
  let acc = -Math.PI / 2 + gap / 2;
  const slices = raw.map((s, i) => {
    const len = (s.value / sum) * usable;
    const o = {
      label: s.label || `Item ${i + 1}`,
      value: s.value,
      color: s.color || t.accent,
      thetaStart: acc,
      thetaLen: len,
    };
    acc += len + gap;
    return o;
  });

  const data = {
    slices,
    total: shownTotal,
    currency: opts.currency,
    legend: opts.legend,
    centerLabel: opts.centerLabel,
    ariaLabel:
      'Grafico de rosca. Total ' +
      formatValue(shownTotal, opts.currency) +
      '. ' +
      slices.map((s) => `${s.label}: ${formatPercent(s.value, sum)}`).join(', '),
    srHeaders: ['Segmento', 'Valor', 'Participacao'],
    srRows: slices.map((s) => [
      s.label,
      formatValue(s.value, opts.currency),
      formatPercent(s.value, sum),
    ]),
  };
  const c = new Chart(el, 'donut', data);
  instances.set(el, c);
  return c;
}

export function bars(el, opts = {}) {
  if (!el) return;
  reset(el);
  const raw = sanitize(opts.series, 'value');
  if (!raw.length) return empty(el, { message: 'Sem dados para exibir', icon: 'bars' });
  const t = tokens();
  const max = Math.max(...raw.map((s) => Math.max(s.value, 0)));
  if (!(max > 0)) return empty(el, { message: 'Sem valores registrados', icon: 'bars' });
  const { ticks, top } = niceTicks(max, raw.length > 8 ? 3 : 4);
  const series = raw.map((s, i) => ({
    label: s.label || `#${i + 1}`,
    value: Math.max(s.value, 0),

    color: s.color || (i === raw.indexOf(raw.reduce((a, b) => (b.value > a.value ? b : a))) ? t.accent2 : t.accent),
  }));
  const data = {
    series,
    top,
    ticks,
    currency: opts.currency,
    ariaLabel:
      'Grafico de barras. ' +
      series.map((s) => `${s.label}: ${formatValue(s.value, opts.currency)}`).join(', '),
    srHeaders: ['Categoria', 'Valor'],
    srRows: series.map((s) => [s.label, formatValue(s.value, opts.currency)]),
  };
  const c = new Chart(el, 'bars', data);
  instances.set(el, c);
  return c;
}

export function line(el, opts = {}) {
  if (!el) return;
  reset(el);
  const raw = sanitize(opts.points, 'value');
  if (raw.length < 2) {
    return empty(el, {
      message: raw.length ? 'Poucos dados para uma serie' : 'Sem dados para exibir',
      icon: 'line',
    });
  }
  const t = tokens();
  const vals = raw.map((p) => p.value);
  const vmax = Math.max(...vals);
  const vmin = Math.min(...vals);

  const min = vmin >= 0 ? 0 : vmin - (vmax - vmin) * 0.1;
  const { ticks, top } = niceTicks(vmax - min, 4);
  const realTop = min + top;
  const points = raw.map((p, i) => ({
    label: p.label || String(i + 1),
    value: p.value,
    color: p.color || t.accent2,
  }));
  const data = {
    points,
    top: realTop,
    min,
    ticks: ticks.map((v) => v + min).filter((v) => v <= realTop + 1e-6),
    currency: opts.currency,
    ariaLabel:
      'Grafico de linha. ' +
      points.map((p) => `${p.label}: ${formatValue(p.value, opts.currency)}`).join(', '),
    srHeaders: ['Periodo', 'Valor'],
    srRows: points.map((p) => [p.label, formatValue(p.value, opts.currency)]),
  };
  const c = new Chart(el, 'line', data);
  instances.set(el, c);
  return c;
}

const EMPTY_ICONS = {
  donut:
    '<circle cx="24" cy="24" r="15" fill="none" stroke="currentColor" stroke-width="1.25" opacity=".55"/><circle cx="24" cy="24" r="7" fill="none" stroke="currentColor" stroke-width="1.25" opacity=".9"/>',
  bars:
    '<path d="M13 33V25M24 33V17M35 33V21" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" opacity=".75"/><path d="M9 37h30" stroke="currentColor" stroke-width="1.25" stroke-linecap="round" opacity=".4"/>',
  line:
    '<path d="M11 30l7-7 6 5 8-11 6 5" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" opacity=".8"/><path d="M9 37h30" stroke="currentColor" stroke-width="1.25" stroke-linecap="round" opacity=".4"/>',
  default:
    '<rect x="11" y="11" width="26" height="26" rx="8" fill="none" stroke="currentColor" stroke-width="1.25" opacity=".55"/><path d="M18 24h12" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" opacity=".8"/>',
};

export function empty(el, opts = {}) {
  if (!el) return;
  reset(el);
  const t = tokens();
  if (getComputedStyle(el).position === 'static') el.style.position = 'relative';

  const wrap = mkEl('div', {
    position: 'absolute',
    inset: '0',
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'center',
    justifyContent: 'center',
    gap: '12px',
    textAlign: 'center',
    padding: '16px',
    pointerEvents: 'none',
  });
  wrap.className = 'h3d-empty';

  const halo = mkEl(
    'div',
    {
      position: 'absolute',
      left: '50%',
      top: '50%',
      width: '260px',
      height: '260px',
      marginLeft: '-130px',
      marginTop: '-130px',
      borderRadius: '50%',
      pointerEvents: 'none',
      background: `radial-gradient(circle, ${t.accent2} 0%, transparent 62%)`,
      opacity: t.light ? '0.1' : '0.16',
      filter: 'blur(18px)',
    },
    wrap
  );
  halo.className = 'h3d-empty__halo';

  const ring = mkEl(
    'div',
    {
      position: 'relative',
      width: '58px',
      height: '58px',
      borderRadius: '20px',
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'center',
      color: t.accent2,
      background: t.light ? 'rgba(0,0,0,.03)' : 'rgba(255,255,255,.045)',
      boxShadow: t.light
        ? '0 0 0 1px ' + t.border + ', inset 0 1px 1px rgba(255,255,255,.7)'
        : '0 0 0 1px rgba(255,255,255,.07), inset 0 1px 1px rgba(255,255,255,.09)',
    },
    wrap
  );
  ring.className = 'h3d-empty__ring';
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.setAttribute('viewBox', '0 0 48 48');
  svg.setAttribute('width', '30');
  svg.setAttribute('height', '30');
  svg.setAttribute('aria-hidden', 'true');
  svg.innerHTML = EMPTY_ICONS[opts.icon] || EMPTY_ICONS.default;
  ring.appendChild(svg);

  const msg = mkEl(
    'div',
    {
      position: 'relative',
      color: t.textSecondary,
      fontSize: '13px',
      lineHeight: '1.5',
      maxWidth: '30ch',
    },
    wrap
  );
  msg.className = 'h3d-empty__message';
  msg.textContent = opts.message || 'Nenhum dado disponivel ainda';

  el.appendChild(wrap);

  const off = onThemeChange((T) => {
    css(halo, {
      background: `radial-gradient(circle, ${T.accent2} 0%, transparent 62%)`,
      opacity: T.light ? '0.1' : '0.16',
    });
    css(ring, {
      color: T.accent2,
      background: T.light ? 'rgba(0,0,0,.03)' : 'rgba(255,255,255,.045)',
    });
    css(msg, { color: T.textSecondary });
  });

  instances.set(el, {
    destroy() {
      off();
      if (wrap.parentNode) wrap.parentNode.removeChild(wrap);
    },
  });
}

export function destroy(el) {
  if (!el) return;
  const inst = instances.get(el);
  if (inst) {
    try {
      inst.destroy();
    } catch (err) {
      console.error('[helios] destroy', err);
    }
    instances.delete(el);
  }
  el.querySelectorAll(':scope > .h3d-root, :scope > .h3d-empty').forEach((n) => n.remove());
}

export function destroyAll(scope) {
  const root = scope || document;
  root.querySelectorAll('[data-chart]').forEach((n) => destroy(n));
}
