import Link from "next/link";
import { ApiProblem, attempt } from "@/components/ApiProblem";
import { RunsTable } from "@/components/RunsTable";
import { StudyExplorer } from "@/components/StudyExplorer";
import { apiGet, type GridResponse, type RunRow, type StudyParams } from "@/lib/api";

export const dynamic = "force-dynamic";

export default async function StudyPage({
  params,
}: {
  params: Promise<{ strategy: string; tag: string; window: string }>;
}) {
  const { strategy, tag, window } = await params;
  const base = `/api/strategies/${encodeURIComponent(strategy)}/studies/${encodeURIComponent(tag)}/${encodeURIComponent(window)}`;
  const [sp, err] = await attempt(apiGet<StudyParams>(`${base}/params`));
  if (err || !sp) return <ApiProblem error={err ?? "no data"} />;

  const explorable = sp.kind === "grid" || sp.kind === "scatter" || sp.kind === "line";
  let body: React.ReactNode;
  if (explorable) {
    const [grid, gerr] = await attempt(apiGet<GridResponse>(`${base}/grid?metric=${sp.default_metric}`));
    body = gerr || !grid ? <ApiProblem error={gerr ?? "no data"} /> : <StudyExplorer params={sp} initial={grid} />;
  } else {
    const [runs, rerr] = await attempt(
      apiGet<RunRow[]>(`/api/runs?strategy=${encodeURIComponent(strategy)}&tag=${encodeURIComponent(tag)}&limit=1000`),
    );
    const inStudy = (runs ?? []).filter((r) => r.study_window === window);
    body = rerr ? (
      <ApiProblem error={rerr} />
    ) : (
      <div className="space-y-3">
        <p className="text-sm text-ink2">
          {sp.kind === "filter_set"
            ? "Same parameters, different entry filters — there is no numeric parameter surface to draw, so these are listed side by side."
            : sp.kind === "walk_forward"
              ? "Walk-forward: each row is an out-of-sample year run with parameters frozen from its training window. A dedicated in-sample-to-out-of-sample timeline is not built yet."
              : "A single configuration."}
        </p>
        <div className="card">
          <RunsTable
            runs={[...inStudy].sort((a, b) => a.window.localeCompare(b.window) || (b.metrics.sharpe_ratio ?? -9) - (a.metrics.sharpe_ratio ?? -9))}
            paramKeys={sp.kind === "walk_forward" ? sp.varying.filter((v) => v.kind !== "categorical").map((v) => v.key) : []}
            showWindow={sp.kind === "walk_forward" || inStudy.some((r) => r.window !== window)}
          />
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-5">
      <div>
        <p className="text-sm text-ink2">
          <Link href="/" className="underline decoration-hair underline-offset-2">Strategies</Link> /{" "}
          <Link href={`/s/${encodeURIComponent(strategy)}`} className="underline decoration-hair underline-offset-2">{strategy.replace(/_/g, " ")}</Link> /
        </p>
        <h1 className="text-2xl font-semibold">
          {tag} <span className="text-base font-normal text-ink2">· {window} · {sp.n_runs} runs</span>
        </h1>
        <p className="mt-1 text-sm text-ink2">
          In-sample unless tagged wf_oos.{" "}
          <Link href={`/study/${encodeURIComponent(tag)}`} className="underline decoration-hair underline-offset-2">Study memo and caveats</Link>
        </p>
      </div>
      {body}
    </div>
  );
}
