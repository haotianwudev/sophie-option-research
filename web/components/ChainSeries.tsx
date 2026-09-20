"use client";

import { CartesianGrid, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

type Props = {
  dates: string[];
  rows: number[];
  expirations: number[];
  /** first date of each source segment after the first: drawn as a labelled seam */
  seams: { date: string; label: string }[];
};

const tick = { fontSize: 11, fill: "var(--muted)" };
const n = (v: number) => v.toLocaleString();
/** Axis ceiling with headroom (for the seam labels), rounded up to 1, 2 or 5 x 10^k so ticks stay clean. */
const niceCeil = (max: number) => {
  const target = max * 1.12;
  const mag = 10 ** Math.floor(Math.log10(target));
  return ([1, 2, 5, 10].map((m) => m * mag).find((v) => v >= target) ?? target);
};

function Tip({ active, payload, label, name }: { active?: boolean; payload?: { value: number }[]; label?: string; name: string }) {
  if (!active || !payload?.length) return null;
  return (
    <div className="rounded-lg border border-hair bg-surface p-2 text-xs shadow-lg">
      <div className="text-ink2">{label}</div>
      <div className="mt-0.5 flex items-center gap-2">
        <span aria-hidden className="inline-block h-0.5 w-3 bg-accent" />
        <b className="tnum text-sm text-ink">{n(payload[0].value)}</b>
        <span className="text-ink2">{name}</span>
      </div>
    </div>
  );
}

/** Contracts per day and expirations per day, one series each on its own axis (never dual-axis). A sudden
 *  cliff means a truncated chain; a step at a labelled seam is a source change, not a fault. */
export function ChainSeries({ dates, rows, expirations, seams }: Props) {
  const data = dates.map((d, i) => ({ date: d, rows: rows[i], expirations: expirations[i] }));
  const yearTicks = data.filter((p, i) => i === 0 || p.date.slice(0, 4) !== data[i - 1].date.slice(0, 4)).map((p) => p.date);
  const chart = (key: "rows" | "expirations", title: string, name: string, height: string) => (
    <figure>
      <figcaption className="mb-1 text-sm font-semibold">{title}</figcaption>
      <div className={height} role="img" aria-label={title}>
        <ResponsiveContainer>
          <LineChart data={data} margin={{ top: 14, right: 12, bottom: 0, left: 0 }}>
            <CartesianGrid stroke="var(--grid)" vertical={false} />
            <XAxis dataKey="date" ticks={yearTicks} interval={0} tick={tick} tickFormatter={(d: string) => d.slice(0, 4)} axisLine={{ stroke: "var(--axis)" }} tickLine={false} />
            <YAxis tick={tick} tickFormatter={n} width={56} axisLine={false} tickLine={false} domain={[0, niceCeil]} />
            {seams.map((s) => (
              <ReferenceLine key={s.date} x={s.date} stroke="var(--axis)" strokeDasharray="3 3"
                label={{ value: s.label, position: "insideTopRight", fontSize: 10, fill: "var(--ink-2)" }} />
            ))}
            <Tooltip cursor={{ stroke: "var(--axis)" }} content={<Tip name={name} />} />
            <Line type="monotone" dataKey={key} stroke="var(--accent)" strokeWidth={2} dot={false}
              activeDot={{ r: 4, stroke: "var(--surface)", strokeWidth: 2 }} isAnimationActive={false} />
          </LineChart>
        </ResponsiveContainer>
      </div>
    </figure>
  );
  return (
    <div className="space-y-5">
      {chart("rows", "Contracts per day", "contracts", "h-56")}
      {chart("expirations", "Distinct expirations per day", "expirations", "h-44")}
    </div>
  );
}
