import Link from "next/link";
import { ApiProblem, attempt } from "@/components/ApiProblem";
import { apiGet, studyHref, type Kind, type StudySummary } from "@/lib/api";
import { fmt, metricLabel, paramLabel } from "@/lib/format";

export const dynamic = "force-dynamic";

const SHAPE: Record<Kind, { label: string; hint: string }> = {
  grid: { label: "Grid", hint: "discrete parameters — every combination is a cell" },
  scatter: { label: "Trial scatter", hint: "continuous parameters (e.g. Optuna) — a cloud of trials, not a lattice" },
  line: { label: "One parameter", hint: "a single parameter varies" },
  filter_set: { label: "Filter comparison", hint: "same params, different entry filters" },
  walk_forward: { label: "Walk-forward", hint: "each window is its own out-of-sample run" },
  single: { label: "Single config", hint: "one configuration, no variation" },
};

export default async function StrategyPage({ params }: { params: Promise<{ strategy: string }> }) {
  const { strategy } = await params;
  const [studies, err] = await attempt(apiGet<StudySummary[]>(`/api/strategies/${encodeURIComponent(strategy)}/studies`));
  if (err || !studies) return <ApiProblem error={err ?? "no data"} />;

  return (
    <div className="space-y-5">
      <div>
        <p className="text-sm text-ink2"><Link href="/" className="underline decoration-hair underline-offset-2">Strategies</Link> /</p>
        <h1 className="text-2xl font-semibold">{strategy.replace(/_/g, " ")}</h1>
        <p className="mt-1 text-sm text-ink2">
          What questions have been asked. Each row is one study; open it to see which parameter combinations were run
          and how they scored.
        </p>
      </div>

      <div className="card overflow-x-auto">
        <table className="data">
          <thead>
            <tr>
              <th>Study</th><th>Window</th><th>Runs</th><th>Shape</th><th>Varies</th><th>Chain data</th><th>{metricLabel("sharpe_ratio")} (best · median for walk-forward)</th><th>Memo</th>
            </tr>
          </thead>
          <tbody>
            {studies.map((s) => (
              <tr key={`${s.tag}|${s.window}`}>
                <td>
                  <Link className="font-semibold underline decoration-hair underline-offset-2" href={studyHref(s)}>{s.tag}</Link>
                </td>
                <td>{s.window}</td>
                <td>{s.n_runs}</td>
                <td title={SHAPE[s.kind].hint}>{SHAPE[s.kind].label}</td>
                <td className="max-w-[34ch] text-ink2">
                  {s.varying.length ? s.varying.map((v) => paramLabel(v.key)).join(", ") : "—"}
                </td>
                <td className="text-ink2">{s.data_sources.map((d) => d ?? "unknown").join(" + ")}</td>
                <td>
                  {s.median ? (
                    <span title="median across the out-of-sample years; the best year would be a selection-bias summary">
                      median {fmt(s.median.metric, s.median.value)}
                    </span>
                  ) : s.best ? (
                    <Link className="underline decoration-hair underline-offset-2" href={`/run/${s.best.hash}`}>
                      {fmt(s.best.metric, s.best.value)}
                    </Link>
                  ) : "—"}
                  {s.n_duplicate_runs > 0 && (
                    <span className="warn ml-2" title="identical params stored under more than one hash">
                      ≠ {s.n_duplicate_runs} duplicated
                    </span>
                  )}
                </td>
                <td>
                  <Link className="underline decoration-hair underline-offset-2" href={`/study/${encodeURIComponent(s.tag)}`}>memo</Link>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="text-xs text-ink2">
        Best is the highest Sharpe within the study. Walk-forward shows the median instead, because the best of several independent
        out-of-sample years is a selection-bias summary. Every number here is in-sample unless the tag is wf_oos.
      </p>
    </div>
  );
}
