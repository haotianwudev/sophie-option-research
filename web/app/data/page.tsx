import { ApiProblem, attempt } from "@/components/ApiProblem";
import { ChainSeries } from "@/components/ChainSeries";
import { CoverageMatrix } from "@/components/CoverageMatrix";
import { apiGet } from "@/lib/api";
import type { Availability } from "@/lib/availability-types";

export const dynamic = "force-dynamic";

const STATUS = {
  ok: { icon: "✓", label: "Up to date", tone: "text-pos" },
  warning: { icon: "⚠", label: "Falling behind", tone: "" },
  stale: { icon: "⚠", label: "Stale", tone: "" },
} as const;

function Section({ title, hint, children }: { title: string; hint?: string; children: React.ReactNode }) {
  return (
    <section className="card space-y-3 p-4">
      <div>
        <h2 className="text-base font-semibold">{title}</h2>
        {hint && <p className="mt-0.5 text-xs text-ink2">{hint}</p>}
      </div>
      {children}
    </section>
  );
}

function Tile({ label, value, sub, warn }: { label: string; value: string; sub?: string; warn?: boolean }) {
  return (
    <div className="card p-3">
      <div className="text-xs text-ink2">{label}</div>
      <div className="tnum mt-1 text-2xl font-semibold leading-none">{value}</div>
      {sub && <div className={`mt-1.5 text-xs ${warn ? "text-ink" : "text-ink2"}`}>{warn && <span className="warn mr-1">⚠</span>}{sub}</div>}
    </div>
  );
}

const byYear = (dates: string[]) => {
  const m = new Map<string, string[]>();
  for (const d of dates) m.set(d.slice(0, 4), [...(m.get(d.slice(0, 4)) ?? []), d]);
  return [...m.entries()];
};

