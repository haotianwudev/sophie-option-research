import type { Availability } from "@/lib/availability-types";

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/**
 * Year x month coverage of trading sessions. Three explicit states, not a shade: a colour scale over
 * 0-100% would make a month with one missing session look identical to a complete one, which is the one
 * thing this view exists to show.
 *   complete  - tinted, plain
 *   missing   - amber with an icon and the count ("▲ 2"), never colour alone
 *   none      - dashed outline (files expected, none present)
 * Months with no expected sessions (before the archive starts, or still in the future) are left blank.
 */
export function CoverageMatrix({ months }: { months: NonNullable<Availability["months"]> }) {
  const years = [...new Set(months.map((m) => m.year))].sort((a, b) => a - b);
  const by = new Map(months.map((m) => [`${m.year}-${m.month}`, m]));

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-x-5 gap-y-1 text-xs text-ink2">
        <span className="flex items-center gap-1.5">
          <span aria-hidden className="inline-block h-3.5 w-3.5 rounded-[3px]" style={{ background: "color-mix(in srgb, var(--accent) 35%, var(--surface))" }} />
          every session present
        </span>
        <span className="flex items-center gap-1.5">
          <span className="warn">▲ n</span> n sessions missing
        </span>
        <span className="flex items-center gap-1.5">
          <span aria-hidden className="inline-block h-3.5 w-3.5 rounded-[3px] border border-dashed border-muted" />
          no files at all
        </span>
      </div>
      <div className="overflow-x-auto">
        <div className="grid gap-[2px]" style={{ gridTemplateColumns: `max-content repeat(12, minmax(46px, 1fr))` }} role="grid" aria-label="Chain file coverage by year and month">
          <div />
          {MONTHS.map((m) => (
            <div key={m} className="pb-1 text-center text-xs font-semibold text-ink2">{m}</div>
          ))}
          {years.map((y) => (
            <div key={y} className="contents">
              <div className="tnum flex items-center justify-end pr-2 text-xs font-semibold text-ink2">{y}</div>
              {MONTHS.map((_, i) => {
                const c = by.get(`${y}-${i + 1}`);
                if (!c || c.expected === 0) return <div key={i} />;
                const missing = c.expected - c.present;
                const label = `${MONTHS[i]} ${y}: ${c.present} of ${c.expected} sessions present`;
                if (c.present === 0)
                  return (
                    <div key={i} role="gridcell" title={label} aria-label={label}
                      className="flex h-8 items-center justify-center rounded-[4px] border border-dashed border-muted text-[11px] text-ink2">0</div>
                  );
                if (missing > 0)
                  return (
                    <div key={i} role="gridcell" title={label} aria-label={label}
                      className="warn flex h-8 items-center justify-center !rounded-[4px] !px-0 text-[11px]">▲ {missing}</div>
                  );
                return (
                  <div key={i} role="gridcell" title={label} aria-label={label} className="h-8 rounded-[4px]"
                    style={{ background: "color-mix(in srgb, var(--accent) 35%, var(--surface))" }} />
                );
              })}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
