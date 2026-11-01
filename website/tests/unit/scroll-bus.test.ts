import { describe, it, expect } from 'vitest';
import { createScrollBus } from '../../src/scripts/scroll-bus';

// A fake frame clock: callbacks queue until flush(), like requestAnimationFrame.
function rig() {
  const queue: Array<() => void> = [];
  let y = 0;
  const reads: string[] = [];
  const bus = createScrollBus({
    requestFrame: (cb) => { queue.push(cb); },
    frame: () => { reads.push('read'); return { y, h: 800 }; },
  });
  const flush = () => { const q = queue.splice(0); q.forEach((cb) => cb()); };
  return { bus, flush, setY: (v: number) => { y = v; }, reads, queued: () => queue.length };
}

describe('scroll bus', () => {
  it('runs each subscriber exactly once per frame, however many scroll events came first', () => {
    const { bus, flush } = rig();
    let a = 0, b = 0;
    bus.onScroll(() => { a++; });
    bus.onScroll(() => { b++; });
    for (let i = 0; i < 7; i++) bus.request();
    flush();
    expect([a, b]).toEqual([1, 1]);
  });

  it('schedules one frame for many requests and reads scrollY/innerHeight once', () => {
    const { bus, flush, queued, reads } = rig();
    bus.onScroll(() => {});
    bus.request(); bus.request(); bus.request();
    expect(queued()).toBe(1);
    flush();
    expect(reads.length).toBe(1);
  });

  it('hands every subscriber the same frame values', () => {
    const { bus, flush, setY } = rig();
    const seen: number[] = [];
    bus.onScroll((f) => seen.push(f.y));
    bus.onScroll((f) => seen.push(f.y));
    setY(321);
    bus.request(); flush();
    expect(seen).toEqual([321, 321]);
  });

  it('measures only after remeasure(), and before the subscribers of that frame', () => {
    const { bus, flush } = rig();
    const order: string[] = [];
    bus.onMeasure(() => order.push('measure'));
    bus.onScroll(() => order.push('frame'));
    bus.request(); flush();
    expect(order).toEqual(['frame']);
    bus.remeasure(); flush();
    expect(order).toEqual(['frame', 'measure', 'frame']);
    bus.request(); flush();
    expect(order).toEqual(['frame', 'measure', 'frame', 'frame']);
  });

  it('a request made while a frame is running makes one more frame, not a loop', () => {
    const { bus, flush, queued } = rig();
    let n = 0;
    bus.onScroll(() => { n++; if (n === 1) bus.request(); });
    bus.request(); flush();
    expect(n).toBe(1);
    expect(queued()).toBe(1);
    flush();
    expect(n).toBe(2);
    expect(queued()).toBe(0);
  });
});
