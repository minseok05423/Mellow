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
