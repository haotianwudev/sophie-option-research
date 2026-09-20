import type { ReactNode } from "react";
import { fmt } from "@/lib/format";
import type { Scale } from "@/lib/scale";

/** Colour key for a metric scale, plus the two cell states that are NOT on the scale. */
export function ScaleLegend({ scale, metric, showCellStates }: { scale: Scale; metric: string; showCellStates?: boolean }) {
  const [lo, hi] = scale.domain;
  return (
    <div className="flex flex-wrap items-center gap-x-6 gap-y-2 text-xs text-ink2">
      <div className="flex items-center gap-2">
        <span className="tnum">{fmt(metric, lo)}</span>
        <span
          aria-hidden
          className="h-2.5 w-44 rounded-full border border-hair"
          style={{ background: scale.gradient }}
        />
        <span className="tnum">{fmt(metric, hi)}</span>
        {scale.center != null && (
          <span className="text-muted">· centred on {fmt(metric, scale.center)}</span>
        )}
      </div>
      {showCellStates && (
        <>
          <Key
            swatch={
              <span
                aria-hidden
                className="inline-block h-3.5 w-3.5 rounded-[3px] border border-dashed border-muted"
              />
            }
          >
            not run
          </Key>
          <Key swatch={<span aria-hidden className="text-[11px] font-semibold text-ink">≠</span>}>
            several runs disagree
          </Key>
        </>
      )}
    </div>
  );
}

function Key({ swatch, children }: { swatch: ReactNode; children: ReactNode }) {
  return (
    <span className="flex items-center gap-1.5">
      {swatch}
      {children}
    </span>
  );
}