export default async function DataPage() {
  const [a, err] = await attempt(apiGet<Availability>("/api/data/availability"));
  if (err || !a) return <ApiProblem error={err ?? "no data"} />;

  if (!a.archive.exists || !a.freshness || !a.coverage || !a.segments || !a.years || !a.months || !a.series || !a.content) {
    return (
      <div className="space-y-4">
        <h1 className="text-2xl font-semibold">Chain data availability</h1>
        <p className="warn inline-block">⚠ No unified chain archive at {a.archive.path}</p>
        <p className="text-sm text-ink2">Set SPX_CHAIN_UNIFIED_DIR, or run the spx-option-chain-unify pipeline.</p>
      </div>
    );
  }
  const f = a.freshness;
  const cov = a.coverage;
  const st = STATUS[f.status];
  const pct = ((cov.present_sessions / cov.expected_sessions) * 100).toFixed(1);
  const seams = a.segments.slice(1).map((s) => ({ date: s.from, label: s.source.replace(/_(backfill|live)$/, "") }));
  const problemDays = a.content.thin_days.length;

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-2xl font-semibold">Chain data availability</h1>
        <p className="mt-1 text-sm text-ink2">
          The unified SPX option-chain archive backtests read, as of {a.as_of}. Read from the files on disk — each day&apos;s
          file is opened for its contract and expiration counts, not just checked for existence.
        </p>
      </div>

      {/* freshness first: "is my data current?" is the question a monitor answers before any other */}
      <div className="card space-y-1.5 border-l-4 p-4" style={{ borderLeftColor: f.status === "ok" ? "var(--pos-text)" : "var(--warn)" }}>
        <p className="flex items-center gap-2 text-base font-semibold">
          <span className={f.status === "ok" ? "text-pos" : "warn"}>{st.icon} {st.label}</span>
          <span className="tnum font-normal text-ink2">
            newest chain {f.latest_chain} · latest session {f.latest_expected_session}
          </span>
        </p>
        <p className="text-sm text-ink2">
          {f.lag_sessions === 0
            ? "Every session up to yesterday has a chain file."
            : `${f.lag_sessions} session${f.lag_sessions === 1 ? "" : "s"} behind: ${f.sessions_since.join(", ")}.`}{" "}
          Warning above {f.thresholds.ok_at_most}, stale above {f.thresholds.warning_at_most}. Backtests cannot see anything after {f.latest_chain}.
        </p>
      </div>

      <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-6">
        <Tile label="First day" value={a.archive.first ?? "—"} sub={`${a.archive.n_files?.toLocaleString()} files`} />
        <Tile label="Last day" value={a.archive.last ?? "—"} />
        <Tile label="Sessions covered" value={`${pct}%`} sub={`${cov.present_sessions.toLocaleString()} of ${cov.expected_sessions.toLocaleString()}`} />
        <Tile label="Gaps before the newest file" value={String(cov.missing_sessions.length)} warn={cov.missing_sessions.length > 0} sub={cov.missing_sessions.length ? "listed below" : undefined} />
        <Tile label="Files on closed days" value={String(cov.files_on_non_sessions.length)} warn={cov.files_on_non_sessions.length > 0} sub={cov.files_on_non_sessions.length ? "listed below" : undefined} />
        <Tile label="Thin or unreadable" value={String(problemDays)} warn={problemDays > 0} sub={problemDays ? "listed below" : "none found"} />
      </div>

      <Section title="Coverage by month" hint="Trading sessions with a chain file, per month.">
        <CoverageMatrix months={a.months} />
      </Section>

      <Section
        title="Sources"
        hint="The archive is stitched from different sources with different root symbols. A backtest that crosses a row below is crossing a schema seam."
      >
        <div className="overflow-x-auto">
          <table className="data">
            <thead><tr><th>Source</th><th>Root symbols</th><th>From</th><th>To</th><th>Days</th><th>Median contracts / day</th><th>Median expirations / day</th></tr></thead>
            <tbody>
              {a.segments.map((s) => (
                <tr key={s.from}>
                  <td>{s.source}</td><td>{s.roots}</td><td>{s.from}</td><td>{s.to}</td>
                  <td>{s.days.toLocaleString()}</td><td>{s.median_rows.toLocaleString()}</td><td>{s.median_expirations}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Section>

      <Section title="Contents per day" hint="A file can exist and still be unusable. A cliff here is a truncated chain; a step at a dashed line is a source change.">
        <ChainSeries dates={a.series.date} rows={a.series.rows} expirations={a.series.expirations} seams={seams} />
        <p className="text-xs text-ink2">
          Thin-day rule: {a.content.rule}. {problemDays === 0 ? "No day tripped it." : `${problemDays} day(s) tripped it.`}
        </p>
        {problemDays > 0 && (
          <table className="data">
            <thead><tr><th>Date</th><th>Contracts</th><th>Typical</th><th>Expirations</th><th>Typical</th><th>Source</th><th>Problem</th></tr></thead>
            <tbody>
              {a.content.thin_days.map((d) => (
                <tr key={d.date}>
                  <td>{d.date}</td><td>{d.rows.toLocaleString()}</td><td>{d.median_rows?.toLocaleString() ?? "—"}</td>
                  <td>{d.expirations}</td><td>{d.median_expirations ?? "—"}</td><td>{d.source}</td><td>{d.error ?? "thin"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Section>

      <Section title="By year">
        <div className="overflow-x-auto">
          <table className="data">
            <thead><tr><th>Year</th><th>Sessions</th><th>Present</th><th>Missing</th><th>Median contracts</th><th>Median expirations</th><th>Source</th></tr></thead>
            <tbody>
              {a.years.map((y) => (
                <tr key={y.year}>
                  <td>{y.year}</td><td>{y.expected}</td><td>{y.present}</td>
                  <td>{y.missing > 0 ? <span className="warn">▲ {y.missing}</span> : 0}</td>
                  <td>{y.median_rows?.toLocaleString() ?? "—"}</td><td>{y.median_expirations ?? "—"}</td><td>{y.sources.join(" + ")}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Section>

      <Section title={`Gaps (${cov.missing_sessions.length})`} hint="Trading days before the newest file that have no chain file. Backtests skip these days. Days after the newest file are freshness, not gaps.">
        {cov.missing_sessions.length === 0 ? (
          <p className="text-sm text-ink2">None.</p>
        ) : (
          <dl className="grid grid-cols-[max-content_1fr] gap-x-4 gap-y-1 text-sm">
            {byYear(cov.missing_sessions).map(([y, ds]) => (
              <div key={y} className="contents">
                <dt className="tnum font-semibold text-ink2">{y}</dt>
                <dd className="tnum">{ds.map((d) => d.slice(5)).join(" · ")}</dd>
              </div>
            ))}
          </dl>
        )}
      </Section>

      <Section
        title={`Files on closed days (${cov.files_on_non_sessions.length})`}
        hint="Chain files whose date the exchange calendar says was not a trading session."
      >
        {cov.files_on_non_sessions.length === 0 ? (
          <p className="text-sm text-ink2">None.</p>
        ) : (
          <>
            <dl className="grid grid-cols-[max-content_1fr] gap-x-4 gap-y-1 text-sm">
              {byYear(cov.files_on_non_sessions).map(([y, ds]) => (
                <div key={y} className="contents">
                  <dt className="tnum font-semibold text-ink2">{y}</dt>
                  <dd className="tnum">{ds.map((d) => d.slice(5)).join(" · ")}</dd>
                </div>
              ))}
            </dl>
            <p className="text-xs text-ink2">
              Checked 2026-09-20 on the 12 files then present: their quotes are not copies of the previous day (1.5–9%
              identical, the same as an ordinary pair of consecutive sessions) and their earliest expiration is what a
              closed day looks like (no same-day expiry). So they look like vendor snapshots taken while the market was
              closed. Whether those quotes were tradable is not known — a backtest can still enter or exit on them.
            </p>
          </>
        )}
      </Section>

      <Section
        title="Cached market series"
        hint="On-disk price caches used offline. When the Sophie Postgres is reachable the loaders prefer its fresher `prices` table, so a stale cache is not necessarily what a backtest saw; this page does not query the database."
      >
        <table className="data">
          <thead><tr><th>Series</th><th>First</th><th>Last</th><th>Age (days)</th><th>Rows</th></tr></thead>
          <tbody>
            {Object.entries(a.market_caches).map(([k, v]) => (
              <tr key={k}>
                <td>{k}</td>
                {v.exists ? (
                  <>
                    <td>{v.first}</td><td>{v.last}</td>
                    <td>{(v.age_days ?? 0) > 7 ? <span className="warn">⚠ {v.age_days}</span> : v.age_days}</td>
                    <td>{v.rows?.toLocaleString()}</td>
                  </>
                ) : (
                  <td colSpan={4}>not cached</td>
                )}
              </tr>
            ))}
          </tbody>
        </table>
      </Section>

      <Section title="Other sources on disk">
        <ul className="space-y-1 text-sm text-ink2">
          <li>
            Legacy OptionsDX conversion (<code>data/processed</code>):{" "}
            {a.legacy_processed.exists
              ? `${a.legacy_processed.n_files} monthly files, ${a.legacy_processed.first} to ${a.legacy_processed.last}${
                  a.legacy_processed.missing_months?.length ? `, missing ${a.legacy_processed.missing_months.join(", ")}` : ", no gaps"
                }. Reproduces runs made before 2026-09-12.`
              : "not present."}
          </li>
          <li>
            Trading calendar:{" "}
            {a.calendar?.validated
              ? `computed NYSE calendar, checked against the cached SPX price series: all ${a.calendar.sessions_compared?.toLocaleString()} sessions from ${a.calendar.from} to ${a.calendar.to} agree. Days after ${a.calendar.to} come from the same rules and are not independently checked.`
              : `NOT VALIDATED. ${a.calendar?.reason ?? ""} Calendar-only days: ${a.calendar?.calendar_only?.join(", ") || "none"}; price-only days: ${a.calendar?.prices_only?.join(", ") || "none"}. Treat the missing and closed-day lists above with suspicion.`}
          </li>
        </ul>
      </Section>
    </div>
  );
}
