import * as THREE from 'three';

// Ported from website/docs/prototype/logo-worlds.html. All DOM and scroll reading is gone: the page drives
// setMix (world weights) and setStation (viewport-fraction placement); the loop keeps the prototype's inertia.

// World palettes (enhance-spec.md section 2). facetL is each product's MID tone; the vertex ramp builds the kit's light-top/saturated-bottom gradient from it. facetR stays lifted navy in the dark worlds (family rule) and true navy #16233D in the Clinic room. exp = tone-mapping exposure.
// Order matches the mix: master, retail, clinic, gym.
const WORLD_KEYS = ['master', 'retail', 'clinic', 'gym'];
const W = [
  { facetL: '#4FA09B', facetR: '#22304A', ring: '#6FC0BC', ringEm: '#2F7B7B', node: '#EDFDFC', halo: '#8FD0D0', glow: '#5FB3AE', key: '#E6F4F2', rim: '#5FB3AE', sky: '#3B5577', ground: '#0E1729', shadow: '#02060C', exp: 1.0, light: 0 },
  { facetL: '#C97B3D', facetR: '#22304A', ring: '#CF8244', ringEm: '#6B3410', node: '#FBE3C4', halo: '#F0B87A', glow: '#C97B3D', key: '#FFEFDC', rim: '#E0945A', sky: '#5A3A24', ground: '#1A1512', shadow: '#060302', exp: 1.0, light: 0 },
  { facetL: '#3D7FB5', facetR: '#16233D', ring: '#2F6AA0', ringEm: '#1A446C', node: '#F4FAFF', halo: '#8CC7EE', glow: '#3D7FB5', key: '#FFFFFF', rim: '#8CC7EE', sky: '#FFFFFF', ground: '#B9CFE0', shadow: '#5F7D9C', exp: 0.92, light: 1 },
  { facetL: '#8B1E3F', facetR: '#22304A', ring: '#9E2342', ringEm: '#4A0C1E', node: '#FFF1F2', halo: '#F5A3A8', glow: '#B8283F', key: '#FFEDEA', rim: '#D8465C', sky: '#4A1A28', ground: '#1A0E14', shadow: '#08030A', exp: 1.0, light: 0 },
];

