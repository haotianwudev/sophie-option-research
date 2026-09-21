import Link from "next/link";
import { ApiProblem, attempt } from "@/components/ApiProblem";
import { DataSummary } from "@/components/DataSummary";
import type { Availability } from "@/lib/availability-types";
import { apiGet, type Health, type StrategySummary } from "@/lib/api";

export const dynamic = "force-dynamic";

export default async function Home() {
  const [strategies, err] = await attempt(apiGet<StrategySummary[]>("/api/strategies"));
  const [health] = await attempt(apiGet<Health>("/api/health"));
  // Short timeout: the first availability call after an API restart scans the whole archive (~10-20 s), and the
  // home page must not wait for that. A timeout renders "still checking" instead.
  const [avail] = await attempt(apiGet<Availability>("/api/data/availability", { signal: AbortSignal.timeout(4000) }));
  if (err || !strategies) return <ApiProblem error={err ?? "no data"} />;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Strategies</h1>
        <p className="mt-1 text-sm text-ink2">
          Pick a strategy, then a study, then a parameter combination. A study is one tag on one date window — the
          unit inside which runs can be compared.
        </p>
      </div>

      <DataSummary a={avail} />

      <ul className="grid gap-3 sm:grid-cols-2">
        {strategies.map((s) => (
          <li key={s.id}>
            <Link href={`/s/${encodeURIComponent(s.id)}`} className="card block p-4 hover:bg-grid">
              <div className="text-lg font-semibold">{s.id.replace(/_/g, " ")}</div>
              <div className="tnum mt-1 text-sm text-ink2">
                {s.n_runs} runs · {s.n_studies} studies
                {s.date_span[0] && ` · ${s.date_span[0].slice(0, 4)}–${s.date_span[1]?.slice(0, 4)}`}
              </div>
              <div className="mt-2 flex flex-wrap gap-1.5 text-xs text-ink2">
                {s.tags.map((t) => (
                  <span key={t} className="rounded-full border border-hair px-2 py-0.5">{t}</span>
                ))}
              </div>
            </Link>
          </li>
        ))}
      </ul>

      {health && (
        <p className="text-xs text-ink2">
          Store: {health.n_runs} runs, {health.n_trade_logs} trade logs, last written {health.mtime.replace("T", " ")}.
          Chain data, derived from each run&apos;s config hash: {Object.entries(health.data_sources).map(([k, v]) => `${v} ${k}`).join(", ")}.
        </p>
      )}
    </div>
  );
}
