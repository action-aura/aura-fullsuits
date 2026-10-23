// The hero till (directions.md 8.4). The markup is server-rendered; this makes it sell. Money is kept in integer
// millidinars (JOD has 3 decimals), so a bill of three items can never drift by a float error, and every number is
// written with toFixed(3), never toLocaleString (which would turn the digits Arabic-Indic).
const BILL_START = 1042;
const BANNER_MS = 1600;
const FLASH_MS = 300;
const BAR_STEP = 6;
const BAR_MAX = 64;

const root = document.querySelector<HTMLElement>('[data-till]');
if (root) init(root);

function init(root: HTMLElement) {
  const q = <T extends HTMLElement>(s: string) => root.querySelector<T>(s)!;
  const linesEl = q('[data-lines]'), totalEl = q('[data-total]'), pay = q<HTMLButtonElement>('[data-pay]');
  const done = q('[data-done]'), doneSum = q('[data-donesum]'), billNo = q('[data-billno]');
  const pTotal = q('[data-ptotal]'), pCount = q('[data-pcount]'), bars = q('[data-bars]'), pscr = q('[data-pscr]');
  const live = q('[data-live]');
  const d = root.dataset;

  // Read at action time, so a visitor who flips the setting mid-visit is honoured.
  const reduced = () => matchMedia('(prefers-reduced-motion: reduce)').matches;
  const fmt = (m: number) => (m / 1000).toFixed(3);
  const toMillis = (s: string) => Math.round(parseFloat(s) * 1000);
  const say = (tpl: string, m: number) => { live.textContent = tpl.replace('{n}', fmt(m)); };

  const cart = new Map<string, { price: number; qty: number }>();
  let total = 0, dayTotal = 0, count = 0, bill = BILL_START, busy = false;
  const tweens = new WeakMap<HTMLElement, number>();

  // Count a number up (cubic ease-out). A newer tween on the same element cancels the older one, and the last frame
  // always writes the exact target. (A frame's timestamp can precede t0, hence the clamp at 0: unclamped it showed -0.088.) Reduced motion writes it at once with no in-between values.
  function tween(el: HTMLElement, from: number, to: number, ms = 400) {
    const id = (tweens.get(el) ?? 0) + 1;
    tweens.set(el, id);
    if (reduced() || from === to) { el.textContent = fmt(to); return; }
    const t0 = performance.now();
    const step = (t: number) => {
      if (tweens.get(el) !== id) return;
      const k = Math.min(1, Math.max(0, (t - t0) / ms)), e = 1 - Math.pow(1 - k, 3);
      el.textContent = fmt(k < 1 ? from + (to - from) * e : to);
      if (k < 1) requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  }

  function render(newName?: string) {
    linesEl.textContent = '';
    if (!cart.size) {
      const li = document.createElement('li');
      li.className = 'lt-empty';
      li.textContent = d.empty ?? '';
      linesEl.append(li);
      return;
    }
    cart.forEach((v, name) => {
      const li = document.createElement('li');
      if (name === newName && v.qty === 1 && !reduced()) li.className = 'lt-new';
      const a = document.createElement('span');
      a.textContent = name;
      if (v.qty > 1) {
        const x = document.createElement('span');
        x.className = 'num lt-q' + (name === newName && !reduced() ? ' lt-bump' : '');
        x.textContent = '×' + v.qty;
        a.append(x);
      }
      const c = document.createElement('span');
      c.className = 'num';
      c.textContent = fmt(v.price * v.qty);
      li.append(a, c);
      linesEl.append(li);
    });
  }

  function add(btn: HTMLElement) {
    if (busy) return;
    const name = btn.dataset.name ?? '', price = toMillis(btn.dataset.price ?? '0');
    const v = cart.get(name) ?? { price, qty: 0 };
    v.qty++;
    cart.set(name, v);
    btn.classList.add('lt-flash');
    setTimeout(() => btn.classList.remove('lt-flash'), FLASH_MS);
    const old = total;
    total = 0;
    cart.forEach((x) => { total += x.price * x.qty; });
    render(name);
    tween(totalEl, old, total);
    pay.disabled = false;
    say(d.liveTotal ?? '', total);
  }

  // Centre of an element relative to the till wrapper, for the flying dot.
  const centre = (el: HTMLElement) => {
    const a = el.getBoundingClientRect(), b = root.getBoundingClientRect();
    return { x: a.left - b.left + a.width / 2, y: a.top - b.top + a.height / 2 };
  };
  function flyDot(then: () => void) {
    if (reduced()) { then(); return; }
    const s = centre(pay), e = centre(pscr);
    const dot = document.createElement('div');
    dot.className = 'lt-fdot';
    root.append(dot);
    dot.animate(
      [{ transform: `translate(${s.x}px,${s.y}px) scale(1)` }, { transform: `translate(${e.x}px,${e.y}px) scale(.6)` }],
      { duration: 600, easing: 'cubic-bezier(.5,0,.3,1)', fill: 'forwards' },
    ).onfinish = () => { dot.remove(); then(); };
  }
  function arrive(sum: number) {
    const old = dayTotal;
    dayTotal += sum;
    count++;
    tween(pTotal, old, dayTotal, 600);
    pCount.textContent = String(count);
    const last = bars.lastElementChild as HTMLElement;
    last.style.height = Math.min(BAR_MAX, (parseFloat(last.style.height) || 8) + BAR_STEP) + 'px';
    if (!reduced()) {
      pscr.classList.remove('lt-ping');
      void pscr.offsetWidth;
      pscr.classList.add('lt-ping');
      setTimeout(() => pscr.classList.remove('lt-ping'), 600);
    }
  }

  function sell() {
    if (busy || !cart.size) return;
    busy = true;
    pay.disabled = true;
    const sum = total;
    doneSum.textContent = fmt(sum);
    done.classList.add('lt-show');
    say(d.liveDone ?? '', sum);
    flyDot(() => arrive(sum));
    setTimeout(() => {
      done.classList.remove('lt-show');
      cart.clear();
      total = 0;
      render();
      totalEl.textContent = fmt(0);
      billNo.textContent = '#' + ++bill;
      busy = false;
    }, BANNER_MS);
  }

  root.querySelectorAll<HTMLElement>('[data-item]').forEach((b) => b.addEventListener('click', () => add(b)));
  pay.addEventListener('click', sell);
}
