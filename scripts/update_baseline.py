"""Re-run the `baseline` study (notebook 03's five configs) with the window extended to the latest
available chain data, and save the runs under tag `baseline`.

    PYTHONPATH=src ./.venv/Scripts/python.exe scripts/update_baseline.py            # run
    PYTHONPATH=src ./.venv/Scripts/python.exe scripts/update_baseline.py --dry-run  # show the plan only
    PYTHONPATH=src ./.venv/Scripts/python.exe scripts/update_baseline.py --start 2022-01-01

Why a script and not the notebook: notebook 03 hard-codes END = 2023-12-31, and the only chain
source that reaches past 2023 is `unified`, so the new runs are pinned to it explicitly. A run over a
different window is a different *study* in the viewer (a study is strategy + tag + window), so this
adds a new comparable set next to the 2016..2023-12-31 one instead of overwriting anything.

Safe to re-run: a config whose hash is already in the store is skipped. Each finished run is checked for
silent truncation (trades stopping well before the window ends), which is the failure mode a stale
price series or a data gap would produce without raising.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from lab.backtest import StrategyConfig, UNIFIED_CHAIN_DIR, run_backtest  # noqa: E402
from lab.experiments import load_runs, save_run  # noqa: E402

TAG = "baseline"
DEFAULT_START = "2016-01-01"   # notebook 03 uses 2016; an earlier set used 2022-01-01 (--start)
DATA_SOURCE = "unified"

# notebook 03's variants, verbatim
FILTERS = {
    "vix_rank>0.5": "vix_rank > 0.5",
    "vix_rank>0.8": "vix_rank > 0.8",
    "rsi<40 & vix_rank>0.5": "vix_rank > 0.5 and rsi14 < 40",
}


def latest_chain_date() -> str:
    """Newest day directory in the unified archive (directory names only, no parquet read)."""
    days = []
    for p in Path(UNIFIED_CHAIN_DIR).glob("year=*/month=*/day=*"):
        y, m, d = (int(part.split("=")[1]) for part in p.parts[-3:])
        days.append(f"{y:04d}-{m:02d}-{d:02d}")
    if not days:
        raise SystemExit(f"no chain days under {UNIFIED_CHAIN_DIR}")
    return max(days)


def plan(start: str, end: str) -> list[StrategyConfig]:
    base = StrategyConfig.from_yaml(ROOT / "configs" / "short_put_45dte.yaml").replace(
        start=start, end=end, data_source=DATA_SOURCE)
    out = [base]
    out += [base.replace(name=f"short_put|{label}", entry_filter=expr) for label, expr in FILTERS.items()]
    out.append(StrategyConfig.from_yaml(ROOT / "configs" / "iron_condor_45dte.yaml").replace(
        start=start, end=end, data_source=DATA_SOURCE))
    return out


def truncation_check(res, start: str, end: str) -> list[str]:
    """Flag a run whose trades stop suspiciously early or leave a whole year empty."""
    t = res.trade_log
    if t is None or t.empty:
        return ["no trades at all"]
    warns = []
    last_exit = pd.to_datetime(t["exit_date"]).max()
    gap = (pd.Timestamp(end) - last_exit).days
    if gap > 75:  # 45-DTE entries + a 21-DTE exit means the tail can legitimately trail by ~2 months
        warns.append(f"last exit {last_exit.date()} is {gap} days before the window end {end}")
    per_year = pd.to_datetime(t["entry_date"]).dt.year.value_counts()
    full_years = range(int(start[:4]), int(end[:4]))
    empty = [y for y in full_years if per_year.get(y, 0) == 0]
    if empty:
        warns.append(f"no entries in {empty}")
    return warns


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--start", default=DEFAULT_START, help="window start; each start is its own study")
    args = ap.parse_args()

    end = latest_chain_date()
    start = args.start
    configs = plan(start, end)
    existing = set(load_runs(TAG)["config_hash"]) if not load_runs(TAG).empty else set()
    print(f"window {start} .. {end}   source={DATA_SOURCE}   tag={TAG}", flush=True)
    for c in configs:
        state = "already in store, will skip" if c.hash() in existing else "to run"
        print(f"  {c.hash()}  {c.name:32s} {state}", flush=True)
    if args.dry_run:
        return 0

    failures = 0
    for c in configs:
        if c.hash() in existing:
            continue
        t0 = time.time()
        print(f"\n>> {c.name} ...", flush=True)
        try:
            res = run_backtest(c)
        except Exception as e:  # keep going: one bad config should not lose the others
            failures += 1
            print(f"   FAILED: {type(e).__name__}: {e}", flush=True)
            continue
        save_run(res, tag=TAG)
        m = res.metrics
        warns = truncation_check(res, start, end)
        print(f"   saved {res.config_hash} in {time.time() - t0:.0f}s | trades {int(m.get('total_trades', 0))} "
              f"| sharpe {m.get('sharpe_ratio', float('nan')):.2f} | win {m.get('win_rate', float('nan')):.0%}", flush=True)
        for w in warns:
            print(f"   WARNING: {w}", flush=True)
    print(f"\ndone, {failures} failed", flush=True)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
