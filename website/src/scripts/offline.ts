// The offline switch (directions.md 8.5). Server-rendered online; this makes it work. Money is integer millidinars,
// written with toFixed(3) (never toLocaleString, which would turn the digits Arabic-Indic). All words come from the
// section's data-* attributes. Reduced motion: no flying chips, numbers jump at once, no autoplay.
const AMOUNTS = [750, 1850, 2600, 900, 3250];
const BILL_START = 2051;
const KEEP = 4;
const STAGGER = 160;
const FLY_MS = 600;
const TOAST_MS = 2400;
const IDLE_MS = 4000;
const PLAN: Array<[number, 'ring' | 'off' | 'on']> = [[0, 'ring'], [1200, 'off'], [2400, 'ring'], [3400, 'ring'], [5000, 'on']];
const CHECK = '<path d="M5 12l4 4 10-10"/>';
const CLOCK = '<circle cx="12" cy="12" r="8"/><path d="M12 8v4l3 2"/>';
const SVG = (p: string) => `<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${p}</svg>`;

const root = document.querySelector<HTMLElement>('[data-offline]');
if (root) init(root);

function init(root: HTMLElement) {
  const q = <T extends Element = HTMLElement>(s: string) => root.querySelector<T>(`[data-o-${s}]`)!;
  const d = root.dataset;
  const demo = q('demo'), sw = q('switch'), path = q<SVGPathElement>('path'), sales = q('sales'), state = q('state');
  const chip = q('chip'), chipText = q('chiptext'), badge = q('badge'), bn = q('bn'), till = q('till'), phone = q('phone');
  const scr = q('scr'), line = q('line'), ptotal = q('ptotal'), bars = q('bars'), toastEl = q('toast');
  const vline = q('vline'), live = q('live');

  // Read at action time, so a visitor who flips the setting mid-visit is honoured.
  const reduced = () => matchMedia('(prefers-reduced-motion: reduce)').matches;
  const fmt = (m: number) => (m / 1000).toFixed(3);

  interface Sale { no: number; a: number; st: HTMLElement }
  let online = true, bill = BILL_START, phoneSum = 0, count = 0, auto = true, run = 0;
  const queue: Sale[] = [];
  const timers: number[] = [];

  // Count the phone total up (cubic ease-out). The last frame always writes the exact target.
  function tween(el: HTMLElement, from: number, to: number) {
    const id = ++run;
    if (reduced() || from === to) { el.textContent = fmt(to); return; }
    const t0 = performance.now();
    const step = (t: number) => {
      if (id !== run) return;
      const k = Math.min(1, Math.max(0, (t - t0) / 500));
      el.textContent = fmt(k < 1 ? from + (to - from) * (1 - Math.pow(1 - k, 3)) : to);
      if (k < 1) requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  }

  // The cable starts at the till's edge that faces the phone and ends at the phone's edge that faces the till, on
  // whichever side each sits in the current direction. It always starts at the till, so the dashes flow from the
  // till to the phone in RTL and LTR alike.
  function geom() {
    const dr = demo.getBoundingClientRect(), t = till.getBoundingClientRect(), p = phone.getBoundingClientRect();
    const right = p.left > t.left;
    const x0 = (right ? t.right : t.left) - dr.left, x1 = (right ? p.left : p.right) - dr.left;
    const y0 = t.top - dr.top + t.height / 2, y1 = p.top - dr.top + p.height / 2, s = right ? 1 : -1;
    path.parentElement!.setAttribute('viewBox', `0 0 ${dr.width} ${dr.height}`);
    path.setAttribute('d', `M ${x0} ${y0} C ${x0 + s * 40} ${y0 + 40}, ${x1 - s * 40} ${y1 + 40}, ${x1} ${y1}`);
    cable();
  }
  // Offline the dash pattern becomes two long runs with a visible gap in the middle (46% 8% 46% of the real length).
  function cable() {
    const L = path.getTotalLength();
    path.style.strokeDasharray = online || !L ? '' : `${L * 0.46} ${L * 0.08} ${L * 0.46}`;
    path.classList.toggle('of-off', !online);
    vline.classList.toggle('of-off', !online);
  }

  function renderLine() {
    const n = document.createElement('span');
    n.className = 'num';
    n.textContent = online ? String(count) : '10:41';
    if (online) n.dataset.oPcount = '';
    line.replaceChildren(online ? d.count! : d.last!, n);
  }
  function renderState() {
    sw.setAttribute('aria-checked', String(online));
    state.textContent = online ? d.on! : d.off!;
    state.className = 'of-ss ' + (online ? 'of-on' : 'of-offc');
    chip.className = 'lt-chip' + (online ? '' : ' of-chip-off');
    chipText.textContent = online ? d.chipOn! : d.chipOff!;
    renderLine();
    cable();
  }
  function renderBadge() {
    bn.textContent = String(queue.length);
    badge.classList.toggle('of-show', queue.length > 0);
  }
  function mark(s: Sale, sent: boolean) {
    const word = document.createElement('span');
    word.textContent = sent ? d.sent! : d.queued!;
    word.className = sent ? 'visually-hidden' : '';
    s.st.className = 'of-st' + (sent ? ' of-ok' : '');
    s.st.innerHTML = SVG(sent ? CHECK : CLOCK);
    s.st.append(word);
  }

  function addRow(s: Sale) {
    q('empty')?.remove();
    const li = document.createElement('li');
    if (!reduced()) li.className = 'of-in';
    const no = document.createElement('span'), amt = document.createElement('span'), a = document.createElement('span');
    no.className = 'num';
    no.textContent = '#' + s.no;
    amt.className = 'of-amt';
    a.className = 'num';
    a.textContent = fmt(s.a);
    amt.append(a, ' ' + d.cur);
    li.append(no, amt, s.st);
    sales.prepend(li);
    mark(s, online);
    while (sales.children.length > KEEP) sales.lastElementChild!.remove();
  }

  function arrive(s: Sale) {
    const old = phoneSum;
    phoneSum += s.a;
    count++;
    tween(ptotal, old, phoneSum);
    if (online) renderLine();
    mark(s, true);
    const last = bars.lastElementChild as HTMLElement;
    last.style.height = Math.min(64, (parseFloat(last.style.height) || 8) + 6) + 'px';
    if (!reduced()) {
      scr.classList.remove('lt-ping');
      void scr.offsetWidth;
      scr.classList.add('lt-ping');
    }
  }

  const mid = (el: HTMLElement, box: DOMRect) => { const r = el.getBoundingClientRect(); return { x: r.left - box.left + r.width / 2, y: r.top - box.top + r.height / 2 }; };
  // A 12px copper square travels the cable (or, where the cable is hidden on phones, a straight line) in 600ms.
  function fly(done: () => void) {
    if (reduced()) { done(); return; }
    const box = demo.getBoundingClientRect(), a = mid(till, box), b = mid(phone, box), L = path.getTotalLength();
    const straight = !L || getComputedStyle(path.parentElement!).display === 'none';
    const dot = document.createElement('div');
    dot.className = 'of-fly';
    demo.append(dot);
    const t0 = performance.now();
    const f = (t: number) => {
      const k = Math.min(1, Math.max(0, (t - t0) / FLY_MS)), e = k < .5 ? 2 * k * k : 1 - Math.pow(-2 * k + 2, 2) / 2;
      const p = straight ? { x: a.x + (b.x - a.x) * e, y: a.y + (b.y - a.y) * e } : path.getPointAtLength(L * e);
      dot.style.transform = `translate(${p.x}px,${p.y}px)`;
      if (k < 1) requestAnimationFrame(f); else { dot.remove(); done(); }
    };
    requestAnimationFrame(f);
  }

  function toast(n: number) {
    const text = (n === 1 ? d.t1! : n === 2 ? d.t2! : n <= 10 ? d.t3! : d.t11!).replace('{n}', String(n));
    toastEl.textContent = text;
    toastEl.classList.add('of-show');
    setTimeout(() => toastEl.classList.remove('of-show'), TOAST_MS);
    live.textContent = text;
  }

  function ring() {
    const s: Sale = { no: bill++, a: AMOUNTS[Math.floor(Math.random() * AMOUNTS.length)], st: document.createElement('span') };
    addRow(s);
    if (online) fly(() => arrive(s)); else { queue.push(s); renderBadge(); }
  }
  function setOnline(v: boolean) {
    online = v;
    renderState();
    live.textContent = v ? d.annOn! : d.annOff!;
    if (!v || !queue.length) return;
    const batch = queue.splice(0);
    renderBadge();
    // Reduced motion: no stagger and no timers, every number is final in the same task as the click.
    batch.forEach((s, i) => {
      const go = () => fly(() => { arrive(s); if (i === batch.length - 1) toast(batch.length); });
      if (reduced()) go(); else setTimeout(go, i * STAGGER);
    });
  }

  sw.addEventListener('click', () => setOnline(!online));
  q('ring').addEventListener('click', ring);
  addEventListener('resize', geom);
  if ('ResizeObserver' in window) new ResizeObserver(geom).observe(demo);
  document.fonts?.ready.then(geom);
  geom();
  renderState();

  // Autoplay: once, 4s after the section is first in view, only if the visitor has not touched it. Any pointerdown or
  // key press inside the section hands control over for good and cancels everything still pending.
  const stop = () => { auto = false; timers.forEach(clearTimeout); };
  root.addEventListener('pointerdown', stop);
  root.addEventListener('keydown', stop);
  if (!reduced() && 'IntersectionObserver' in window) {
    const io = new IntersectionObserver((es) => {
      if (!es[0].isIntersecting) return;
      io.disconnect();
      timers.push(window.setTimeout(() => {
        if (!auto || reduced()) return;
        PLAN.forEach(([ms, act]) => timers.push(window.setTimeout(() => {
          if (auto) act === 'ring' ? ring() : setOnline(act === 'on');
        }, ms)));
      }, IDLE_MS));
    }, { threshold: 0.4 });
    io.observe(root);
  }
}

// Marks this file as a module: without an import/export TypeScript treats every script as one shared global scope and
// reports the top-level `root`/`init` of each as a redeclaration of the others. The bundler already treated it as a module.
export {};
