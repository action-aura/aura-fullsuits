import { worldMix, dominant, WORLDS, type World } from './mix';
import { bus, type Frame } from '../scroll-bus';

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
  const update = (f: Frame) => {
    let mix = worldMix(cache, f.y, f.h);
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
  // One shared scheduler (scroll-bus.ts): this runs once per frame with scrollY read for it, and the section geometry
  // is re-measured only when the layout can have changed.
  const b = bus();
  b.onMeasure(() => { cache = sections(); });
  b.onScroll(update);
  update({ y: window.scrollY, h: window.innerHeight });
  return () => {};
}
