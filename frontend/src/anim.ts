// Minimal Manim-style animation primitives. The M2 scene renderer builds on these.

export type Easing = (t: number) => number;

const sigmoid = (x: number) => 1 / (1 + Math.exp(-x));

// Manim's default rate function `smooth`: an S-curve with zero slope at both ends.
export const smooth: Easing = (t) => {
  const inflection = 10;
  const err = sigmoid(-inflection / 2);
  return Math.min(1, Math.max(0, (sigmoid(inflection * (t - 0.5)) - err) / (1 - 2 * err)));
};

export const lerp = (a: number, b: number, t: number) => a + (b - a) * t;

/** A number that eases from its current value to a target over time. Read it every frame. */
export class Tween {
  private from: number;
  private to: number;
  private start = 0;
  private duration = 0;

  constructor(value: number, private ease: Easing = smooth) {
    this.from = this.to = value;
  }

  set(target: number, durationMs: number, now = performance.now()) {
    this.from = this.get(now);
    this.to = target;
    this.start = now;
    this.duration = durationMs;
  }

  get(now = performance.now()): number {
    if (this.duration <= 0) return this.to;
    const t = Math.min(1, (now - this.start) / this.duration);
    return lerp(this.from, this.to, this.ease(t));
  }
}

const reducedMotion = () => typeof matchMedia === 'function' && matchMedia('(prefers-reduced-motion: reduce)').matches;

/** Call `frame(t)` every animation frame for `ms`, with t eased from 0 to 1; resolves when done.
 * With reduced motion turned on in the OS, it jumps straight to the end. */
export function play(ms: number, frame: (t: number) => void, ease: Easing = smooth): Promise<void> {
  if (reducedMotion() || ms <= 0) {
    frame(1);
    return Promise.resolve();
  }
  return new Promise((done) => {
    const start = performance.now();
    const tick = (now: number) => {
      const t = Math.min(1, (now - start) / ms);
      frame(ease(t));
      if (t < 1) requestAnimationFrame(tick);
      else done();
    };
    requestAnimationFrame(tick);
  });
}