export function mountJewel(canvas, { phone }) {
  const reduce = matchMedia('(prefers-reduced-motion: reduce)').matches;
  const C = (h) => new THREE.Color(h);
  W.forEach((w) => {
    if (w.c) return;
    w.c = {};
    for (const k in w) if (typeof w[k] === 'string') w.c[k] = C(w[k]);
  });

  const gl = canvas.getContext('webgl2') || canvas.getContext('webgl');
  const renderer = new THREE.WebGLRenderer({ canvas, context: gl, antialias: true, alpha: true, powerPreference: 'high-performance' });

  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, phone ? 1.5 : 2));
  renderer.outputEncoding = THREE.sRGBEncoding;
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1.0;
  renderer.setClearColor(0x000000, 0);
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = THREE.PCFSoftShadowMap;

  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(22, 1, 0.1, 120);   // longer lens: product-render perspective, the ring reads as a true ellipse

  // Procedural studio environment: gradient dome + light cards, baked through PMREM. No textures.
  (function () {
    const es = new THREE.Scene();
    const dg = new THREE.SphereGeometry(30, 32, 16), dp = dg.attributes.position, dc = new Float32Array(dp.count * 3);
    const top = C('#9A9EA6'), mid = C('#2E3238'), bot = C('#07080A'), tmp = new THREE.Color();   // neutral dome: a cool dome greys out copper and garnet
    for (let i = 0; i < dp.count; i++) {
      const y = dp.getY(i) / 30;
      if (y >= 0) tmp.copy(mid).lerp(top, y); else tmp.copy(mid).lerp(bot, -y);
      dc[i * 3] = tmp.r; dc[i * 3 + 1] = tmp.g; dc[i * 3 + 2] = tmp.b;
    }
    dg.setAttribute('color', new THREE.BufferAttribute(dc, 3));
    es.add(new THREE.Mesh(dg, new THREE.MeshBasicMaterial({ side: THREE.BackSide, vertexColors: true })));
    function card(w, h, x, y, z, col) {
      const m = new THREE.Mesh(new THREE.PlaneGeometry(w, h), new THREE.MeshBasicMaterial({ color: col, side: THREE.DoubleSide }));
      m.position.set(x, y, z); m.lookAt(0, 0, 0); es.add(m);
    }
    card(22, 5, 0, 10, 6, 0xffffff);      // wide softbox above: the long specular streak across the facets
    card(6, 13, -12, 3, -2, 0xbfe6ee);
    card(5, 10, 11, -2, 6, 0xd6d9e0);
    card(3, 16, 9, 4, -10, 0xffffff);     // tall rim card behind right: edge light on the navy facet
    card(2.5, 2.5, 5, 7, 8, 0xffffff);    // small hot card front-right: a tight glint on the bevels and the ring crown
    card(12, 2, 0, -9, 5, 0x707a88);
    const pm = new THREE.PMREMGenerator(renderer);
    scene.environment = pm.fromScene(es, 0.035).texture;
    pm.dispose();
    es.traverse((o) => {
      if (o.geometry) o.geometry.dispose();
      if (o.material) o.material.dispose();
    });
  })();

  const rig = new THREE.Group(), tilt = new THREE.Group(), mark = new THREE.Group();
  scene.add(rig); rig.add(tilt); tilt.add(mark);

  // Facets from the SVG geometry (120u canvas -> units: x'=(x-60)/10, y'=(60-y)/10)
  function facet(pts) {
    const s = new THREE.Shape();
    pts.forEach((p, i) => { if (i) s.lineTo(p[0], p[1]); else s.moveTo(p[0], p[1]); });
    s.closePath();
    const g = new THREE.ExtrudeGeometry(s, { depth: 0.7, bevelEnabled: true, bevelThickness: 0.12, bevelSize: 0.09, bevelSegments: phone ? 3 : 6, curveSegments: 4 });
    g.translate(0, 0, -0.35);
    // Brand facets are light-at-top, saturated-at-bottom gradients: a brightness ramp in vertex colours (>1 brightens).
    const p = g.attributes.position, c = new Float32Array(p.count * 3);
    // u is clamped: the bevel pushes vertices 0.09 outside the shape, and pow(negative) is NaN, which paints whole triangles black.
    for (let i = 0; i < p.count; i++) {
      const u = Math.min(1, Math.max(0, (p.getY(i) + 4.4) / 9.14)), b = 0.55 + 0.5 * Math.pow(u, 1.2);
      c[i * 3] = c[i * 3 + 1] = c[i * 3 + 2] = b;
    }
    g.setAttribute('color', new THREE.BufferAttribute(c, 3));
    return g;
  }
  const matL = new THREE.MeshPhysicalMaterial({ color: W[0].c.facetL, vertexColors: true, metalness: 1, roughness: 0.2, clearcoat: 0.35, clearcoatRoughness: 0.08, envMapIntensity: 1.0, emissive: C('#000000') });
  const matR = new THREE.MeshStandardMaterial({ color: W[0].c.facetR, vertexColors: true, metalness: 1, roughness: 0.24, envMapIntensity: 1.0 });
  const mL = new THREE.Mesh(facet([[0, 4.74], [0, 0.4], [-2, -4.4], [-3.8, -4.4]]), matL);
  const mR = new THREE.Mesh(facet([[0, 4.74], [3.8, -4.4], [2, -4.4], [0, 0.4]]), matR);
  const FOLD = 0.3; mL.rotation.y = -FOLD; mR.rotation.y = FOLD; // legs recede, ridge nearest the viewer
  mL.castShadow = mR.castShadow = mL.receiveShadow = mR.receiveShadow = true;
  mark.add(mL, mR);

  const ridge = new THREE.Mesh(new THREE.BoxGeometry(0.06, 4.3, 0.06), new THREE.MeshBasicMaterial({ color: 0xffffff, transparent: true, opacity: 0.9 }));
  ridge.position.set(0, 2.57, 0.43); mark.add(ridge);

  // Orbit ring. Torus in its own plane, tilted so the top arc passes behind the shoulders and the bottom arc in front
  // of the legs, then rotated 12 degrees like the SVG. Back/front dimming is done in view depth (see onBeforeCompile),
  // so it stays right while the node travels and the camera drifts.
  const ringZ = new THREE.Group(); ringZ.position.set(0, -0.9, 0); ringZ.rotation.z = 12 * Math.PI / 180;
  const ringX = new THREE.Group(); ringX.rotation.x = -75 * Math.PI / 180;
  const spin = new THREE.Group();
  mark.add(ringZ); ringZ.add(ringX); ringX.add(spin);
  const matRing = new THREE.MeshStandardMaterial({ color: W[0].c.ring, metalness: 1, roughness: 0.2, emissive: W[0].c.ringEm, emissiveIntensity: 0.45, envMapIntensity: 1.0 });
  let ringShader = null;
  matRing.onBeforeCompile = function (sh) {
    sh.uniforms.uRingZ = { value: 30 };
    sh.fragmentShader = sh.fragmentShader
      .replace('uniform vec3 diffuse;', 'uniform vec3 diffuse; uniform float uRingZ;')
      .replace('vec4 diffuseColor = vec4( diffuse, opacity );', 'float auraBack = smoothstep(-0.6, 2.8, vViewPosition.z - uRingZ); vec4 diffuseColor = vec4( diffuse * mix(1.0, 0.3, auraBack), opacity );')
      .replace('vec3 totalEmissiveRadiance = emissive;', 'vec3 totalEmissiveRadiance = emissive * mix(1.0, 0.15, auraBack);');
    ringShader = sh;
  };
  const ringMesh = new THREE.Mesh(new THREE.TorusGeometry(3.6, 0.26, phone ? 16 : 32, phone ? 120 : 220), matRing);   // tube .26: a hair over the kit's 6/120 stroke so it reads at 500 px
  ringMesh.castShadow = ringMesh.receiveShadow = true;
  spin.add(ringMesh);

  // Energy node: emissive core + additive halos (dark worlds) + normal-blend halo (light world) + a real point light.
  const node = new THREE.Group(); node.position.set(3.6, 0, 0); spin.add(node);
  const core = new THREE.Mesh(new THREE.SphereGeometry(0.38, 28, 20), new THREE.MeshBasicMaterial({ color: W[0].c.node }));   // clears the .26 tube by .12 all round: a bead on the ring, not a lip around it
  node.add(core);
  const haloTex = (function () {
    const cv = document.createElement('canvas'); cv.width = cv.height = 128; const x = cv.getContext('2d');
    const g = x.createRadialGradient(64, 64, 0, 64, 64, 64);
    g.addColorStop(0, 'rgba(255,255,255,1)'); g.addColorStop(0.18, 'rgba(255,255,255,.75)'); g.addColorStop(0.45, 'rgba(255,255,255,.22)'); g.addColorStop(1, 'rgba(255,255,255,0)');
    x.fillStyle = g; x.fillRect(0, 0, 128, 128);
    return new THREE.CanvasTexture(cv);
  })();
  function sprite(scale, opacity, additive, depthTest, parent) {
    const s = new THREE.Sprite(new THREE.SpriteMaterial({ map: haloTex, color: W[0].c.halo, transparent: true, opacity: opacity, depthWrite: false, depthTest: depthTest, blending: additive ? THREE.AdditiveBlending : THREE.NormalBlending }));
    s.scale.set(scale, scale, 1); parent.add(s); return s;
  }
  const haloA = sprite(1.9, 0.6, true, false, node), haloB = sprite(5.2, 0.22, true, false, node), haloN = sprite(4.4, 0, false, false, node), haloT = sprite(1.5, 0, false, false, node);   // haloA no longer depth-tests: the tube was cutting the glow in half, which read as a white band
  const nodeLight = new THREE.PointLight(0xffffff, 1.2, 8, 2); node.add(nodeLight);

  // Soft ground shadow so the letter has weight
  const shadow = new THREE.Sprite(new THREE.SpriteMaterial({ map: haloTex, color: W[0].c.shadow, transparent: true, opacity: 0.55, depthWrite: false, blending: THREE.NormalBlending }));
  shadow.scale.set(7.6, 1.25, 1); shadow.position.set(0.1, -4.7, 0.6); mark.add(shadow);   // fully on screen now: tighter, so the jewel stands on something

  const hemi = new THREE.HemisphereLight(0xffffff, 0x000000, 0.45); scene.add(hemi);
  // Key light lives on the rig so its shadow frustum follows the mark. Its shadow is what sells "pierced":
  // the front arc shades the legs, the legs shade the back arc.
  const key = new THREE.DirectionalLight(0xffffff, 1.0); key.position.set(-5, 8, 9);
  key.castShadow = true; key.shadow.mapSize.set(phone ? 512 : 1024, phone ? 512 : 1024);
  key.shadow.camera.left = key.shadow.camera.bottom = -6.6; key.shadow.camera.right = key.shadow.camera.top = 6.6;   // covers the 1.14 station without clipping the ring's shadow
  key.shadow.camera.near = 1; key.shadow.camera.far = 40; key.shadow.bias = -0.0004; key.shadow.normalBias = 0.03;
  rig.add(key); rig.add(key.target);
  const rim = new THREE.DirectionalLight(0xffffff, 0.75); rim.position.set(7, 2, -6); scene.add(rim);

  // Layout: the page owns the stations. Desktop scale 1 = 58% of the viewport height; phone scale 1 = mark width 95% of the view.
  const view = { w: 1, h: 1, desktop: true };
  function resize() {
    // The canvas fills its host (the About band), not the viewport; the host is the whole page width, so `desktop` still follows the window.
    const host = canvas.parentElement, w = host.clientWidth || innerWidth, h = host.clientHeight || innerHeight, aspect = w / h, desktop = innerWidth >= 900;
    renderer.setSize(w, h, false); camera.aspect = aspect; camera.updateProjectionMatrix();
    const visH = desktop ? 9.14 / 0.58 : (7.66 / 0.95) / aspect;   // scale 1: mark height = 58% of the view (desktop); mark width = 95% of the view (phone)
    const d = visH / 2 / Math.tan(camera.fov * Math.PI / 360), elev = 6 * Math.PI / 180;
    camera.position.set(0, d * Math.sin(elev), d * Math.cos(elev)); camera.lookAt(0, 0, 0);
    view.w = visH * aspect; view.h = visH; view.desktop = desktop;   // the rig is placed every frame from the station
  }
  addEventListener('resize', resize); resize();

  let mouse = { x: 0, y: 0 }, tx = 0, ty = 0;
  const _v = new THREE.Vector3();
  const onPointer = (e) => {
    if (reduce) return;
    const f = e.pointerType === 'touch' ? 0.4 : 1;
    mouse.x = (e.clientX / innerWidth * 2 - 1) * f; mouse.y = (e.clientY / innerHeight * 2 - 1) * f;
  };
  addEventListener('pointermove', onPointer, { passive: true });

  // Colour blend: weighted sum of the world palettes. With two non-zero weights this is the prototype's lerp(A, B, te).
  const wsum = (name, out) => {
    out.setRGB(0, 0, 0);
    for (let i = 0; i < 4; i++) {
      const c = W[i].c[name], w = curMix[i];
      out.r += c.r * w; out.g += c.g * w; out.b += c.b * w;
    }
    return out;
  };
  function applyWorld() {
    wsum('facetL', matL.color);
    matL.emissive.copy(matL.color).multiplyScalar(0.08);
    wsum('facetR', matR.color);
    wsum('ring', matRing.color);
    wsum('ringEm', matRing.emissive);
    wsum('node', core.material.color);
    wsum('halo', haloA.material.color);
    haloB.material.color.copy(haloA.material.color);
    haloN.material.color.copy(haloA.material.color); wsum('node', haloT.material.color);
    // The point light takes the deeper "glow" tone where a world defines one, so lit metal keeps its hue instead of washing to pink.
    wsum('glow', nodeLight.color);
    wsum('key', key.color);
    wsum('rim', rim.color);
    wsum('sky', hemi.color);
    wsum('ground', hemi.groundColor);
    let light = 0, exp = 0;
    for (let i = 0; i < 4; i++) { light += W[i].light * curMix[i]; exp += W[i].exp * curMix[i]; }
    hemi.intensity = 0.45 + 0.05 * light; key.intensity = 1.0 - 0.3 * light;
    ridge.material.color.copy(core.material.color);
    core.material.color.multiplyScalar(1.25 + 0.6 * light);   // pearl: a value above 1.0 survives ACES as true white; on porcelain a 1.0 white maps to grey
    // Cobalt on porcelain: in the light room the facets become glazed ceramic (dielectric, clearcoated) and the ring deep glass,
    // so the mark keeps saturation against the pale field instead of washing out as a pale relief did.
    matR.metalness = 1 - 0.85 * light; matR.roughness = 0.24 + 0.2 * light;
    matL.metalness = 1 - 0.9 * light; matL.roughness = 0.2 + 0.04 * light; matL.clearcoat = 0.35 + 0.65 * light; matL.envMapIntensity = 1.0 - 0.4 * light;
    matRing.metalness = 1 - 0.75 * light; matRing.roughness = 0.2 + 0.1 * light; matRing.envMapIntensity = 1.0 - 0.6 * light;
    haloB.material.opacity = 0.2 * (1 - light);
    haloN.material.opacity = 0.4 * light; haloT.material.opacity = 0.55 * light;   // light room: a tight white corona plus the wide sky halo, so the bead glows on porcelain too
    wsum('shadow', shadow.material.color);
    shadow.material.opacity = 0.55 - 0.23 * light;
    renderer.toneMappingExposure = exp;
  }

  // Targets (set by the page) and eased current values (advanced by the loop).
  const tgtMix = [1, 0, 0, 0], curMix = [1, 0, 0, 0];
  const tgtSt = { x: 0.73, y: 0.54, s: 1 }, curSt = { x: 0.73, y: 0.54, s: 1 };
  let stationSeen = false, tgtProg = 0, curProg = 0;

  function setProgress(f) {
    tgtProg = Math.min(1, Math.max(0, f));
  }

  function setMix(mix) {
    for (let i = 0; i < 4; i++) tgtMix[i] = mix[WORLD_KEYS[i]] || 0;
  }
  function setStation(st) {
    tgtSt.x = st.x; tgtSt.y = st.y; tgtSt.s = st.scale;
    if (!stationSeen) { stationSeen = true; curSt.x = st.x; curSt.y = st.y; curSt.s = st.scale; }   // first placement is a snap, not a glide from the default
  }

  // Arrival: the mark rises into place on first paint. Under reduced motion it is instant.
  let born = null;
  function draw(now) {
    if (born === null) born = now;
    const age = reduce ? 1 : Math.min(1, (now - born) / 1800), ea = 1 - Math.pow(1 - age, 3);
    applyWorld();
    // Station -> world units at the rig depth. Desktop x is clamped so the ring's outer edge stays >= .37 units inside tall
    // viewports. y: the mark's own centre is +.17*s above its origin.
    // Phone: the station's x and y are ignored, as in the prototype: centred, apex at mid-height (origin = apex - 4.74*s). Only scale applies.
    const flat = phone || !view.desktop, f = curProg;
    const s = curSt.s, fx = flat ? 0 : curSt.x - 0.5;
    let bx = fx * view.w;
    if (!flat) bx = (fx < 0 ? -1 : 1) * Math.min(Math.abs(bx), view.w / 2 - 4.2 * s);
    const by = flat ? -4.74 * s : (0.5 - curSt.y) * view.h - 0.17 * s;
    mark.scale.setScalar(s);
    // Scroll: the node sweeps the front arc from the right seam toward the centre.
    spin.rotation.z = -f * Math.PI * 0.6;
    rig.position.set(bx, by + f * 0.5 - (1 - ea) * 0.8, 0);   // small rise and arrival drop on top of the station
    const t = now / 1000;
    let idleY = 0, idleR = 0, idleX = 0;
    if (!reduce) {
      idleY = Math.sin(t * 0.9) * 0.04; idleR = Math.sin(t * 0.45) * 0.06; idleX = Math.cos(t * 0.7) * 0.01;   // slow turntable sway, +-3.4 degrees over ~14 s, plus a small bob
      // Facet Catch: every 9 s the key light swings from x=-5 to 0 and back over 1.4 s, a highlight travelling across the metal.
      const cyc = t % 9, sw = Math.min(1, cyc / 1.4), e = sw * sw * (3 - 2 * sw); key.position.x = -5 + 5 * Math.sin(e * Math.PI);
    }
    tilt.position.y = idleY;
    tx += ((mouse.x * 0.12 + idleR - 0.04 - fx * 0.4 + f * 0.26 - (1 - ea) * 0.3) - tx) * 0.05;   // a slight yaw toward the copy column at every station
    ty += ((-mouse.y * 0.07 + idleX) - ty) * 0.05;
    tilt.rotation.y = tx; tilt.rotation.x = ty;
    if (!reduce) haloA.material.opacity = 0.54 + 0.08 * Math.sin(t * 2.2);
    camera.updateMatrixWorld(); camera.matrixWorldInverse.copy(camera.matrixWorld).invert();
    ringZ.getWorldPosition(_v).applyMatrix4(camera.matrixWorldInverse);
    if (ringShader) ringShader.uniforms.uRingZ.value = -_v.z;
    renderer.render(scene, camera);
  }

  // One loop. Pauses when the tab is hidden; under reduced motion it renders only when something changed.
  let running = true, last = 0, raf = 0, lastKey = -1;
  function frame(now) {
    if (!running) return;
    raf = requestAnimationFrame(frame);
    const dt = Math.min(0.1, (now - last) / 1000) || 0.016; last = now;   // clamp at 100 ms: a slow software GL still converges within ~3 s instead of ~10 s; identical at 60 fps
    const k = 1 - Math.pow(1 - (reduce ? 0.2 : 0.085), dt * 60);
    for (let i = 0; i < 4; i++) {
      curMix[i] += (tgtMix[i] - curMix[i]) * k;
      if (Math.abs(tgtMix[i] - curMix[i]) < 0.0004) curMix[i] = tgtMix[i];
    }
    curSt.x += (tgtSt.x - curSt.x) * k; curSt.y += (tgtSt.y - curSt.y) * k; curSt.s += (tgtSt.s - curSt.s) * k;
    if (Math.abs(tgtSt.x - curSt.x) < 0.0002) curSt.x = tgtSt.x;
    if (Math.abs(tgtSt.y - curSt.y) < 0.0002) curSt.y = tgtSt.y;
    if (Math.abs(tgtSt.s - curSt.s) < 0.0004) curSt.s = tgtSt.s;
    curProg += (tgtProg - curProg) * k;
    if (Math.abs(tgtProg - curProg) < 0.0002) curProg = tgtProg;
    if (reduce) {
      const keyv = curProg * 17 + curMix[0] * 1000 + curMix[1] * 2000 + curMix[2] * 3000 + curMix[3] * 4000 + curSt.x * 7 + curSt.y * 11 + curSt.s * 13;
      if (keyv === lastKey) return;
      lastKey = keyv;
    }
    draw(now);
  }
  const onVisibility = () => {
    running = !document.hidden;
    if (running) { last = performance.now(); cancelAnimationFrame(raf); raf = requestAnimationFrame(frame); }
  };
  document.addEventListener('visibilitychange', onVisibility);
  raf = requestAnimationFrame(frame);

  function dispose() {
    running = false;
    cancelAnimationFrame(raf);
    removeEventListener('resize', resize);
    removeEventListener('pointermove', onPointer);
    document.removeEventListener('visibilitychange', onVisibility);
    scene.traverse((o) => {
      if (o.geometry) o.geometry.dispose();
      if (o.material) {
        const ms = Array.isArray(o.material) ? o.material : [o.material];
        ms.forEach((m) => m.dispose());
      }
    });
    haloTex.dispose();
    if (scene.environment) scene.environment.dispose();
    renderer.dispose();
  }

  return { setMix, setStation, setProgress, dispose };
}
