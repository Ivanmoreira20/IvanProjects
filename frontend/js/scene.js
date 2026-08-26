import * as THREE from '../vendor/three.module.min.js';
import {
  addTask, removeTask,
  prefersReducedMotion, onReducedMotionChange,
  canOpenContext, acquireContext,
  dpr, tokens, onThemeChange,
  disposeObject, killRenderer, radialTexture,
  clamp, lerp, isCoarsePointer,
} from '../vendor/helios-core.js';

const PRESETS = {
  login: { count: 900, spread: 26, size: 0.30, fog: 3, drift: 0.020, parallax: 1.5, opacity: 0.95, offsetX: 0.16 },
  app:   { count: 380, spread: 34, size: 0.24, fog: 2, drift: 0.010, parallax: 0.7, opacity: 0.55, offsetX: 0.10 },
};

let current = null;

function buildParticles(preset, palette) {
  const n = preset.count;
  const pos = new Float32Array(n * 3);
  const col = new Float32Array(n * 3);
  const c = new THREE.Color();

  for (let i = 0; i < n; i++) {
    const i3 = i * 3;
    pos[i3]     = (Math.random() - 0.5) * preset.spread;
    pos[i3 + 1] = (Math.random() - 0.5) * preset.spread * 0.62;

    pos[i3 + 2] = -Math.random() * preset.spread * 0.9;

    const r = Math.random();
    c.set(r < 0.55 ? palette[0] : r < 0.88 ? palette[1] : palette[2]);

    const depth = 0.35 + 0.65 * (1 + pos[i3 + 2] / (preset.spread * 0.9));
    col[i3]     = c.r * depth;
    col[i3 + 1] = c.g * depth;
    col[i3 + 2] = c.b * depth;
  }

  const geo = new THREE.BufferGeometry();
  geo.setAttribute('position', new THREE.BufferAttribute(pos, 3));
  geo.setAttribute('color', new THREE.BufferAttribute(col, 3));

  const mat = new THREE.PointsMaterial({
    size: preset.size,
    map: radialTexture(THREE, 'soft'),
    vertexColors: true,
    transparent: true,
    opacity: preset.opacity,
    depthWrite: false,
    blending: THREE.AdditiveBlending,
    sizeAttenuation: true,
  });

  return new THREE.Points(geo, mat);
}

function buildFog(preset, palette) {
  const group = new THREE.Group();
  const tex = radialTexture(THREE, 'nebula', [
    [0, 'rgba(255,255,255,0.55)'],
    [0.35, 'rgba(255,255,255,0.20)'],
    [0.7, 'rgba(255,255,255,0.05)'],
    [1, 'rgba(255,255,255,0)'],
  ]);

  for (let i = 0; i < preset.fog; i++) {
    const mat = new THREE.SpriteMaterial({
      map: tex,
      color: new THREE.Color(palette[i % palette.length]),
      transparent: true,
      opacity: 0.34,
      depthWrite: false,
      blending: THREE.AdditiveBlending,
    });
    const s = new THREE.Sprite(mat);

    const t = preset.fog > 1 ? i / (preset.fog - 1) : 0.5;
    const fx = (-0.30 + 0.92 * t) * preset.spread;
    const fy = (i % 2 === 0 ? 0.13 : -0.15) * preset.spread;
    const fz = -preset.spread * (0.55 + 0.14 * t);

    const scale = preset.spread * (0.62 + 0.22 * ((i % 3) / 2));
    s.scale.set(scale, scale * 0.8, 1);
    s.position.set(fx, fy, fz);
    s.userData.phase = (i / Math.max(1, preset.fog)) * Math.PI * 2;
    s.userData.baseY = s.position.y;
    group.add(s);
  }
  return group;
}

export function unmount() {
  if (!current) return;
  const s = current;
  current = null;

  if (s.task) removeTask(s.task);
  if (s.ro) s.ro.disconnect();
  if (s.offTheme) s.offTheme();
  if (s.offReduce) s.offReduce();
  if (s.onPointer) window.removeEventListener('pointermove', s.onPointer);
  if (s.onLeave) window.removeEventListener('pointerout', s.onLeave);

  try { disposeObject(s.scene); } catch (_) {  }
  try { killRenderer(s.renderer); } catch (_) {  }

  if (s.host) s.host.classList.remove('is-ready');
}

