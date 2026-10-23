// "What it does" (home section 6). Desktop: the section is a tall track with a sticky stage; scroll progress through the
// track picks the active item, and a click scrolls the page to that item's slice so the two stay one source of truth.
// Phones: no pinning, so scroll is ignored; click and arrow keys still work. Active state is data-active on the panel and
// aria-selected on the tab; the cross-fade itself is pure CSS (opacity), so reduced motion only has to turn off a transition.
const root = document.querySelector<HTMLElement>('[data-wd]');
if (root) init(root);

function init(root: HTMLElement) {
  const track = root.querySelector<HTMLElement>('[data-wd-track]')!;
  const tabs = [...root.querySelectorAll<HTMLButtonElement>('[data-wd-tab]')];
  const panels = [...root.querySelectorAll<HTMLElement>('[data-wd-panel]')];
  const N = tabs.length;
  const wide = matchMedia('(min-width: 900px)');
  let current = 0;

  function show(i: number) {
    current = i;
    tabs.forEach((t, k) => {
      const on = k === i;
      t.setAttribute('aria-selected', on ? 'true' : 'false');
      t.tabIndex = on ? 0 : -1;
    });
    panels.forEach((p, k) => p.setAttribute('data-active', k === i ? 'true' : 'false'));
  }

  // Scroll distance the sticky stage is pinned for, and where each item's slice starts (page coordinates).
  const range = () => Math.max(1, track.offsetHeight - (track.firstElementChild as HTMLElement).offsetHeight);
  const trackTop = () => track.getBoundingClientRect().top + scrollY;
  const indexFromScroll = () => {
    const p = Math.min(1, Math.max(0, (scrollY - trackTop()) / range()));
    return Math.min(N - 1, Math.floor(p * N));
  };
  const syncFromScroll = () => { if (wide.matches) { const i = indexFromScroll(); if (i !== current) show(i); } };

  let raf = 0;
  addEventListener('scroll', () => { if (!raf) raf = requestAnimationFrame(() => { raf = 0; syncFromScroll(); }); }, { passive: true });
  wide.addEventListener('change', () => { if (wide.matches) syncFromScroll(); });
  addEventListener('resize', syncFromScroll);

  function choose(i: number, focus = false) {
    show(i);
    if (focus) tabs[i].focus();
    if (wide.matches) scrollTo({ top: trackTop() + ((i + 0.5) / N) * range(), behavior: 'instant' as ScrollBehavior });
  }

  tabs.forEach((t, i) => t.addEventListener('click', () => choose(i)));

  root.querySelector('[data-wd-list]')!.addEventListener('keydown', (e) => {
    const k = (e as KeyboardEvent).key;
    // In a right-to-left page the first tab sits on the right, so ArrowLeft is "next" there and ArrowRight is "previous".
    const rtl = getComputedStyle(root).direction === 'rtl';
    const next = k === 'ArrowDown' || k === (rtl ? 'ArrowLeft' : 'ArrowRight');
    const prev = k === 'ArrowUp' || k === (rtl ? 'ArrowRight' : 'ArrowLeft');
    let i = -1;
    if (next) i = (current + 1) % N;
    else if (prev) i = (current - 1 + N) % N;
    else if (k === 'Home') i = 0;
    else if (k === 'End') i = N - 1;
    if (i < 0) return;
    e.preventDefault();
    choose(i, true);
  });

  syncFromScroll();
}
