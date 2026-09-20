"use client";

import Link from "next/link";
import { useMemo, useRef, useState } from "react";
import type { Cell, GridResponse } from "@/lib/api";
import { fmt, metricLabel, paramLabel } from "@/lib/format";
import { makeScale } from "@/lib/scale";
import { ScaleLegend } from "./ScaleLegend";

/**
 * The coverage grid: one cell per parameter combination, coloured by the chosen metric.
 * The point of the view is the difference between three cell states, so each looks unlike the others:
 *   - ran: filled from the metric scale (a value of exactly 0 is the neutral gray midpoint, not empty)
 *   - not run: transparent with a dashed outline and "not run"
 *   - several runs disagree: filled, with a "≠ ×N" mark -- identical params stored under more than one
 *     hash with different results; the cell shows the newest and says so, never silently picks.
 */
export function CoverageGrid({ data, onEmpty }: { data: GridResponse; onEmpty?: (c: Cell) => void }) {
  const cells = data.cells ?? [];
  const scale = useMemo(() => makeScale(data.metric, cells.map((c) => c.value)), [data.metric, cells]);
  const byKey = useMemo(() => new Map(cells.map((c) => [`${c.x}|${c.y}`, c])), [cells]);
  const xs = data.x.labels;
  const ys: (string | null)[] = data.y ? data.y.labels : [null];

  const wrap = useRef<HTMLDivElement>(null);
  const [tip, setTip] = useState<{ cell: Cell; left: number; top: number } | null>(null);
  const show = (cell: Cell, el: HTMLElement) => {
    const w = wrap.current?.getBoundingClientRect();
    if (!w) return;
    const r = el.getBoundingClientRect();
    setTip({ cell, left: r.left - w.left + r.width / 2, top: r.top - w.top });
  };

  const xTitle = paramLabel(data.x.key);
  const yTitle = data.y ? paramLabel(data.y.key) : "";

  return (
    <div className="space-y-3">
      <ScaleLegend scale={scale} metric={data.metric} showCellStates />

      <div ref={wrap} className="relative overflow-x-auto pb-1" onPointerLeave={() => setTip(null)}>
        <div
          role="grid"
          aria-label={`${metricLabel(data.metric)} by ${xTitle}${yTitle ? ` and ${yTitle}` : ""}`}
          className="grid gap-[2px]"
          style={{ gridTemplateColumns: `max-content repeat(${xs.length}, minmax(88px, 1fr))` }}
        >
          <div className="px-2 pb-1 text-xs leading-tight text-ink2">
            {yTitle && <div>{yTitle} ↓</div>}
            <div>{xTitle} →</div>
          </div>
          {xs.map((x) => (
            <div key={x} className="tnum px-1 pb-1 text-center text-xs font-semibold text-ink2">
              {x}
            </div>
          ))}

          {ys.map((y) => (
            <Row key={String(y)} y={y}>
              {xs.map((x) => {
                const cell = byKey.get(`${x}|${y}`);
                if (!cell) return <div key={x} />;
                return (
                  <GridCell
                    key={x}
                    cell={cell}
                    metric={data.metric}
                    scale={scale}
                    onShow={show}
                    onHide={() => setTip(null)}
                    onEmpty={onEmpty}
                  />
                );
              })}
            </Row>
          ))}
        </div>

        {tip && <Tip cell={tip.cell} data={data} left={tip.left} top={tip.top} />}
      </div>

      <details className="text-sm">
        <summary className="cursor-pointer text-ink2">Table view</summary>
        <div className="mt-2 overflow-x-auto">
          <table className="data">
            <thead>
              <tr>
                <th>{xTitle}</th>
                {data.y && <th>{yTitle}</th>}
                <th>{metricLabel(data.metric)}</th>
                <th>Trades</th>
                <th>Runs</th>
                <th>Run</th>
              </tr>
            </thead>
            <tbody>
              {cells.map((c) => (
                <tr key={`${c.x}|${c.y}`}>
                  <td>{c.x}</td>
                  {data.y && <td>{c.y}</td>}
                  <td>{c.ran ? fmt(data.metric, c.value) : "not run"}</td>
                  <td>{c.ran ? fmt("total_trades", c.total_trades) : "—"}</td>
                  <td>{c.n_runs ? `${c.n_runs}${c.conflict ? " (disagree)" : ""}` : "0"}</td>
                  <td>
                    {c.hash ? (
                      <Link className="underline decoration-hair underline-offset-2" href={`/run/${c.hash}`}>
                        {c.hash}
                      </Link>
                    ) : (
                      "—"
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
    </div>
  );
}

function Row({ y, children }: { y: string | null; children: React.ReactNode }) {
  return (
    <>
      <div className="tnum flex items-center justify-end px-2 text-xs font-semibold text-ink2">{y ?? ""}</div>
      {children}
    </>
  );
}

function GridCell({
  cell,
  metric,
  scale,
  onShow,
  onHide,
  onEmpty,
}: {
  cell: Cell;
  metric: string;
  scale: ReturnType<typeof makeScale>;
  onShow: (c: Cell, el: HTMLElement) => void;
  onHide: () => void;
  onEmpty?: (c: Cell) => void;
}) {
  const base = "relative flex min-h-[58px] flex-col items-center justify-center rounded-[6px] px-2 py-1.5 text-center";
  if (!cell.ran) {
    return (
      <button
        type="button"
        role="gridcell"
        className={`${base} border border-dashed border-muted bg-transparent text-xs text-ink2 hover:bg-grid`}
        aria-label={`${cell.x}${cell.y ? `, ${cell.y}` : ""}: not run. Show how to run it`}
        onPointerEnter={(e) => onShow(cell, e.currentTarget)}
        onFocus={(e) => onShow(cell, e.currentTarget)}
        onBlur={onHide}
        onClick={() => onEmpty?.(cell)}
      >
        not run
      </button>
    );
  }
  const { bg, fg } = scale.at(cell.value);
  return (
    <Link
      href={`/run/${cell.hash}`}
      role="gridcell"
      className={`${base} transition-[filter] hover:brightness-110 hover:contrast-110`}
      style={{ background: bg, color: fg }}
      aria-label={`${cell.x}${cell.y ? `, ${cell.y}` : ""}: ${metricLabel(metric)} ${fmt(metric, cell.value)}${
        cell.conflict ? `, ${cell.n_runs} runs disagree` : ""
      }`}
      onPointerEnter={(e) => onShow(cell, e.currentTarget)}
      onFocus={(e) => onShow(cell, e.currentTarget)}
      onBlur={onHide}
    >
      <span className="tnum text-[15px] font-semibold leading-tight">{fmt(metric, cell.value)}</span>
      <span className="tnum text-[11px] leading-tight opacity-90">{fmt("total_trades", cell.total_trades)} trades</span>
      {cell.n_runs > 1 && (
        <span
          className="absolute right-1 top-0.5 text-[10px] font-bold leading-none"
          title={cell.conflict ? "several runs with identical params disagree" : "several runs with identical params"}
        >
          {cell.conflict ? "≠ " : ""}×{cell.n_runs}
        </span>
      )}
    </Link>
  );
}

function Tip({ cell, data, left, top }: { cell: Cell; data: GridResponse; left: number; top: number }) {
  return (
    <div
      role="tooltip"
      className="pointer-events-none absolute z-10 w-60 -translate-x-1/2 -translate-y-full rounded-lg border border-hair bg-surface p-2.5 text-xs shadow-lg"
      style={{ left, top: top - 6 }}
    >
      <div className="text-ink2">
        {paramLabel(data.x.key)} <b className="text-ink">{cell.x}</b>
        {data.y && (
          <>
            {" · "}
            {paramLabel(data.y.key)} <b className="text-ink">{cell.y}</b>
          </>
        )}
      </div>
      {cell.ran ? (
        <>
          <div className="tnum mt-1 text-lg font-semibold leading-none text-ink">{fmt(data.metric, cell.value)}</div>
          <div className="text-ink2">
            {metricLabel(data.metric)} · {fmt("total_trades", cell.total_trades)} trades
          </div>
          {cell.n_runs > 1 && (
            <div className="mt-1.5 text-ink2">
              <span className="warn">{cell.conflict ? "≠ runs disagree" : `${cell.n_runs} runs`}</span>{" "}
              {cell.conflict
                ? `${cell.n_runs} runs share these params but differ by ${fmt(data.metric, cell.spread)}${
                    (cell.data_sources?.length ?? 0) > 1 ? `: they used different chain data (${cell.data_sources?.join(" vs ")})` : ""
                  }. Showing the newest.`
                : "identical params and results."}
            </div>
          )}
          <div className="mt-1 text-muted">{cell.hash} · click to open</div>
        </>
      ) : (
        <div className="mt-1 text-ink2">No run in the store for this combination. Click for the call to make one.</div>
      )}
    </div>
  );
}
