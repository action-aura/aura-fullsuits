// One scroll scheduler for the whole page (v3 jank fix J3).
//
// Before, four independent listeners (the world blend, the jewel, the "what it does" stage, the nav) each ran on their
// own, some straight in the scroll event and several times a frame, and some read layout (getBoundingClientRect) between
// another listener's style writes. Reading layout after a style write forces a synchronous layout, so a glide became a
// chain of forced layouts. Now:
//   - the browser's scroll events only mark the bus dirty; ONE requestAnimationFrame per frame runs everything;
//   - scrollY and innerHeight are read once, first, and handed to every subscriber (a subscriber must not read layout);
//   - geometry (section tops, hero bottom, footer top, track range) is measured by `onMeasure` callbacks, only when the
//     layout can have changed (load, resize, fonts ready, main resized), stored in DOCUMENT coordinates, and used from
//     then on as plain numbers.
export interface Frame { y: number; h: number }

export interface BusEnv {
  /** Schedule `cb` for the next frame (requestAnimationFrame in the browser). */
  requestFrame(cb: () => void): void;
  /** The only layout-adjacent read, done once per frame. */
  frame(): Frame;
}

export interface ScrollBus {
  /** Called once per frame in which the page scrolled, resized or was remeasured. Must not read layout. */
  onScroll(fn: (f: Frame) => void): void;
  /** Called before the next frame's subscribers, only after `remeasure()`. This is the place for getBoundingClientRect. */
  onMeasure(fn: () => void): void;
  /** Layout may have changed: re-measure, then run a frame. */
  remeasure(): void;
  /** Ask for a frame (scroll happened). Coalesced: any number of calls before the frame make one run. */
  request(): void;
}

export function createScrollBus(env: BusEnv): ScrollBus {
  const subs: Array<(f: Frame) => void> = [];
  const measures: Array<() => void> = [];
  let queued = false;
  let dirty = false;
  const run = () => {
    queued = false;
    if (dirty) { dirty = false; for (const m of measures) m(); }
    const f = env.frame();
    for (const s of subs) s(f);
  };
  const request = () => { if (!queued) { queued = true; env.requestFrame(run); } };
  return {
    onScroll: (fn) => { subs.push(fn); },
    onMeasure: (fn) => { measures.push(fn); },
    remeasure: () => { dirty = true; request(); },
    request,
  };
}

let shared: ScrollBus | null = null;

/** The page's bus, wired to the real window on first use. Every script imports this one instance (the bundler dedupes). */
export function bus(): ScrollBus {
  if (shared) return shared;
  const b = createScrollBus({ requestFrame: (cb) => { requestAnimationFrame(cb); }, frame: () => ({ y: scrollY, h: innerHeight }) });
  addEventListener('scroll', b.request, { passive: true });
  addEventListener('resize', b.remeasure);
  addEventListener('load', b.remeasure);
  document.fonts?.ready.then(b.remeasure);
  const main = document.querySelector('main');
  if (main && typeof ResizeObserver !== 'undefined') new ResizeObserver(b.remeasure).observe(main);
  shared = b;
  return b;
}
