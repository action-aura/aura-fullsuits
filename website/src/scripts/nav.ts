const root = document.documentElement;

// Nav bar: --nav-top keeps the fixed nav under the phone strip (38px) at the top of the page and lets it settle at 0 as the
// strip scrolls away; data-scrolled switches on the soft field bar behind the nav (components.css .nav::before).
const nav = document.querySelector<HTMLElement>('.nav');
const stripH = parseFloat(getComputedStyle(root).getPropertyValue('--strip-h')) || 0;
const placeNav = () => {
  root.style.setProperty('--nav-top', Math.max(0, stripH - scrollY) + 'px');
  nav?.toggleAttribute('data-scrolled', scrollY > 8);
};
addEventListener('scroll', placeNav, { passive: true });
addEventListener('resize', placeNav);
placeNav();

const sections = document.querySelectorAll<HTMLElement>('[data-w]');
if (sections.length && 'IntersectionObserver' in window) {
  const io = new IntersectionObserver(
    (entries) => {
      for (const e of entries) {
        if (e.isIntersecting && e.target.id) root.dataset.chapter = e.target.id;
      }
    },
    { rootMargin: '-45% 0px -45% 0px' },
  );
  sections.forEach((s) => io.observe(s));
}

document.addEventListener('click', (e) => {
  const a = (e.target as Element | null)?.closest?.('a.nav-lang, a.footer-lang');
  const lang = a?.getAttribute('hreflang');
  if (!lang) return;
  try {
    localStorage.setItem('aa-lang', lang);
  } catch {
    /* storage unavailable */
  }
});

const btn = document.querySelector<HTMLButtonElement>('.nav-toggle');
const sheet = document.getElementById('nav-sheet');
if (btn && sheet) {
  const label = btn.querySelector<HTMLElement>('.nav-toggle-label');
  const setOpen = (open: boolean) => {
    const wasOpen = !sheet.hidden;
    sheet.hidden = !open;
    btn.setAttribute('aria-expanded', String(open));
    if (label) label.textContent = (open ? btn.dataset.close : btn.dataset.menu) ?? '';
    document.querySelectorAll<HTMLElement>('main, footer').forEach((el) => {
      if (open) el.setAttribute('inert', '');
      else el.removeAttribute('inert');
    });
    if (wasOpen && !open) btn.focus();
  };
  btn.addEventListener('click', () => setOpen(sheet.hidden));
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && !sheet.hidden) setOpen(false);
  });
  sheet.addEventListener('click', (e) => {
    if ((e.target as HTMLElement).closest('a')) setOpen(false);
  });
}

// Marks this file as a module: without an import/export TypeScript treats every script as one shared global scope and
// reports the top-level `root`/`init` of each as a redeclaration of the others. The bundler already treated it as a module.
export {};