export function mount(el, opts = {}) {

  unmount();
  if (!el) return;

  const preset = PRESETS[opts.mode === 'login' ? 'login' : 'app'];
  el.classList.remove('is-ready');

  if (!canOpenContext()) return;

  let renderer;
  try {
    renderer = new THREE.WebGLRenderer({
      alpha: true,
      antialias: false,
      powerPreference: 'low-power',
      failIfMajorPerformanceCaveat: false,
    });
  } catch (_) {
    return;
  }
  acquireContext();

  const w = Math.max(1, el.clientWidth || window.innerWidth);
  const h = Math.max(1, el.clientHeight || window.innerHeight);

  renderer.setPixelRatio(dpr());
  renderer.setSize(w, h, false);
  renderer.setClearColor(0x000000, 0);
  el.appendChild(renderer.domElement);

  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(58, w / h, 0.1, 120);
  camera.position.set(0, 0, 16);

  const t = tokens();
  const palette = [t.accent, t.accent2, t.accent3];

  const scale = w < 700 ? 0.45 : w < 1100 ? 0.7 : 1;
  const tuned = { ...preset, count: Math.round(preset.count * scale) };

  const shiftX = tuned.spread * (tuned.offsetX || 0);

  let particles = buildParticles(tuned, palette);
  let fog = buildFog(tuned, palette);
  particles.position.x = shiftX;
  fog.position.x = shiftX;
  scene.add(particles, fog);

  const s = {
    host: el, renderer, scene, camera, particles, fog,
    preset: tuned, palette,
    px: 0, py: 0, tx: 0, ty: 0, elapsed: 0,
    task: null, ro: null, offTheme: null, offReduce: null,
    onPointer: null, onLeave: null,
  };
  current = s;

  const draw = () => renderer.render(scene, camera);

  const resize = () => {
    const nw = Math.max(1, el.clientWidth || window.innerWidth);
    const nh = Math.max(1, el.clientHeight || window.innerHeight);
    camera.aspect = nw / nh;
    camera.updateProjectionMatrix();
    renderer.setPixelRatio(dpr());
    renderer.setSize(nw, nh, false);
    draw();
  };
  if (typeof ResizeObserver === 'function') {
    s.ro = new ResizeObserver(resize);
    s.ro.observe(el);
  } else {
    window.addEventListener('resize', resize);
    s.ro = { disconnect: () => window.removeEventListener('resize', resize) };
  }

  s.offTheme = onThemeChange((next) => {
    if (current !== s) return;
    const pal = [next.accent, next.accent2, next.accent3];
    s.palette = pal;
    scene.remove(particles, fog);
    disposeObject(particles);
    disposeObject(fog);
    particles = buildParticles(s.preset, pal);
    fog = buildFog(s.preset, pal);
    particles.position.x = shiftX;
    fog.position.x = shiftX;
    s.particles = particles;
    s.fog = fog;
    scene.add(particles, fog);
    draw();
  });

  if (!isCoarsePointer) {
    s.onPointer = (ev) => {
      if (current !== s) return;
      s.tx = (ev.clientX / window.innerWidth - 0.5) * 2;
      s.ty = (ev.clientY / window.innerHeight - 0.5) * 2;
    };
    s.onLeave = () => { s.tx = 0; s.ty = 0; };
    window.addEventListener('pointermove', s.onPointer, { passive: true });
    window.addEventListener('pointerout', s.onLeave, { passive: true });
  }

  const tick = (dt) => {
    if (current !== s) return;
    const step = clamp(dt || 0.016, 0, 0.05);
    s.elapsed += step;

    particles.rotation.y += s.preset.drift * step;
    particles.rotation.x = Math.sin(s.elapsed * 0.09) * 0.045;

    for (const sprite of fog.children) {
      const ph = sprite.userData.phase + s.elapsed * 0.16;
      sprite.position.y = sprite.userData.baseY + Math.sin(ph) * 0.75;
      sprite.material.opacity = 0.26 + Math.sin(ph * 0.7) * 0.10;
    }

    const k = s.preset.parallax;
    s.px = lerp(s.px, s.tx * k, 0.045);
    s.py = lerp(s.py, -s.ty * k * 0.6, 0.045);
    camera.position.x = s.px;
    camera.position.y = s.py;
    camera.lookAt(0, 0, -8);

    draw();
  };

  const applyMotion = (reduced) => {
    if (current !== s) return;
    if (reduced) {
      if (s.task) { removeTask(s.task); s.task = null; }
      s.px = s.py = s.tx = s.ty = 0;
      camera.position.set(0, 0, 16);
      camera.lookAt(0, 0, -8);
      draw();
    } else if (!s.task) {
      s.task = tick;
      addTask(s.task);
    }
  };
  applyMotion(prefersReducedMotion());
  s.offReduce = onReducedMotionChange(applyMotion);

  draw();

  requestAnimationFrame(() => { if (current === s) el.classList.add('is-ready'); });
}
