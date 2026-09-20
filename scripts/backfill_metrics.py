"""Backfill the extended metric columns on runs saved before lab/metrics.py existed.

Thirteen metrics -- premium_capture through max_dd_days, including
probabilistic_sharpe, which is the platform's own "is this edge established" gate --
are NaN on the runs written in July 2026 and populated on those from September 2026.
Comparing the two vintages on a headline metric is impossible while that holds.

Every one of them is recomputable from data already on disk: compute_run_metrics needs
only a trade log, an equity curve and the daily feature matrix, and results/trades/
{hash}.parquet carries per-trade equity in its own `equity` column. So this is a
recompute, not a re-run: no chain data is read and no backtest is simulated. (optopsy does
get imported -- lab.market_data.load_market -> lab.db -> lab.backtest -- but nothing here
calls it. load_market also reads the Sophie Postgres `prices` table when reachable, so the
features used here may differ very slightly from the cached Yahoo series the July runs saw;
see ATOL below.)

Read-only by default. --write backs up runs.parquet first, and refuses to write at all
unless the self-check passes: rows that already hold these metrics are recomputed and
must reproduce their stored values. If they don't, this script's reconstruction of the
equity curve disagrees with what the backtester originally produced, and filling the
NaNs would be writing plausible-looking numbers of unknown provenance.

Usage
    PYTHONPATH=src ./.venv/Scripts/python.exe scripts/backfill_metrics.py
    PYTHONPATH=src ./.venv/Scripts/python.exe scripts/backfill_metrics.py --write
"""

from __future__ import annotations

import argparse
import shutil
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from lab import RESULTS_DIR  # noqa: E402
from lab.features import build_features  # noqa: E402
from lab.market_data import load_market  # noqa: E402
from lab.metrics import compute_run_metrics  # noqa: E402

RUNS_PATH = RESULTS_DIR / "runs.parquet"
TRADES_DIR = RESULTS_DIR / "trades"

# The columns compute_run_metrics owns. Optopsy's own summary columns are never touched.
EXTENDED = [
    "premium_capture",
    "worst_trade_over_avg_credit",
    "pnl_per_day_in_trade",
    "exposure",
    "avg_return_on_margin",
    "ann_return_on_margin",
    "trade_pnl_tstat",
    "trade_pnl_skew",
    "trade_pnl_kurtosis",
    "cagr",
    "ann_vol",
    "probabilistic_sharpe",
    "max_dd_days",
]

# A recompute of the same inputs through the same code should be near bit-comparable, so
# the relative tolerance is tight. The absolute floor exists for one metric:
# avg_return_on_margin is the mean of per-trade returns of size ~1e-1 that nearly cancel
# to ~1e-4, so a 1e-9 discrepancy in a single input reads as ~1e-5 relative on the mean
# even though it is ~1e-8 of the terms that produced it (seen on vrp09 run 925ad2cc7eca,
# a July 2026 row: abs diff 2e-9, while its sibling ann_return_on_margin matched to 6e-9
# relative). 1e-7 absolute is far below any real disagreement for every other metric here,
# which all sit at 1e-2 or larger.
RTOL = 1e-6
ATOL = 1e-7


def recompute(config_hash: str, features: pd.DataFrame) -> dict | None:
    """Recompute the extended metrics for one run from its stored trade log."""
    path = TRADES_DIR / f"{config_hash}.parquet"
    if not path.exists():
        return None
    trades = pd.read_parquet(path)
    if trades.empty:
        return None
    # The same reconstruction lab/db.py:131 uses when publishing a run's equity curve.
    equity = trades.set_index("exit_date")["equity"]
    return compute_run_metrics(trades, equity, features=features)


