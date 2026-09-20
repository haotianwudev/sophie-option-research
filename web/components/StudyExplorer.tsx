"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { apiGet, type Cell, type GridResponse, type StudyParams } from "@/lib/api";
import { levelLabel, metricLabel, paramLabel } from "@/lib/format";
import { missingRunSnippet } from "@/lib/snippet";
import { CoverageGrid } from "./CoverageGrid";
import { TrialScatter } from "./TrialScatter";

/**
 * Param level of the drill-down for one study. All state lives here; every control change refetches
 * the grid from the API, and while it loads the previous render stays on screen at reduced opacity
 * (no skeleton, no layout jump).
 *
 * Only pins the reader chose are sent as `fix`; the API pins every other non-axis param itself and
 * reports what it pinned, so each cell always means exactly one combination.
 */
export function StudyExplorer({ params, initial }: { params: StudyParams; initial: GridResponse }) {
  const [data, setData] = useState(initial);
  const [x, setX] = useState(initial.x.key);
  const [y, setY] = useState<string | null>(initial.y?.key ?? null);
  const [metric, setMetric] = useState(initial.metric);
  const [pins, setPins] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [empty, setEmpty] = useState<Cell | null>(null);
  const [copied, setCopied] = useState(false);
  const first = useRef(true);

  const numeric = params.varying.filter((v) => v.kind !== "categorical");

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const q = new URLSearchParams({ x, metric });
      if (y) q.set("y", y);
      for (const [k, v] of Object.entries(pins)) q.append("fix", `${k}:${v}`);
      const path = `/api/strategies/${encodeURIComponent(params.strategy)}/studies/${encodeURIComponent(params.tag)}/${encodeURIComponent(params.window)}/grid?${q}`;
      setData(await apiGet<GridResponse>(path));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [x, y, metric, pins, params.strategy, params.tag, params.window]);

  useEffect(() => {
    if (first.current) {
      first.current = false; // the server already rendered `initial`
      return;
    }
    void load();
  }, [load]);

  const setAxis = (which: "x" | "y", key: string) => {
    if (which === "x") {
      if (key === y) setY(x);
      setX(key);
    } else {
      if (key === x) setX(y ?? key);
      setY(key);
    }
    setPins({});
    setEmpty(null);
  };

  const snippet = empty ? missingRunSnippet(data, params, empty.x, empty.y) : "";
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(snippet);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      /* clipboard unavailable: the snippet is selectable text */
    }
  };

  const nCells = data.n_cells ?? 0;
  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-end gap-x-4 gap-y-3">
        <Field label="Metric">
          <select className="btn" value={metric} onChange={(e) => setMetric(e.target.value)}>
            {params.metrics.map((m) => (
              <option key={m} value={m}>{metricLabel(m)}</option>
            ))}
          </select>
        </Field>
        <Field label="X axis">
          <select className="btn" value={x} onChange={(e) => setAxis("x", e.target.value)}>
            {numeric.map((v) => <option key={v.key} value={v.key}>{paramLabel(v.key)}</option>)}
          </select>
        </Field>
        {data.study_kind !== "line" && (
          <Field label="Y axis">
            <select className="btn" value={y ?? ""} onChange={(e) => setAxis("y", e.target.value)}>
              {numeric.map((v) => <option key={v.key} value={v.key}>{paramLabel(v.key)}</option>)}
            </select>
          </Field>
        )}
        {data.free.map((f) => (
          <Field key={f.key} label={`${paramLabel(f.key)} ${f.key in data.fixed ? "(held at)" : "(all)"}`}>
            <select
              className="btn"
              value={data.fixed[f.key] ?? ""}
              onChange={(e) => setPins((p) => ({ ...p, [f.key]: e.target.value }))}
            >
              {!(f.key in data.fixed) && <option value="">all</option>}
              {f.labels.map((l) => (
                <option key={l} value={l}>{l} ({f.counts[l]} runs)</option>
              ))}
            </select>
          </Field>
        ))}
        <span className="pb-1 text-xs text-ink2" aria-live="polite">
          {data.kind === "grid"
            ? `${data.n_cells_run} of ${nCells} combinations run${data.n_cells_conflict ? ` · ${data.n_cells_conflict} disagree` : ""}`
            : `${data.points?.length ?? 0} trials`}
          {" · "}window {params.window}
        </span>
      </div>

      {error && <p className="warn inline-block">⚠ {error}</p>}

      <div className={`card p-4 transition-opacity ${loading ? "opacity-60" : ""}`}>
        {data.kind === "scatter" ? <TrialScatter data={data} /> : <CoverageGrid data={data} onEmpty={setEmpty} />}
      </div>

      {empty && (
        <div className="card p-4">
          <div className="mb-2 flex items-center justify-between gap-3">
            <p className="text-sm text-ink2">
              No run for <b className="text-ink">{levelLabel(empty.x)}{empty.y ? ` · ${empty.y}` : ""}</b>. The viewer is read-only, so here is the call to make it in a notebook:
            </p>
            <div className="flex gap-2">
              <button type="button" className="btn" onClick={copy}>{copied ? "Copied" : "Copy"}</button>
              <button type="button" className="btn" onClick={() => setEmpty(null)}>Close</button>
            </div>
          </div>
          <pre className="overflow-x-auto rounded-lg border border-hair bg-page p-3 text-xs leading-relaxed">{snippet}</pre>
        </div>
      )}

      <aside className="card p-4">
        <h3 className="mb-2 text-sm font-semibold">Held constant in this study</h3>
        <dl className="grid grid-cols-[max-content_1fr] gap-x-4 gap-y-1 text-sm">
          {Object.entries(params.constant).map(([k, v]) => (
            <div key={k} className="contents">
              <dt className="text-ink2">{paramLabel(k)}</dt>
              <dd className="tnum">{levelLabel(v)}</dd>
            </div>
          ))}
        </dl>
        <p className="mt-3 text-xs text-ink2">
          Delta band edges (min/max) move with the target at a fixed width and are not offered as axes.
          Chain data source and simulation settings are derived from each run&apos;s config hash, not stored.
        </p>
      </aside>
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="flex flex-col gap-1 text-xs text-ink2">
      {label}
      {children}
    </label>
  );
}
