import { worldMix, dominant, WORLDS, type World } from './mix';

// Each world layer takes ITS OWN share as opacity. (The prototype's first
// version applied a stacked-crossfade formula to semi-transparent scrims,
// which left the previous world's vignette at full strength: the grey band
// over Clinic. Never stack-compose these layers.)
export function startWorlds(root: HTMLElement): () => void {
  const sections = () => Array.from(document.querySelectorAll<HTMLElement>('[data-w]')).map((el) => ({
    top: el.offsetTop, height: el.offsetHeight, world: (el.dataset.w as World) ?? 'master',
  }));
  let cache = sections();
  let frame = 0;
  const update = () => {
    frame = 0;
    const mix = worldMix(cache, window.scrollY, window.innerHeight);
    for (const w of WORLDS) root.style.setProperty(`--w-${w}`, mix[w].toFixed(3));
    root.dataset.world = dominant(mix);
    root.dispatchEvent(new CustomEvent('worldmix', { detail: mix }));
  };
  const onScroll = () => { if (!frame) frame = requestAnimationFrame(update); };
  const onResize = () => { cache = sections(); onScroll(); };
  addEventListener('scroll', onScroll, { passive: true });
  addEventListener('resize', onResize);
  update();
  return () => { removeEventListener('scroll', onScroll); removeEventListener('resize', onResize); cancelAnimationFrame(frame); };
}
