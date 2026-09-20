// Value -> colour for the coverage grid and scatter.
//
// Two jobs, per the dataviz method:
//  - DIVERGING (blue <-> red around a neutral gray) only for metrics with a meaningful centre:
//    Sharpe/CAGR/P&L around 0, profit factor/omega around 1. Colouring max_drawdown that way would
//    paint every cell red, because it is never positive -- so it is sequential.
//  - SEQUENTIAL (one blue hue, light -> dark) for everything else, with the anchor flipped in
//    dark mode so large values are the bright end.
//
// Both modes are computed and emitted as CSS light-dark(), so the page follows the theme with no
// JS theme state. Stops come from palette.generated.ts (see scripts/gen-palette.mjs).
import { PALETTE } from "./palette.generated";

type Lab = [number, number, number];
const toLin = (c: number) => (c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4);
const toSrgb = (c: number) => (c <= 0.0031308 ? 12.92 * c : 1.055 * c ** (1 / 2.4) - 0.055);

function hexToLab(h: string): Lab {
  const [r, g, b] = [1, 3, 5].map((i) => toLin(parseInt(h.slice(i, i + 2), 16) / 255));
  const l = Math.cbrt(0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b);
  const m = Math.cbrt(0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b);
  const s = Math.cbrt(0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b);
  return [
    0.2104542553 * l + 0.793617785 * m - 0.0040720468 * s,
    1.9779984951 * l - 2.428592205 * m + 0.4505937099 * s,
    0.0259040371 * l + 0.7827717662 * m - 0.808675766 * s,
  ];
}
function labToHex([L, a, b]: Lab): string {
  const l = (L + 0.3963377774 * a + 0.2158037573 * b) ** 3;
  const m = (L - 0.1055613458 * a - 0.0638541728 * b) ** 3;
  const s = (L - 0.0894841775 * a - 1.291485548 * b) ** 3;
  const rgb = [
    4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s,
    -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
    -0.0041960863 * l - 0.7034186147 * m + 1.707614701 * s,
  ].map(toSrgb);
  return "#" + rgb.map((v) => Math.round(Math.min(1, Math.max(0, v)) * 255).toString(16).padStart(2, "0")).join("");
}

/** Piecewise-linear interpolation in OKLab across an ordered list of stops, t in [0,1]. */
function ramp(stops: readonly string[], t: number): string {
  const n = stops.length - 1;
  const x = Math.min(1, Math.max(0, t)) * n;
  const i = Math.min(n - 1, Math.floor(x));
  const a = hexToLab(stops[i]);
  const b = hexToLab(stops[i + 1]);
  const f = x - i;
  return labToHex([a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f, a[2] + (b[2] - a[2]) * f]);
}

const relLum = (h: string) => {
  const [r, g, b] = [1, 3, 5].map((i) => toLin(parseInt(h.slice(i, i + 2), 16) / 255));
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
};
const contrast = (a: string, b: string) => {
  const [hi, lo] = [relLum(a), relLum(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
};
/** Text set inside a coloured fill: white or ink, whichever clears more contrast. */
const textOn = (bg: string) => (contrast(bg, "#ffffff") >= contrast(bg, "#0b0b0b") ? "#ffffff" : "#0b0b0b");

const DIVERGE_AT: Record<string, number> = {
  sharpe_ratio: 0, sortino_ratio: 0, calmar_ratio: 0, cagr: 0, total_return: 0, total_pnl: 0,
  avg_pnl: 0, premium_capture: 0, avg_return_on_margin: 0, ann_return_on_margin: 0,
  pnl_per_day_in_trade: 0, trade_pnl_tstat: 0, profit_factor: 1, omega_ratio: 1,
};

const cssPair = (light: string, dark: string) => `light-dark(${light}, ${dark})`;

function divergingStops(mode: "light" | "dark"): string[] {
  const d = PALETTE.diverging[mode];
  return [...[...d.neg].reverse(), d.mid, ...d.pos];
}

export interface Scale {
  kind: "diverging" | "sequential";
  center: number | null;
  domain: [number, number];
  /** CSS colour + text colour for a value (light-dark() aware); null -> the neutral "no value". */
  at: (v: number | null) => { bg: string; fg: string };
  /** CSS gradient for the legend bar. */
  gradient: string;
}

export function makeScale(metric: string, values: (number | null)[]): Scale {
  const vs = values.filter((v): v is number => v != null && Number.isFinite(v));
  const center = metric in DIVERGE_AT ? DIVERGE_AT[metric] : null;
  const min = vs.length ? Math.min(...vs) : 0;
  const max = vs.length ? Math.max(...vs) : 1;

  let toT: (v: number) => number;
  let domain: [number, number];
  let stops: Record<"light" | "dark", string[]>;
  if (center != null) {
    // symmetric around the centre so equal distance = equal colour on both arms
    const span = Math.max(Math.abs(max - center), Math.abs(min - center), 1e-9);
    domain = [center - span, center + span];
    toT = (v) => 0.5 + (0.5 * (v - center)) / span;
    stops = { light: divergingStops("light"), dark: divergingStops("dark") };
  } else {
    domain = [min, max];
    toT = (v) => (max === min ? 0.5 : (v - min) / (max - min));
    stops = { light: [...PALETTE.sequential.light], dark: [...PALETTE.sequential.dark] };
  }
  const neutral = { light: PALETTE.neutral.light, dark: PALETTE.neutral.dark };

  return {
    kind: center != null ? "diverging" : "sequential",
    center,
    domain,
    at: (v) => {
      if (v == null || !Number.isFinite(v)) {
        return { bg: cssPair(neutral.light, neutral.dark), fg: "var(--ink-2)" };
      }
      const t = toT(v);
      const l = ramp(stops.light, t);
      const d = ramp(stops.dark, t);
      return { bg: cssPair(l, d), fg: cssPair(textOn(l), textOn(d)) };
    },
    gradient: `linear-gradient(90deg, ${stops.light
      .map((l, i) => cssPair(l, stops.dark[i]))
      .join(", ")})`,
  };
}
