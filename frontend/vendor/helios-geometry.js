const RING_SEGMENTS = 48;

const PROFILE_POINTS = 8;
const VERTS_PER_RING = PROFILE_POINTS * 2;

function buildProfile(inner, outer, height, chamfer) {
  const hz = height / 2;
  const c = Math.min(chamfer, hz * 0.8, (outer - inner) * 0.35);
  const P = [
    [inner + c, hz],
    [outer - c, hz],
    [outer, hz - c],
    [outer, -hz + c],
    [outer - c, -hz],
    [inner + c, -hz],
    [inner, -hz + c],
    [inner, hz - c],
  ];
  const N = [];
  for (let s = 0; s < PROFILE_POINTS; s++) {
    const a = P[s];
    const b = P[(s + 1) % PROFILE_POINTS];
    const dr = b[0] - a[0];
    const dz = b[1] - a[1];
    const len = Math.hypot(dr, dz) || 1;
    N.push([-dz / len, dr / len]);
  }
  return { P, N };
}

export function makeRingSector(THREE, { inner, outer, height, chamfer = 0.02 }) {
  const geo = new THREE.BufferGeometry();
  const ringCount = RING_SEGMENTS + 1;
  const capVerts = PROFILE_POINTS * 2;
  const total = ringCount * VERTS_PER_RING + capVerts;

  const pos = new Float32Array(total * 3);
  const nor = new Float32Array(total * 3);
  geo.setAttribute('position', new THREE.BufferAttribute(pos, 3));
  geo.setAttribute('normal', new THREE.BufferAttribute(nor, 3));

  const idx = [];
  for (let i = 0; i < RING_SEGMENTS; i++) {
    const b0 = i * VERTS_PER_RING;
    const b1 = (i + 1) * VERTS_PER_RING;
    for (let s = 0; s < PROFILE_POINTS; s++) {
      const A0 = b0 + s * 2;
      const B0 = b0 + s * 2 + 1;
      const A1 = b1 + s * 2;
      const B1 = b1 + s * 2 + 1;

      idx.push(A0, B1, A1, A0, B0, B1);
    }
  }
  const capStart = ringCount * VERTS_PER_RING;
  const capEnd = capStart + PROFILE_POINTS;

  for (let s = 1; s < PROFILE_POINTS - 1; s++) {
    idx.push(capStart, capStart + s + 1, capStart + s);
  }

  for (let s = 1; s < PROFILE_POINTS - 1; s++) {
    idx.push(capEnd, capEnd + s, capEnd + s + 1);
  }
  geo.setIndex(idx);

  const profile = buildProfile(inner, outer, height, chamfer);

  geo.updateSector = function (thetaStart, thetaLength) {
    const { P, N } = profile;
    const len = Math.max(thetaLength, 1e-5);
    for (let i = 0; i < ringCount; i++) {
      const a = thetaStart + (i / RING_SEGMENTS) * len;
      const ca = Math.cos(a);
      const sa = Math.sin(a);
      const base = i * VERTS_PER_RING;
      for (let s = 0; s < PROFILE_POINTS; s++) {
        const A = P[s];
        const B = P[(s + 1) % PROFILE_POINTS];
        const n = N[s];
        const nx = n[0] * ca;
        const ny = n[0] * sa;
        const nz = n[1];
        let o = (base + s * 2) * 3;
        pos[o] = A[0] * ca;
        pos[o + 1] = A[0] * sa;
        pos[o + 2] = A[1];
        nor[o] = nx;
        nor[o + 1] = ny;
        nor[o + 2] = nz;
        o += 3;
        pos[o] = B[0] * ca;
        pos[o + 1] = B[0] * sa;
        pos[o + 2] = B[1];
        nor[o] = nx;
        nor[o + 1] = ny;
        nor[o + 2] = nz;
      }
    }

    const writeCap = (vertBase, angle, sign) => {
      const ca = Math.cos(angle);
      const sa = Math.sin(angle);

      const nx = -sa * sign;
      const ny = ca * sign;
      for (let s = 0; s < PROFILE_POINTS; s++) {
        const o = (vertBase + s) * 3;
        pos[o] = P[s][0] * ca;
        pos[o + 1] = P[s][0] * sa;
        pos[o + 2] = P[s][1];
        nor[o] = nx;
        nor[o + 1] = ny;
        nor[o + 2] = 0;
      }
    };
    writeCap(capStart, thetaStart, -1);
    writeCap(capEnd, thetaStart + len, 1);

    geo.attributes.position.needsUpdate = true;
    geo.attributes.normal.needsUpdate = true;
    geo.computeBoundingSphere();
    return geo;
  };

  geo.updateSector(0, 0.001);
  return geo;
}

