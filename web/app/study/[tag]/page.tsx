import Link from "next/link";
import { ApiProblem, attempt } from "@/components/ApiProblem";
import { CaveatList } from "@/components/CaveatList";
import { apiGet, type Memo, type RunRow } from "@/lib/api";
import { fmt, metricLabel } from "@/lib/format";

export const dynamic = "force-dynamic";

export default async function StudyMemoPage({ params }: { params: Promise<{ tag: string }> }) {
  const { tag } = await params;
  const [memo, err] = await attempt(apiGet<Memo>(`/api/studies/${encodeURIComponent(tag)}/memo`));
  if (err || !memo) return <ApiProblem error={err ?? "no data"} />;

  // the memo rows carry no window, so join it back on from the runs API by hash
  const [runs] = await attempt(apiGet<RunRow[]>(`/api/runs?tag=${encodeURIComponent(tag)}&limit=1000`));
  const windowOf = new Map((runs ?? []).map((r) => [r.hash, r.window]));
  const windows = memo.method.windows;
  const blended = windows.length > 1;
  const cols = memo.headline_metrics;

  return (
    <div className="space-y-5">
      <div>
        <p className="text-sm text-ink2"><Link href="/" className="underline decoration-hair underline-offset-2">Strategies</Link> /</p>
        <h1 className="text-2xl font-semibold">Study memo: {memo.study_key}</h1>
        <p className="mt-1 text-sm text-ink2">
          {memo.method.n_runs} runs · {memo.method.strategy.join(", ")}
          {memo.hypothesis && <> · {memo.hypothesis}</>}
        </p>
      </div>

      {blended && (
        <div className="card space-y-1 border-l-4 p-4" style={{ borderLeftColor: "var(--warn)" }}>
          <p><span className="warn">⚠ This memo blends {windows.length} date windows</span></p>
          <p className="text-sm text-ink2">
            The memo is built per tag, not per window, so its results table and “best run” put runs from different
            market periods side by side: {windows.join(" and ")}. Compare within a window — the per-study pages do.
          </p>
        </div>
      )}

      <CaveatList caveats={memo.caveats} />

      <section className="card space-y-3 p-4">
        <h2 className="text-base font-semibold">Runs, best Sharpe first</h2>
        <div className="overflow-x-auto">
          <table className="data">
            <thead>
              <tr>
                <th>Window</th><th>Configuration</th><th>Filter</th>
                {cols.map((c) => <th key={c}>{metricLabel(c)}</th>)}
                <th>Run</th>
              </tr>
            </thead>
            <tbody>
              {memo.results.map((r) => (
                <tr key={String(r.config_hash)}>
                  <td className="whitespace-nowrap">{windowOf.get(String(r.config_hash)) ?? "—"}</td>
                  <td className="min-w-[24ch] max-w-[40ch] break-words">{String(r.name).split("|").slice(1).join("|") || String(r.name)}</td>
                  <td className="max-w-[20ch] break-words">{r.entry_filter || "none"}</td>
                  {cols.map((c) => <td key={c}>{fmt(c, typeof r[c] === "number" ? (r[c] as number) : null)}</td>)}
                  <td><Link className="underline decoration-hair underline-offset-2" href={`/run/${r.config_hash}`}>{r.config_hash}</Link></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {memo.notes && <p className="text-sm text-ink2">{memo.notes}</p>}
      </section>
    </div>
  );
}
