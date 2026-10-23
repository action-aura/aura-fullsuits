// Tap motion, shared by every pressable. On pointerdown the element gets `.is-pressed` (CSS scales it to .97 in 120ms;
// :active alone is unreliable on touch browsers) and a soft disc in the current world's --accent grows from the touch
// point (.tap-glow, appended to <body>, transform and opacity only). Released, cancelled or dragged off, the press ends.
// Reduced motion: no glow, no class; components.css keeps only an instant colour state through :active.
const PRESSABLE = [
  '.wa-pill', '.demo-link', '.nav-demo', '.nav-toggle', '.chapter-link', '.nav-sheet-link', '.btn-send', '.btn-send-wa',
  // E2: the playable retail demos
  '.lt-item', '.lt-pay', '.of-sw', '.of-new', '.wd-tab',
  // E3: clinic booking slots and gym member cards
  '.bk-free', '.ck-card',
].join(',');

const reduced = matchMedia('(prefers-reduced-motion: reduce)');

addEventListener('pointerdown', (e) => {
  if (e.button > 0) return;
  const el = (e.target as Element | null)?.closest<HTMLElement>(PRESSABLE);
  if (!el || (el as HTMLButtonElement).disabled) return;
  if (reduced.matches) return;
  el.classList.add('is-pressed');
  const end = () => {
    el.classList.remove('is-pressed');
    removeEventListener('pointerup', end, true);
    removeEventListener('pointercancel', end, true);
  };
  addEventListener('pointerup', end, true);
  addEventListener('pointercancel', end, true);

  const glow = document.createElement('span');
  glow.className = 'tap-glow';
  glow.setAttribute('aria-hidden', 'true');
  glow.style.left = e.clientX + 'px';
  glow.style.top = e.clientY + 'px';
  glow.addEventListener('animationend', () => glow.remove(), { once: true });
  document.body.appendChild(glow);
}, { passive: true });
