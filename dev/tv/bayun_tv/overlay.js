// Bayun overlay for the TV kiosk: an animated glow that shows the voice loop phase,
// a caption with what was heard or said, and numbered hints for screen control.
// Injected over CDP; no innerHTML anywhere (YouTube enforces Trusted Types).
(() => {
  if (location.port === '8770') return;
  const VERSION = 6;
  const old = window.__bayunOverlay;
  if (old && old.version >= VERSION) return;
  if (old && old.destroy) old.destroy();

  const PALETTES = {
    listening: ['#46c8ff', '#7a6bff', '#2fe0cf'],
    thinking: ['#b57bff', '#ffb547', '#ff6fae'],
    speaking: ['#34e0a0', '#3fb4ff', '#b4f56a'],
    error: ['#ff5a5f', '#ff9a52', '#ff3d6e'],
    loading: ['#8fa3ff', '#b57bff', '#46c8ff'],
    idle: ['#46c8ff', '#7a6bff', '#2fe0cf'],
  };
  const ENERGY = {listening: 0.55, thinking: 0.5, speaking: 0.75, error: 0.6, loading: 0.4, idle: 0};
  const SPEED = {listening: 1.0, thinking: 2.2, speaking: 1.4, error: 0.8, loading: 1.6, idle: 0.6};
  const VISIBLE = new Set(['listening', 'thinking', 'speaking', 'error', 'loading']);
  const BRIEF = new Set(['error', 'loading']);

  const host = document.createElement('div');
  host.id = 'bayun-overlay';
  host.style.cssText = 'position:fixed;inset:0;pointer-events:none;z-index:2147483647;contain:strict';
  const root = host.attachShadow({mode: 'open'});
  const style = document.createElement('style');
  style.textContent = `
    :host { all: initial; }
    canvas { position:absolute; pointer-events:none; opacity:0; transition:opacity .35s ease; }
    canvas.on { opacity:1; }
    .aurora { left:0; right:0; bottom:0; width:100%; height:30vh; filter:blur(.7vh) saturate(1.3); }
    .orb { left:50%; bottom:2vh; width:30vh; height:30vh; margin-left:-15vh; filter:blur(.6vh) saturate(1.3); }
    .edge { inset:0; width:100%; height:100%; filter:blur(.9vh) saturate(1.2); }
    .caption { position:absolute; left:50%; transform:translate(-50%, 1vh); bottom:var(--cap-bottom, 9vh);
      max-width:66vw; padding:1.1vh 2.6vh; border-radius:2.4vh;
      background:rgba(10,12,18,.42); backdrop-filter:blur(2.2vh) saturate(1.4);
      -webkit-backdrop-filter:blur(2.2vh) saturate(1.4);
      box-shadow:0 .6vh 3vh rgba(0,0,0,.35), inset 0 0 0 1px rgba(255,255,255,.08);
      color:#f6f4ef; font:600 3.1vh/1.32 "Inter","SF Pro Display","Segoe UI",system-ui,sans-serif;
      letter-spacing:.01em; text-align:center; text-shadow:0 .2vh .8vh rgba(0,0,0,.55);
      display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical; overflow:hidden;
      opacity:0; transition:opacity .3s ease, transform .3s ease; }
    .caption.on { opacity:1; transform:translate(-50%, 0); }
    .caption .who { display:block; font-size:1.7vh; font-weight:600; letter-spacing:.12em;
      text-transform:uppercase; opacity:.62; margin-bottom:.3vh; }
    .hint { position:absolute; min-width:3.4vh; height:3.4vh; padding:0 .8vh; border-radius:1.7vh;
      background:#ffd84a; color:#121212; font:800 2.1vh/3.4vh "Inter",system-ui,sans-serif; text-align:center;
      box-shadow:0 .3vh 1.2vh rgba(0,0,0,.5), 0 0 0 .25vh rgba(18,18,18,.85); transform:translate(-30%, -30%); }
    .hint.sel { background:#46c8ff; color:#04121c; }
    .sel-box { position:absolute; border-radius:1vh; box-shadow:0 0 0 .4vh #46c8ff, 0 0 3vh #46c8ff;
      transition:all .2s ease; }
  `;
  const canvases = {};
  for (const name of ['aurora', 'orb', 'edge']) {
    const c = document.createElement('canvas');
    c.className = name;
    canvases[name] = c;
  }
  const caption = document.createElement('div');
  caption.className = 'caption';
  const who = document.createElement('span');
  who.className = 'who';
  const said = document.createElement('span');
  caption.append(who, said);
  const hintLayer = document.createElement('div');
  root.append(style, ...Object.values(canvases), caption, hintLayer);

  const attach = () => {
    const parent = document.fullscreenElement || document.documentElement;
    if (parent && host.parentNode !== parent) parent.appendChild(host);
  };
  document.addEventListener('fullscreenchange', attach);
  if (document.documentElement) attach(); else document.addEventListener('DOMContentLoaded', attach);

  // ---- animated state ----------------------------------------------------
  const hex = h => [1, 3, 5].map(i => parseInt(h.slice(i, i + 2), 16));
  const lerp = (a, b, k) => a + (b - a) * k;
  const st = {
    variant: 'aurora', phase: 'idle', alpha: 0, targetAlpha: 0,
    colors: PALETTES.idle.map(hex), energy: 0, speed: 0.6, level: 0, levelTarget: 0, t: 0,
  };
  let raf = 0, last = 0, briefTimer = 0;

  const rgba = (c, a) => `rgba(${c[0] | 0},${c[1] | 0},${c[2] | 0},${a})`;

  function sizeCanvas(c, scale) {
    // Soft glows are rendered at low resolution and stretched: cheap on 4K and looks the same.
    const r = c.getBoundingClientRect();
    const w = Math.max(64, Math.round(r.width * scale)), h = Math.max(32, Math.round(r.height * scale));
    if (c.width !== w || c.height !== h) { c.width = w; c.height = h; }
    return [w, h];
  }

  function envelope() {
    // Speech-like loudness when the voice loop does not report a level.
    const t = st.t;
    return 0.45 + 0.3 * Math.sin(t * 7.3) * Math.sin(t * 2.1) + 0.25 * Math.sin(t * 13.7 + 1.3) ** 2;
  }

  function drawAurora(c) {
    // Siri-like bottom glow: drifting colour pools along the edge plus thin luminous ribbons.
    const [w, h] = sizeCanvas(c, 0.25);
    const g = c.getContext('2d');
    g.clearRect(0, 0, w, h);
    g.globalCompositeOperation = 'lighter';
    const e = st.energy * (0.55 + 0.9 * st.level);
    for (let i = 0; i < 3; i++) {
      const col = st.colors[i];
      const cx = w * (0.5 + 0.38 * Math.sin(st.t * st.speed * (0.23 + 0.11 * i) + i * 2.1));
      const rx = w * (0.32 + 0.08 * Math.sin(st.t * 0.7 + i)), ry = h * (0.55 + 0.45 * e);
      g.save();
      g.translate(cx, h);
      g.scale(rx / ry, 1);
      const pool = g.createRadialGradient(0, 0, 0, 0, 0, ry);
      pool.addColorStop(0, rgba(col, 0.7));
      pool.addColorStop(0.45, rgba(col, 0.32));
      pool.addColorStop(1, rgba(col, 0));
      g.fillStyle = pool;
      g.beginPath();
      g.arc(0, 0, ry, Math.PI, 2 * Math.PI);
      g.fill();
      g.restore();
    }
    g.lineCap = 'round';
    for (let i = 0; i < 3; i++) {
      const col = st.colors[(i + 1) % 3];
      const base = h * (0.72 - 0.1 * i);
      const amp = h * (0.05 + 0.2 * e) * (1 - i * 0.2);
      const f1 = (2.2 + i * 0.9) * Math.PI / w, f2 = (4.1 + i * 1.7) * Math.PI / w;
      const ph = st.t * st.speed * (1.1 + i * 0.4) + i * 1.7;
      g.beginPath();
      for (let x = -4; x <= w + 4; x += 3) {
        const env = Math.sin(Math.PI * Math.min(1, Math.max(0, x / w)));  // ribbons taper at the sides
        const y = base - amp * env * (0.65 * Math.sin(x * f1 + ph) + 0.35 * Math.sin(x * f2 - ph * 1.4));
        if (x < 0) g.moveTo(x, y); else g.lineTo(x, y);
      }
      g.strokeStyle = rgba(col, 0.75);
      g.lineWidth = h * (0.035 + 0.03 * e);
      g.stroke();
      g.strokeStyle = 'rgba(255,255,255,0.35)';
      g.lineWidth = h * 0.012;
      g.stroke();
    }
  }

  function drawOrb(c) {
    const [w, h] = sizeCanvas(c, 0.5);
    const g = c.getContext('2d');
    g.clearRect(0, 0, w, h);
    g.globalCompositeOperation = 'lighter';
    const cx = w / 2, cy = h / 2, R = Math.min(w, h) * 0.38;
    const e = st.energy * (0.5 + 0.9 * st.level);
    const halo = g.createRadialGradient(cx, cy, R * 0.6, cx, cy, R * (1.9 + 0.5 * e));
    halo.addColorStop(0, rgba(st.colors[0], 0.35));
    halo.addColorStop(1, rgba(st.colors[1], 0));
    g.fillStyle = halo;
    g.fillRect(0, 0, w, h);
    for (let i = 0; i < 3; i++) {
      const a = st.t * st.speed * (0.8 + i * 0.45) + i * 2.09;
      const d = R * (0.12 + 0.22 * e);
      const x = cx + Math.cos(a) * d, y = cy + Math.sin(a * 1.17) * d;
      const r = R * (0.85 + 0.25 * Math.sin(st.t * 2.3 + i) * e);
      const grad = g.createRadialGradient(x, y, 0, x, y, r);
      grad.addColorStop(0, rgba(st.colors[i], 0.95));
      grad.addColorStop(0.55, rgba(st.colors[i], 0.45));
      grad.addColorStop(1, rgba(st.colors[i], 0));
      g.fillStyle = grad;
      g.beginPath();
      g.arc(x, y, r, 0, Math.PI * 2);
      g.fill();
    }
    const hl = g.createRadialGradient(cx - R * 0.25, cy - R * 0.3, 0, cx, cy, R * 0.9);
    hl.addColorStop(0, 'rgba(255,255,255,0.55)');
    hl.addColorStop(0.4, 'rgba(255,255,255,0.08)');
    hl.addColorStop(1, 'rgba(255,255,255,0)');
    g.fillStyle = hl;
    g.beginPath();
    g.arc(cx, cy, R * 0.9, 0, Math.PI * 2);
    g.fill();
  }

  function drawEdge(c) {
    const [w, h] = sizeCanvas(c, 0.25);
    const g = c.getContext('2d');
    g.clearRect(0, 0, w, h);
    const e = st.energy * (0.6 + 0.8 * st.level);
    const grad = g.createConicGradient(st.t * st.speed * 0.9, w / 2, h / 2);
    const cols = [...st.colors, st.colors[0]];
    cols.forEach((col, i) => grad.addColorStop(i / (cols.length - 1), rgba(col, 0.95)));
    g.strokeStyle = grad;
    g.lineWidth = h * (0.035 + 0.05 * e);
    g.strokeRect(0, 0, w, h);
    g.lineWidth = h * 0.012;
    g.globalAlpha = 0.9;
    g.strokeRect(g.lineWidth / 2, g.lineWidth / 2, w - g.lineWidth, h - g.lineWidth);
    g.globalAlpha = 1;
  }

  const DRAW = {aurora: drawAurora, orb: drawOrb, edge: drawEdge};
  const CAPTION_BOTTOM = {aurora: '11vh', orb: '31vh', edge: '6vh'};

  function frame(now, manual = false) {
    const dt = Math.max(0, Math.min(0.05, last ? (now - last) / 1000 : 0.016));
    last = now;
    st.t += dt;
    const k = 1 - Math.exp(-dt / 0.3);  // ~300 ms transitions
    const target = PALETTES[st.phase] || PALETTES.idle;
    st.colors = st.colors.map((c, i) => c.map((v, j) => lerp(v, hex(target[i])[j], k)));
    st.energy = lerp(st.energy, ENERGY[st.phase] ?? 0, k);
    st.speed = lerp(st.speed, SPEED[st.phase] ?? 1, k);
    const lt = st.phase === 'speaking' && st.levelTarget === 0 ? envelope() : st.levelTarget;
    st.level = lerp(st.level, lt, 1 - Math.exp(-dt / 0.08));
    const c = canvases[st.variant];
    DRAW[st.variant](c);
    if (manual) return;
    if (st.targetAlpha === 0 && !c.classList.contains('on')) {
      // fully faded: stop drawing until the next phase change
      raf = 0;
      last = 0;
      return;
    }
    raf = requestAnimationFrame(frame);
  }

  function setVisible(on) {
    st.targetAlpha = on ? 1 : 0;
    for (const [name, c] of Object.entries(canvases)) c.classList.toggle('on', on && name === st.variant);
    if (on && !raf) raf = requestAnimationFrame(frame);
    if (!on) setTimeout(() => { if (st.targetAlpha === 0 && raf) { cancelAnimationFrame(raf); raf = 0; last = 0; } }, 450);
  }

  function setCaption(phase, text) {
    const show = VISIBLE.has(phase) && phase !== 'loading' && !!text;
    caption.classList.toggle('on', show);
    if (show) {
      who.textContent = phase === 'speaking' ? 'Баюн' : phase === 'error' ? 'Ошибка' : 'Вы';
      said.textContent = text;
    }
    caption.style.setProperty('--cap-bottom', CAPTION_BOTTOM[st.variant]);
  }

  // ---- hints for screen control -----------------------------------------
  function hints(items, selected) {
    while (hintLayer.firstChild) hintLayer.firstChild.remove();
    if (!items) return;
    for (const it of items) {
      const b = document.createElement('div');
      b.className = 'hint' + (it.n === selected ? ' sel' : '');
      b.textContent = String(it.n);
      b.style.left = `${it.x}px`;
      b.style.top = `${it.y}px`;
      hintLayer.appendChild(b);
      if (it.n === selected) {
        const box = document.createElement('div');
        box.className = 'sel-box';
        Object.assign(box.style, {left: `${it.x}px`, top: `${it.y}px`, width: `${it.w}px`, height: `${it.h}px`});
        hintLayer.appendChild(box);
      }
    }
  }

  window.__bayunOverlay = {
    version: VERSION,
    set(phase, text) {
      attach();
      if (phase !== st.phase) {
        clearTimeout(briefTimer);
        st.phase = phase;
        if (BRIEF.has(phase)) briefTimer = setTimeout(() => { setVisible(false); caption.classList.remove('on'); }, 2000);
        setVisible(VISIBLE.has(phase));
      }
      setCaption(phase, text);
    },
    level(v) { st.levelTarget = Math.max(0, Math.min(1, +v || 0)); },
    variant(name) {
      if (!DRAW[name]) return;
      st.variant = name;
      setVisible(VISIBLE.has(st.phase));
      setCaption(st.phase, said.textContent);
    },
    hints,
    destroy() { cancelAnimationFrame(raf); host.remove(); document.removeEventListener('fullscreenchange', attach); },
    // for screenshots: jump straight to the target look
    settle(seconds) {
      let now = last || performance.now();
      for (let i = 0; i < seconds * 60; i++) frame(now += 16.7, true);
    },
  };
})();