export function makeChamferBar(THREE, { width, depth, chamfer = 0.02 }) {
  const hw = width / 2;
  const hd = depth / 2;
  const c = Math.min(chamfer, hw * 0.5, hd * 0.5);

  const CORNERS = [
    [-1, -1],
    [1, -1],
    [1, 1],
    [-1, 1],
  ];

  const sideVerts = 4 * 4;
  const chamVerts = 4 * 4;
  const cornerVerts = 4 * 3 * 2;
  const topVerts = 4;
  const botVerts = 4;
  const total = sideVerts + chamVerts + cornerVerts + topVerts + botVerts;

  const pos = new Float32Array(total * 3);
  const nor = new Float32Array(total * 3);
  const geo = new THREE.BufferGeometry();
  geo.setAttribute('position', new THREE.BufferAttribute(pos, 3));
  geo.setAttribute('normal', new THREE.BufferAttribute(nor, 3));

  const idx = [];
  let v = 0;
  const meta = [];

  const push = (x, z, yRow, nx, ny, nz) => {
    const o = v * 3;
    nor[o] = nx;
    nor[o + 1] = ny;
    nor[o + 2] = nz;
    meta.push({ x, z, yRow });
    return v++;
  };

  for (let f = 0; f < 4; f++) {
    const a = CORNERS[f];
    const b = CORNERS[(f + 1) % 4];

    const ex = b[0] * hw - a[0] * hw;
    const ez = b[1] * hd - a[1] * hd;
    const L = Math.hypot(ex, ez) || 1;
    const nx = ez / L;
    const nz = -ex / L;
    const v0 = push(a[0] * hw, a[1] * hd, 0, nx, 0, nz);
    const v1 = push(b[0] * hw, b[1] * hd, 0, nx, 0, nz);
    const v2 = push(b[0] * hw, b[1] * hd, 1, nx, 0, nz);
    const v3 = push(a[0] * hw, a[1] * hd, 1, nx, 0, nz);

    idx.push(v0, v3, v2, v0, v2, v1);
  }

  const inset = c;
  for (let f = 0; f < 4; f++) {
    const a = CORNERS[f];
    const b = CORNERS[(f + 1) % 4];
    const ex = b[0] * hw - a[0] * hw;
    const ez = b[1] * hd - a[1] * hd;
    const L = Math.hypot(ex, ez) || 1;
    const nx = ez / L;
    const nz = -ex / L;

    const cx = nx * 0.7071;
    const cy = 0.7071;
    const cz = nz * 0.7071;

    const ax = a[0] * hw - Math.sign(nx) * inset;
    const az = a[1] * hd - Math.sign(nz) * inset;
    const bx = b[0] * hw - Math.sign(nx) * inset;
    const bz = b[1] * hd - Math.sign(nz) * inset;
    const v0 = push(a[0] * hw, a[1] * hd, 1, cx, cy, cz);
    const v1 = push(b[0] * hw, b[1] * hd, 1, cx, cy, cz);
    const v2 = push(bx, bz, 2, cx, cy, cz);
    const v3 = push(ax, az, 2, cx, cy, cz);
    idx.push(v0, v3, v2, v0, v2, v1);
  }

  for (let f = 0; f < 4; f++) {
    const a = CORNERS[f];
    const sx = Math.sign(a[0]);
    const sz = Math.sign(a[1]);
    const nx = sx * 0.5774;
    const ny = 0.5774;
    const nz = sz * 0.5774;
    const cxp = a[0] * hw;
    const czp = a[1] * hd;
    const v0 = push(cxp, czp, 1, nx, ny, nz);
    const v1 = push(cxp - sx * inset, czp, 2, nx, ny, nz);
    const v2 = push(cxp, czp - sz * inset, 2, nx, ny, nz);

    if (sx * sz > 0) idx.push(v0, v2, v1);
    else idx.push(v0, v1, v2);

    push(cxp, czp, 2, nx, ny, nz);
    push(cxp, czp, 2, nx, ny, nz);
    push(cxp, czp, 2, nx, ny, nz);
  }

  {
    const t0 = push(-hw + inset, -hd + inset, 2, 0, 1, 0);
    const t1 = push(hw - inset, -hd + inset, 2, 0, 1, 0);
    const t2 = push(hw - inset, hd - inset, 2, 0, 1, 0);
    const t3 = push(-hw + inset, hd - inset, 2, 0, 1, 0);
    idx.push(t0, t2, t1, t0, t3, t2);
  }

  {
    const b0 = push(-hw, -hd, 0, 0, -1, 0);
    const b1 = push(hw, -hd, 0, 0, -1, 0);
    const b2 = push(hw, hd, 0, 0, -1, 0);
    const b3 = push(-hw, hd, 0, 0, -1, 0);
    idx.push(b0, b1, b2, b0, b2, b3);
  }

  geo.setIndex(idx);

  let built = false;
  geo.updateHeight = function (h) {
    const hh = Math.max(h, 1e-4);
    const cc = Math.min(c, hh * 0.5);
    for (let i = 0; i < meta.length; i++) {
      const m = meta[i];
      const o = i * 3;
      if (!built) {
        pos[o] = m.x;
        pos[o + 2] = m.z;
      }
      pos[o + 1] = m.yRow === 0 ? 0 : m.yRow === 1 ? hh - cc : hh;
    }
    built = true;
    geo.attributes.position.needsUpdate = true;
    geo.computeBoundingSphere();
    return geo;
  };

  geo.updateHeight(0.0001);
  return geo;
}

export function makeGlowFloor(THREE, { width, depth, segments = 24 }) {
  const geo = new THREE.PlaneGeometry(width, depth, segments, segments);
  geo.rotateX(-Math.PI / 2);
  const p = geo.attributes.position;
  const colors = new Float32Array(p.count * 3);
  const maxD = Math.hypot(width / 2, depth / 2);
  for (let i = 0; i < p.count; i++) {
    const x = p.getX(i);
    const z = p.getZ(i);
    const d = Math.hypot(x, z) / maxD;
    const g = Math.pow(1 - Math.min(d, 1), 2.4);
    colors[i * 3] = g;
    colors[i * 3 + 1] = g;
    colors[i * 3 + 2] = g;
  }
  geo.setAttribute('color', new THREE.BufferAttribute(colors, 3));
  return geo;
}
