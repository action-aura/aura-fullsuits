const root = document.documentElement;

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

const btn = document.querySelector<HTMLButtonElement>('.nav-toggle');
const sheet = document.getElementById('nav-sheet');
if (btn && sheet) {
  const label = btn.querySelector<HTMLElement>('.nav-toggle-label');
  const setOpen = (open: boolean) => {
    sheet.hidden = !open;
    btn.setAttribute('aria-expanded', String(open));
    if (label) label.textContent = (open ? btn.dataset.close : btn.dataset.menu) ?? '';
  };
  btn.addEventListener('click', () => setOpen(sheet.hidden));
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && !sheet.hidden) setOpen(false);
  });
  sheet.addEventListener('click', (e) => {
    if ((e.target as HTMLElement).closest('a')) setOpen(false);
  });
}
