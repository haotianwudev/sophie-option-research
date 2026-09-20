"use client";

import { useRouter } from "next/navigation";
import { useMemo, useState } from "react";
import type { GridResponse, Point } from "@/lib/api";
import { fmt, metricLabel, paramLabel } from "@/lib/format";
import { makeScale } from "@/lib/scale";
import { ScaleLegend } from "./ScaleLegend";

const W = 760;
const H = 440;
const M = { l: 64, r: 24, t: 16, b: 54 };

/** Linear ticks that land on round numbers. */
function ticks(lo: number, hi: number, n = 5): number[] {
  if (!(hi > lo)) return [lo];
  const raw = (hi - lo) / n;
  const pow = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * pow).find((s) => s >= raw) ?? raw;
  const out: number[] = [];
  for (let v = Math.ceil(lo / step) * step; v <= hi + 1e-9; v += step) out.push(+v.toFixed(10));
  return out;
}

/**
 * Trial scatter for studies whose parameters are continuous (Optuna): a cloud of trials, not a
 * lattice, so binning them onto a grid would invent cells that never existed. Colour is the chosen
 * metric on the same scale the grid uses. Marks are 12px dots with the 2px surface ring; each has a
 * 28px transparent hit area, because an 8px dot is a pinpoint nobody hits reliably.
 */
export function TrialScatter({ data }: { data: GridResponse }) {
  const router = useRouter();
  const pts = useMemo(() => (data.points ?? []).filter((p) => p.x != null && p.y != null), [data.points]);
  const scale = useMemo(() => makeScale(data.metric, pts.map((p) => p.value)), [data.metric, pts]);
  const [tip, setTip] = useState<Point | null>(null);
  if (!pts.length || !data.y) return <p className="text-sm text-ink2">No plottable trials.</p>;

  const xs = pts.map((p) => p.x as number);
  const ys = pts.map((p) => p.y as number);
  const pad = (lo: number, hi: number) => {
    const d = (hi - lo || Math.abs(hi) || 1) * 0.06;
    return [lo - d, hi + d] as const;
  };
  const [x0, x1] = pad(Math.min(...xs), Math.max(...xs));
  const [y0, y1] = pad(Math.min(...ys), Math.max(...ys));
  const sx = (v: number) => M.l + ((v - x0) / (x1 - x0)) * (W - M.l - M.r);
  const sy = (v: number) => H - M.b - ((v - y0) / (y1 - y0)) * (H - M.t - M.b);
  const xTitle = paramLabel(data.x.key);
  const yTitle = paramLabel(data.y.key);

  return (
    <div className="space-y-3">
      <ScaleLegend scale={scale} metric={data.metric} />
      <div className="relative">
        <svg viewBox={`0 0 ${W} ${H}`} className="w-full" role="img" aria-label={`${pts.length} trials: ${yTitle} against ${xTitle}, coloured by ${metricLabel(data.metric)}`}>
          {ticks(y0, y1).map((t) => (
            <g key={`y${t}`}>
              <line x1={M.l} x2={W - M.r} y1={sy(t)} y2={sy(t)} stroke="var(--grid)" strokeWidth={1} />
              <text x={M.l - 8} y={sy(t)} dy="0.32em" textAnchor="end" fontSize={11} fill="var(--muted)" className="tnum">
                {+t.toFixed(3)}
              </text>
            </g>
          ))}
          {ticks(x0, x1).map((t) => (
            <text key={`x${t}`} x={sx(t)} y={H - M.b + 18} textAnchor="middle" fontSize={11} fill="var(--muted)" className="tnum">
              {+t.toFixed(3)}
            </text>
          ))}
          <line x1={M.l} x2={W - M.r} y1={H - M.b} y2={H - M.b} stroke="var(--axis)" />
          <text x={(M.l + W - M.r) / 2} y={H - 10} textAnchor="middle" fontSize={12} fill="var(--ink-2)">{xTitle}</text>
          <text transform={`translate(14 ${(M.t + H - M.b) / 2}) rotate(-90)`} textAnchor="middle" fontSize={12} fill="var(--ink-2)">{yTitle}</text>

          {pts.map((p) => {
            const cx = sx(p.x as number);
            const cy = sy(p.y as number);
            const { bg } = scale.at(p.value);
            return (
              <g key={p.hash}>
                <circle cx={cx} cy={cy} r={6} style={{ fill: bg }} stroke="var(--surface)" strokeWidth={2} />
                <circle
                  cx={cx}
                  cy={cy}
                  r={14}
                  fill="transparent"
                  tabIndex={0}
                  role="link"
                  aria-label={`${xTitle} ${p.x}, ${yTitle} ${p.y}: ${metricLabel(data.metric)} ${fmt(data.metric, p.value)}`}
                  className="cursor-pointer"
                  onPointerEnter={() => setTip(p)}
                  onPointerLeave={() => setTip(null)}
                  onFocus={() => setTip(p)}
                  onBlur={() => setTip(null)}
                  onClick={() => router.push(`/run/${p.hash}`)}
                  onKeyDown={(e) => e.key === "Enter" && router.push(`/run/${p.hash}`)}
                />
              </g>
            );
          })}
        </svg>
        {tip && (
          <div
            role="tooltip"
            className="pointer-events-none absolute z-10 w-64 -translate-x-1/2 -translate-y-full rounded-lg border border-hair bg-surface p-2.5 text-xs shadow-lg"
            style={{ left: `${(sx(tip.x as number) / W) * 100}%`, top: `${(sy(tip.y as number) / H) * 100}%`, marginTop: -10 }}
          >
            <div className="tnum mt-0.5 text-lg font-semibold leading-none text-ink">{fmt(data.metric, tip.value)}</div>
            <div className="text-ink2">{metricLabel(data.metric)} · {fmt("total_trades", tip.total_trades)} trades</div>
            <div className="mt-1 text-ink2">
              {xTitle} <b className="text-ink">{+(tip.x as number).toFixed(4)}</b> · {yTitle}{" "}
              <b className="text-ink">{+(tip.y as number).toFixed(4)}</b>
            </div>
            {tip.entry_filter && <div className="mt-1 break-words text-ink2">filter: {tip.entry_filter}</div>}
            <div className="mt-1 text-muted">{tip.hash} · click to open</div>
          </div>
        )}
      </div>
      <details className="text-sm">
        <summary className="cursor-pointer text-ink2">Table view</summary>
        <div className="mt-2 overflow-x-auto">
          <table className="data">
            <thead>
              <tr><th>{xTitle}</th><th>{yTitle}</th><th>{metricLabel(data.metric)}</th><th>Trades</th><th>Filter</th><th>Run</th></tr>
            </thead>
            <tbody>
              {[...pts].sort((a, b) => (b.value ?? -Infinity) - (a.value ?? -Infinity)).map((p) => (
                <tr key={p.hash}>
                  <td>{+(p.x as number).toFixed(4)}</td>
                  <td>{+(p.y as number).toFixed(4)}</td>
                  <td>{fmt(data.metric, p.value)}</td>
                  <td>{fmt("total_trades", p.total_trades)}</td>
                  <td>{p.entry_filter ?? "—"}</td>
                  <td><a className="underline decoration-hair underline-offset-2" href={`/run/${p.hash}`}>{p.hash}</a></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
    </div>
  );
}
