// Generates lib/palette.generated.ts -- the colour stops for the coverage grid / scatter.
//
// Source of truth: the dataviz skill's reference palette (palette.md). Everything is taken
// from it EXCEPT one thing it does not document: a red ramp. The diverging pair is
// blue <-> red with a gray midpoint, but only the blue steps are given as a ramp. The red arm
// is therefore DERIVED here: for each blue step, red at the same OKLCH lightness, hue taken
// from the documented categorical red (#e34948 light / #e66767 dark), chroma scaled the way
// blue's is. Equal lightness per step is what makes "2 steps blue" and "2 steps red" carry the
// same visual weight. This script checks that instead of assuming it, and exits 1 if not.
//
//   npm run gen:palette
import { writeFileSync } from "node:fs";

// ---- OKLab / OKLCH ---------------------------------------------------------------------
const toLin = (c) => (c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4);
const toSrgb = (c) => (c <= 0.0031308 ? 12.92 * c : 1.055 * c ** (1 / 2.4) - 0.055);
const hex2rgb = (h) => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16) / 255);
const rgb2hex = (r) => "#" + r.map((v) => Math.round(Math.min(1, Math.max(0, v)) * 255).toString(16).padStart(2, "0")).join("");

function rgb2oklab([r, g, b]) {
  const [R, G, B] = [r, g, b].map(toLin);
  const l = Math.cbrt(0.4122214708 * R + 0.5363325363 * G + 0.0514459929 * B);
  const m = Math.cbrt(0.2119034982 * R + 0.6806995451 * G + 0.1073969566 * B);
  const s = Math.cbrt(0.0883024619 * R + 0.2817188376 * G + 0.6299787005 * B);
  return [0.2104542553 * l + 0.793617785 * m - 0.0040720468 * s,
          1.9779984951 * l - 2.428592205 * m + 0.4505937099 * s,
          0.0259040371 * l + 0.7827717662 * m - 0.808675766 * s];
}
function oklab2rgb([L, a, b]) {
  const l = (L + 0.3963377774 * a + 0.2158037573 * b) ** 3;
  const m = (L - 0.1055613458 * a - 0.0638541728 * b) ** 3;
  const s = (L - 0.0894841775 * a - 1.291485548 * b) ** 3;
  return [4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s,
          -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
          -0.0041960863 * l - 0.7034186147 * m + 1.707614701 * s].map(toSrgb);
}
const lch = (hex) => { const [L, a, b] = rgb2oklab(hex2rgb(hex)); return [L, Math.hypot(a, b), Math.atan2(b, a)]; };
const inGamut = (rgb) => rgb.every((v) => v >= -1e-4 && v <= 1 + 1e-4);
function fromLch(L, C, h) {           // gamut-clamp by shrinking chroma, never lightness
  for (let c = C; c >= 0; c -= 0.002) {
    const rgb = oklab2rgb([L, c * Math.cos(h), c * Math.sin(h)]);
    if (inGamut(rgb)) return rgb2hex(rgb);
  }
  return rgb2hex(oklab2rgb([L, 0, 0]));
}
const L_of = (hex) => lch(hex)[0];

// ---- documented inputs (palette.md) ----------------------------------------------------
const BLUE = { 100: "#cde2fb", 150: "#b7d3f6", 200: "#9ec5f4", 250: "#86b6ef", 300: "#6da7ec", 350: "#5598e7",
               400: "#3987e5", 450: "#2a78d6", 500: "#256abf", 550: "#1c5cab", 600: "#184f95", 650: "#104281", 700: "#0d366b" };
const NEUTRAL = { light: "#f0efec", dark: "#383835" };
const RED_ANCHOR = { light: "#e34948", dark: "#e66767" };
const SURFACE = { light: "#fcfcfb", dark: "#1a1a19" };

// arms: 4 steps each, magnitude increasing away from the neutral midpoint
const BLUE_ARM = { light: [150, 300, 450, 600], dark: [600, 500, 400, 300] };
// sequential: one hue; light = light->dark. dark flips the anchor so big values are bright.
const SEQ = { light: [100, 200, 300, 400, 500, 600, 700], dark: [700, 600, 500, 400, 300, 250] };

const out = { surface: SURFACE, neutral: NEUTRAL, diverging: {}, sequential: {} };
const problems = [];

for (const mode of ["light", "dark"]) {
  const blue = BLUE_ARM[mode].map((k) => BLUE[k]);
  const [, cBlueRef] = lch(BLUE[450]);
  const [, cRedRef, hRed] = lch(RED_ANCHOR[mode]);
  const red = blue.map((b) => { const [L, C] = lch(b); return fromLch(L, C * (cRedRef / cBlueRef), hRed); });
  out.diverging[mode] = { neg: red, mid: NEUTRAL[mode], pos: blue };

  // checks -----------------------------------------------------------------------------
  const Ls = { pos: blue.map(L_of), neg: red.map(L_of) };
  const dir = mode === "light" ? -1 : 1;                 // light: gets darker outward; dark: brighter
  for (const arm of ["pos", "neg"]) {
    const seq = [L_of(NEUTRAL[mode]), ...Ls[arm]];
    for (let i = 1; i < seq.length; i++)
      if ((seq[i] - seq[i - 1]) * dir <= 0) problems.push(`${mode} ${arm} arm not monotone at step ${i}: ${seq.map((x) => x.toFixed(3))}`);
  }
  blue.forEach((_, i) => { const d = Math.abs(Ls.pos[i] - Ls.neg[i]); if (d > 0.02) problems.push(`${mode} step ${i + 1}: arm lightness differs by ${d.toFixed(3)}`); });
  console.log(`${mode} diverging  neg ${red.join(" ")} | ${NEUTRAL[mode]} | pos ${blue.join(" ")}`);
  console.log(`   L pos ${Ls.pos.map((x) => x.toFixed(3))}  L neg ${Ls.neg.map((x) => x.toFixed(3))}`);

  out.sequential[mode] = SEQ[mode].map((k) => BLUE[k]);
  const sL = out.sequential[mode].map(L_of);
  for (let i = 1; i < sL.length; i++) if ((sL[i] - sL[i - 1]) * dir <= 0) problems.push(`${mode} sequential not monotone at ${i}`);
  console.log(`${mode} sequential ${out.sequential[mode].join(" ")}`);
}

writeFileSync(new URL("../lib/palette.generated.ts", import.meta.url),
`// GENERATED by scripts/gen-palette.mjs -- do not edit. Run \`npm run gen:palette\`.
// Blue steps + neutral + surfaces are from the dataviz reference palette. The RED ARM IS DERIVED
// (same OKLCH lightness as each blue step; hue from the documented categorical red) because
// the reference documents no red ramp. See the header of the generator.
export const PALETTE = ${JSON.stringify(out, null, 2)} as const;
`);
if (problems.length) { console.error("\nPALETTE PROBLEMS:\n" + problems.join("\n")); process.exit(1); }
console.log("\npalette checks passed: arms monotone away from neutral, arm lightness matched within 0.02");
