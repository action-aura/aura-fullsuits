// "From day one" (home section 7, directions.md 8.6). The page is server-rendered in its pending state when JS runs
// (the .js class on <html>); this runs the timeline: once the section is 35% in view, every 700ms step i = 1..5 marks
// row i done, moves the copper progress to i*20%, and adds .on-i to the drawing (CSS does the draw and the fill).
// Reduced motion: all five steps are applied at once, and the CSS has no transitions.
const STEP_MS = 700;
const STEPS = 5;

const root = document.querySelector<HTMLElement>('[data-setup]');
if (root) init(root);

function init(root: HTMLElement) {
  const rows = [...root.querySelectorAll<HTMLElement>('[data-su-row]')];
  const bar = root.querySelector<HTMLElement>('[data-su-bar]')!;
  const fill = root.querySelector<HTMLElement>('[data-su-fill]')!;
  const pct = root.querySelector<HTMLElement>('[data-su-pct]')!;
  const svg = root.querySelector<SVGElement>('[data-su-svg]')!;
  const reduced = matchMedia('(prefers-reduced-motion: reduce)').matches;

  // Start from the pending state explicitly, so a script that runs after the section is already painted still begins at 0.
  bar.setAttribute('aria-valuenow', '0');

  function step(i: number) {
    rows[i - 1]?.classList.add('done');
    fill.style.setProperty('--p', String(i / STEPS));
    pct.textContent = `${i * (100 / STEPS)}%`;
    bar.setAttribute('aria-valuenow', String(i * (100 / STEPS)));
    svg.classList.add(`on-${i}`);
    root.dataset.step = String(i);
  }

  const all = () => { for (let i = 1; i <= STEPS; i++) step(i); };
  if (reduced || !('IntersectionObserver' in window)) { all(); return; }

  const io = new IntersectionObserver((entries) => {
    if (!entries.some((e) => e.isIntersecting)) return;
    io.disconnect();
    for (let i = 1; i <= STEPS; i++) setTimeout(() => step(i), i * STEP_MS);
  // 35% of the section, but never more than 80% of the screen: on a phone the section is taller than the screen.
  }, { threshold: Math.min(0.35, (innerHeight * 0.8) / Math.max(1, root.offsetHeight)) });
  io.observe(root);
}