def self_check(runs: pd.DataFrame, features: pd.DataFrame) -> tuple[bool, pd.DataFrame]:
    """Recompute rows that already have these metrics and compare to what's stored.

    This is the whole basis for trusting the backfill: the rows from September 2026 were
    written by the backtester itself, so reproducing them proves the reconstruction here
    matches what run_backtest did.
    """
    done = runs[runs["premium_capture"].notna()]
    rows = []
    for _, row in done.iterrows():
        fresh = recompute(row["config_hash"], features)
        if fresh is None:
            rows.append({"config_hash": row["config_hash"], "metric": "(no trade log)",
                         "stored": np.nan, "fresh": np.nan, "match": False})
            continue
        for col in EXTENDED:
            stored, new = row.get(col), fresh.get(col)
            if pd.isna(stored) and new is None:
                continue
            if pd.isna(stored) or new is None:
                rows.append({"config_hash": row["config_hash"], "metric": col,
                             "stored": stored, "fresh": new, "match": False})
                continue
            ok = bool(np.isclose(float(stored), float(new), rtol=RTOL, atol=ATOL))
            rows.append({"config_hash": row["config_hash"], "metric": col,
                         "stored": float(stored), "fresh": float(new), "match": ok})
    checked = pd.DataFrame(rows)
    passed = bool(len(checked)) and bool(checked["match"].all())
    return passed, checked


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--write", action="store_true",
                    help="commit the backfill (default is a dry run)")
    args = ap.parse_args()

    if not RUNS_PATH.exists():
        print(f"no results store at {RUNS_PATH}")
        return 1

    runs = pd.read_parquet(RUNS_PATH)
    missing_cols = [c for c in EXTENDED if c not in runs.columns]
    if missing_cols:
        print(f"store is missing expected columns: {missing_cols}")
        return 1

    stale = runs["premium_capture"].isna()
    print(f"store:  {len(runs)} runs, {int(stale.sum())} missing extended metrics")
    if not stale.any():
        print("nothing to backfill.")
        return 0

    print("features: building from cached market data ...")
    features = build_features(load_market())

    print(f"\nself-check: recomputing {int((~stale).sum())} rows that already have values")
    passed, checked = self_check(runs, features)
    if len(checked):
        worst = checked[~checked["match"]]
        print(f"  {len(checked)} comparisons, {len(worst)} mismatched")
        if len(worst):
            print(worst.head(20).to_string(index=False))
    if not passed:
        print("\nSELF-CHECK FAILED — refusing to write.")
        print("The recomputation does not reproduce values the backtester itself wrote,")
        print("so filling the NaN rows would invent numbers. Investigate before retrying.")
        return 1
    print("  self-check passed: recomputation reproduces stored values within tolerance")

    filled, skipped, unexpected = {}, [], set()
    for idx, row in runs[stale].iterrows():
        fresh = recompute(row["config_hash"], features)
        if fresh is None:
            skipped.append(row["config_hash"])
            continue
        unexpected |= set(fresh) - set(EXTENDED)
        filled[idx] = {k: v for k, v in fresh.items() if k in EXTENDED}

    print(f"\nbackfill: {len(filled)} runs recomputed, {len(skipped)} skipped")
    if skipped:
        print(f"  no trade log for: {', '.join(skipped)}")
    if unexpected:
        print(f"  note: metrics returned but not written (not store columns): {sorted(unexpected)}")

    preview = pd.DataFrame(
        [{"config_hash": runs.at[i, "config_hash"], "tag": runs.at[i, "tag"],
          **{c: vals.get(c) for c in ("premium_capture", "cagr", "probabilistic_sharpe", "max_dd_days")}}
         for i, vals in list(filled.items())[:10]]
    )
    print("\nfirst 10 rows to be written:")
    print(preview.to_string(index=False))

    if not args.write:
        print("\ndry run — nothing written. Re-run with --write to commit.")
        return 0

    backup = RUNS_PATH.with_suffix(f".parquet.bak-{date.today().isoformat()}")
    shutil.copy2(RUNS_PATH, backup)
    print(f"\nbacked up store to {backup.name}")

    for idx, vals in filled.items():
        for col, val in vals.items():
            runs.at[idx, col] = val
    runs.to_parquet(RUNS_PATH, index=False)

    after = pd.read_parquet(RUNS_PATH)
    print(f"wrote {RUNS_PATH.name}: {len(after)} rows, "
          f"{int(after['premium_capture'].isna().sum())} still missing premium_capture")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
