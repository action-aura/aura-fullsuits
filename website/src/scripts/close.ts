// Fits the big phone number to the full width of its box (directions.md Direction 2, 8.4: measure at a known size, then
// scale). The CSS already sets a vw-based size that is close, so without JS (or before fonts load) it is merely a bit
// off, never broken. Re-fits after the fonts load and on resize.
const FIT_AT = 100;
const el = document.querySelector<HTMLElement>('[data-cl-phone]');
const box = document.querySelector<HTMLElement>('[data-cl-box]');

function fit() {
  if (!el || !box) return;
  el.style.fontSize = `${FIT_AT}px`;
  const w = el.scrollWidth;
  if (w > 0) el.style.fontSize = `${(FIT_AT * box.clientWidth) / w}px`;
}

if (el && box) {
  fit();
  document.fonts?.ready.then(fit);
  let t = 0;
  addEventListener('resize', () => { clearTimeout(t); t = window.setTimeout(fit, 150); });
}
