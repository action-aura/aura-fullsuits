const reducedQuery = window.matchMedia('(prefers-reduced-motion: reduce)');

document.querySelectorAll<HTMLElement>('.rail').forEach((rail) => {
  rail.addEventListener('keydown', (e) => {
    if (e.key !== 'ArrowRight' && e.key !== 'ArrowLeft') return;
    const card = rail.querySelector<HTMLElement>('.rail-card');
    if (!card) return;
    e.preventDefault();

    const rtl = document.documentElement.dir === 'rtl';
    const gap = parseFloat(getComputedStyle(rail).columnGap) || 0;
    const w = card.offsetWidth + gap;

    // Forward = ArrowRight in LTR, ArrowLeft in RTL. Chromium RTL scrollLeft
    // runs negative, so forward there is a negative delta.
    const forward = rtl ? e.key === 'ArrowLeft' : e.key === 'ArrowRight';
    const sign = (forward ? 1 : -1) * (rtl ? -1 : 1);

    rail.scrollBy({ left: sign * w, behavior: reducedQuery.matches ? 'auto' : 'smooth' });
  });
});
