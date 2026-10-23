// The clinic booking preview (E3). Server-rendered with three booked slots; this makes the free ones work. Words come
// from data-* attributes and the hidden patients list in the markup, so the script holds no language of its own. Numbers
// are plain digits (String(n)), never toLocaleString, which would turn them Arabic-Indic on /ar.
const root = document.querySelector<HTMLElement>('[data-book]');
if (root) init(root);

function init(root: HTMLElement) {
  const q = <T extends HTMLElement>(s: string) => root.querySelector<T>(`[data-bk-${s}]`)!;
  const d = root.dataset;
  const countEl = q('count'), dayEl = q('day'), live = q('live');
  const fileName = q('name'), fileLast = q('last'), filePlan = q('plan'), fileTx = q('tx'), fileAv = q('av');
  const file = root.querySelector<HTMLElement>('.bk-file')!;
  const patients = Array.from(q('patients').children).map((li) => ({
    name: (li as HTMLElement).dataset.n!, tx: (li as HTMLElement).dataset.t!, last: (li as HTMLElement).dataset.l!, plan: (li as HTMLElement).dataset.p!,
  }));
  const reduced = () => matchMedia('(prefers-reduced-motion: reduce)').matches;

  // "Today" with today's weekday in the page language. Western digits are not involved (a weekday name has none), but
  // the numbering system is pinned anyway so a browser locale can never switch it.
  try {
    const loc = d.lang === 'ar' ? 'ar-u-nu-latn' : 'en';
    const wd = new Date().toLocaleDateString(loc, { weekday: 'long' });
    dayEl.textContent = `${dayEl.textContent} · ${wd}`;
  } catch { /* the plain "Today" stays */ }

  let next = root.querySelectorAll('.bk-slot.is-booked').length; // patients 0..2 are the booked ones
  let count = next;

  function showFile(i: number) {
    const p = patients[i % patients.length];
    fileName.textContent = p.name;
    fileLast.textContent = p.last;
    filePlan.textContent = p.plan;
    fileTx.textContent = p.tx;
    fileAv.textContent = p.name.slice(0, 1);
    if (!reduced()) {
      file.classList.remove('bk-swap');
      void file.offsetWidth;
      file.classList.add('bk-swap');
    }
  }

  function book(btn: HTMLElement) {
    const li = btn.closest<HTMLElement>('.bk-slot')!;
    const time = li.dataset.slot!;
    const i = next++;
    const p = patients[i % patients.length];
    const fill = document.createElement('span');
    fill.className = 'bk-fill' + (reduced() ? '' : ' bk-in');
    fill.tabIndex = -1;
    const b = document.createElement('b');
    b.textContent = p.name;
    const hid = document.createElement('span');
    hid.className = 'visually-hidden';
    hid.textContent = ` (${d.bookedWord})`;
    fill.append(b, ` · ${p.tx}`, hid);
    btn.replaceWith(fill);
    li.classList.add('is-booked');
    count++;
    countEl.textContent = String(count);
    if (!reduced()) {
      countEl.classList.remove('dc-bump');
      void countEl.offsetWidth;
      countEl.classList.add('dc-bump');
    }
    showFile(i);
    live.textContent = d.announce!.replace('{t}', time).replace('{name}', p.name).replace('{tx}', p.tx).replace('{n}', String(count));
    // The pressed button is gone; keep the keyboard where it was.
    fill.focus({ preventScroll: true });
  }

  root.querySelectorAll<HTMLElement>('[data-bk-free]').forEach((b) => b.addEventListener('click', () => book(b)));
}
