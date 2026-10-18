import { worldMix, dominant, WORLDS, type World } from './mix';

// Each world layer takes ITS OWN share as opacity. (The prototype's first
// version applied a stacked-crossfade formula to semi-transparent scrims,
// which left the previous world's vignette at full strength: the grey band
// over Clinic. Never stack-compose these layers.)
export function startWorlds(root: HTMLElement): () => void {
  const reduced = matchMedia('(prefers-reduced-motion: reduce)').matches;
  // getBoundingClientRect + scrollY, not offsetTop: offsetTop is relative to the
  // nearest positioned ancestor and drifts from document position.
  const sections = () => Array.from(document.querySelectorAll<HTMLElement>('[data-w]')).map((el) => ({
    top: el.getBoundingClientRect().top + window.scrollY, height: el.offsetHeight, world: (el.dataset.w as World) ?? 'master',
  }));
  const layers = Object.fromEntries(WORLDS.map((w) => [w, Array.from(document.querySelectorAll<HTMLElement>(`.bg-${w}, .sc-${w}`))])) as Record<World, HTMLElement[]>;
  let cache = sections();
  let frame = 0;
  const update = () => {
    frame = 0;
    let mix = worldMix(cache, window.scrollY, window.innerHeight);
    const dom = dominant(mix);
    if (reduced) {
      // Reduced motion: step to the dominant world, no blending.
      mix = Object.fromEntries(WORLDS.map((w) => [w, w === dom ? 1 : 0])) as typeof mix;
    }
    for (const w of WORLDS) root.style.setProperty(`--w-${w}`, mix[w].toFixed(3));
    // A world at share 0 is hidden, not just transparent: axe read the hidden master field behind Clinic copy
    // (dark-on-dark, while the pixels measure 14:1), and phones painted four invisible full-screen layers.
    for (const w of WORLDS) {
      const off = mix[w] < 0.0005;
      for (const el of layers[w]) el.toggleAttribute('data-off', off);
    }
    root.dataset.world = dom;
    // Latest mix kept on the root so a late-mounting jewel can read it (deep-load colour).
    (root as any).__auraMix = mix;
    root.dispatchEvent(new CustomEvent('worldmix', { detail: mix }));
  };
  const onScroll = () => { if (!frame) frame = requestAnimationFrame(update); };
  const remeasure = () => { cache = sections(); onScroll(); };
  addEventListener('scroll', onScroll, { passive: true });
  addEventListener('resize', remeasure);
  addEventListener('load', remeasure);
  document.fonts?.ready.then(remeasure);
  const main = document.querySelector('main');
  const ro = main && typeof ResizeObserver !== 'undefined' ? new ResizeObserver(remeasure) : null;
  if (main) ro?.observe(main);
  update();
  return () => {
    removeEventListener('scroll', onScroll); removeEventListener('resize', remeasure);
    removeEventListener('load', remeasure); ro?.disconnect(); cancelAnimationFrame(frame);
  };
}
