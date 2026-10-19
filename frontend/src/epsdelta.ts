// The two ε–δ demo functions and the math behind them. No drawing here.

import type { Bounds } from './plot';

export interface Demo {
  id: 'square' | 'step';
  tab: string;          // button label (LaTeX)
  fLatex: string;       // definition of f (LaTeX)
  a: number;
  fa: number;
  f: (x: number) => number;
  jumps: number[];      // x positions where the graph is broken
  openDots: { x: number; y: number }[];  // limits the function doesn't attain
  bounds: Bounds;       // default camera
  /** Largest δ that works for this ε. Infinity = every δ works; null = no δ works. */
  delta: (eps: number) => number | null;
}

export const EPS_MIN = 0.01;
export const EPS_MAX = 1;

export const square: Demo = {
  id: 'square',
  tab: 'f(x)=x^2',
  fLatex: 'f(x) = x^2,\\quad a = 1',
  a: 1,
  fa: 1,
  f: (x) => x * x,
  jumps: [],
  openDots: [],
  bounds: { xmin: -1.1, xmax: 2.6, ymin: -0.6, ymax: 2.9 },
  // |x² − 1| < ε  ⇔  √(1−ε) < x < √(1+ε) near x = 1. The right gap √(1+ε)−1 is the
  // smaller one (x² is convex), so it is the largest δ that works.
  delta: (eps) => Math.sqrt(1 + eps) - 1,
};

export const step: Demo = {
  id: 'step',
  tab: '\\text{step}',
  fLatex: 'f(x) = \\begin{cases} 0 & x<0 \\\\ \\tfrac12 & x=0 \\\\ 1 & x>0 \\end{cases},\\quad a = 0',
  a: 0,
  fa: 0.5,
  f: (x) => (x < 0 ? 0 : x > 0 ? 1 : 0.5),
  jumps: [0],
  openDots: [{ x: 0, y: 0 }, { x: 0, y: 1 }],
  bounds: { xmin: -1.6, xmax: 1.6, ymin: -0.7, ymax: 1.7 },
  // Near 0, f takes the values 0 and 1, both exactly ½ from f(0) = ½.
  // They're inside the ε-band iff ½ < ε (strict inequality), regardless of δ.
  delta: (eps) => (eps > 0.5 ? Infinity : null),
};

export const DEMOS = { square, step };
