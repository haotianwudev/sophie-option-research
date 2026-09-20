"use client";

import { useEffect, useState } from "react";
import { Area, AreaChart, CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { apiGet, type EquityResp } from "@/lib/api";

const usd = (v: number) => `$${Math.round(v).toLocaleString()}`;
const pct = (v: number) => `${(v * 100).toFixed(1).replace("-", "−")}%`;
const tick = { fontSize: 11, fill: "var(--muted)" };

function Tip({ active, payload, label, fmtVal, name }: {
  active?: boolean; payload?: { value: number }[]; label?: string; fmtVal: (v: number) => string; name: string;
}) {
  if (!active || !payload?.length) return null;
  return (
    <div className="rounded-lg border border-hair bg-surface p-2 text-xs shadow-lg">
      <div className="text-ink2">{label}</div>
      <div className="mt-0.5 flex items-center gap-2">
        <span aria-hidden className="inline-block h-0.5 w-3 bg-accent" />
        <b className="tnum text-sm text-ink">{fmtVal(payload[0].value)}</b>
        <span className="text-ink2">{name}</span>
      </div>
    </div>
  );
}

/** Two single-series charts, stacked: equity, then drawdown. Different measures get their own
 *  axis; never a dual-axis chart. Lines are 2px, no dots, area at a 10% wash. */
export function EquityChart({ hash }: { hash: string }) {
  const [data, setData] = useState<EquityResp | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    apiGet<EquityResp>(`/api/runs/${hash}/equity`).then(setData).catch((e) => setErr(String(e.message ?? e)));
  }, [hash]);

  if (err) return <p className="warn inline-block">⚠ {err}</p>;
  if (!data) return <div className="h-[380px] animate-pulse rounded-lg bg-grid" aria-busy />;
  const pts = data.points;
  // one tick per calendar year, at its first data point (labelling raw dates by year repeats years)
  const yearTicks = pts.filter((p, i) => i === 0 || p.date.slice(0, 4) !== pts[i - 1].date.slice(0, 4)).map((p) => p.date);

  return (
    <div className="space-y-4">
      <figure>
        <figcaption className="mb-1 text-sm font-semibold">Equity</figcaption>
        <div className="h-64" role="img" aria-label={`Equity curve over ${pts.length} days`}>
          <ResponsiveContainer>
            <LineChart data={pts} margin={{ top: 6, right: 12, bottom: 0, left: 0 }}>
              <CartesianGrid stroke="var(--grid)" vertical={false} />
              <XAxis dataKey="date" ticks={yearTicks} interval={0} tick={tick} tickFormatter={(d: string) => d.slice(0, 4)} axisLine={{ stroke: "var(--axis)" }} tickLine={false} />
              <YAxis tick={tick} tickFormatter={usd} width={72} axisLine={false} tickLine={false} domain={["auto", "auto"]} />
              <Tooltip cursor={{ stroke: "var(--axis)" }} content={<Tip fmtVal={usd} name="equity" />} />
              <Line type="monotone" dataKey="equity" stroke="var(--accent)" strokeWidth={2} dot={false} activeDot={{ r: 4, stroke: "var(--surface)", strokeWidth: 2 }} isAnimationActive={false} />
            </LineChart>
          </ResponsiveContainer>
        </div>
      </figure>
      <figure>
        <figcaption className="mb-1 text-sm font-semibold">Drawdown from peak</figcaption>
        <div className="h-40" role="img" aria-label="Drawdown from running peak">
          <ResponsiveContainer>
            <AreaChart data={pts} margin={{ top: 6, right: 12, bottom: 0, left: 0 }}>
              <CartesianGrid stroke="var(--grid)" vertical={false} />
              <XAxis dataKey="date" ticks={yearTicks} interval={0} tick={tick} tickFormatter={(d: string) => d.slice(0, 4)} axisLine={{ stroke: "var(--axis)" }} tickLine={false} />
              <YAxis tick={tick} tickFormatter={pct} width={72} axisLine={false} tickLine={false} />
              <Tooltip cursor={{ stroke: "var(--axis)" }} content={<Tip fmtVal={pct} name="drawdown" />} />
              <Area type="monotone" dataKey="drawdown" stroke="var(--ink-2)" strokeWidth={2} fill="var(--ink-2)" fillOpacity={0.1} dot={false} isAnimationActive={false} />
            </AreaChart>
          </ResponsiveContainer>
        </div>
      </figure>
      <p className="text-xs text-ink2">{data.note}</p>
    </div>
  );
}
