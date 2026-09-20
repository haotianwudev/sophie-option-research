"""Refresh the stored research studies (sweep04, mgmt04, optuna04, vrp09, wf_oos) to the newest chain date.

    PYTHONPATH=src ./.venv/Scripts/python.exe scripts/update_studies.py --dry-run
    PYTHONPATH=src ./.venv/Scripts/python.exe scripts/update_studies.py
    PYTHONPATH=src ./.venv/Scripts/python.exe scripts/update_studies.py --only sweep04,mgmt04

What "refresh" means here -- and why it replays the STORED configs rather than the notebooks. The notebooks and
the store disagree (the stored sweep04 has no delta 0.10, mgmt04 and vrp09 use shorter windows, wf_oos covers
2014-2023), so this takes each stored run's own name, params, entry filter and start date and changes only
the end date (to the newest chain) and the data source (unified, the only source past 2023). Each becomes a
new study in the viewer (a study is strategy + tag + window) beside the original; nothing is overwritten.

  sweep04 / mgmt04 / vrp09 : the same grid cells / filters, new window.
  optuna04                 : the SAME trial parameter sets re-evaluated on the longer window. This is not a new
                             search (a fresh TPE search would land on a different optimum and would replace
                             the old result instead of updating it).
  wf_oos                   : two more out-of-sample years (2024, 2025), tuned on the four years before each. 2026 is
                             a partial year and is left out, so the study stays a set of full-year windows.

Not refreshed, because they are not in the results store: the rolling/sizing studies (notebook 08, in-notebook
only) and ML meta-labeling (notebook 06, no stored runs).

Chains are loaded once per window and shared between runs (a run drops from ~50 s to ~3 s). Idempotent: a config
whose hash is already stored is skipped. Each result is checked for silent truncation.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from lab.backtest import StrategyConfig, load_chains, load_features, run_backtest  # noqa: E402
from lab.experiments import save_run, walk_forward  # noqa: E402
from update_baseline import latest_chain_date, truncation_check  # noqa: E402

REPLAY_TAGS = ["sweep04", "mgmt04", "optuna04", "vrp09"]
SIM_DEFAULT = {"capital": 100000, "quantity": 1, "max_positions": 1}   # every stored run was proven to use this
WF_GRID = {"leg1_delta.target": [0.10, 0.16, 0.30], "take_profit": [0.25, 0.5, None]}   # notebook 05
WF_FIRST_YEAR, WF_LAST_YEAR = 2020, 2025   # tests 2024 and 2025 (train 2020-23, 2021-24)


def _none(v):
    return None if (v is None or v == "" or (isinstance(v, float) and math.isnan(v))) else v


def replay_config(row: pd.Series, end: str) -> StrategyConfig:
    return StrategyConfig(
        name=row["name"], strategy=row["strategy"], params=json.loads(row["params_json"]),
        entry_filter=_none(row["entry_filter"]), start=_none(row["start"]), end=end,
        sim=dict(SIM_DEFAULT), data_source="unified")


def plan(runs: pd.DataFrame, end: str, only: set[str]) -> list[tuple[str, StrategyConfig, bool]]:
    """(tag, config, already_stored) for every stored run of the replayed tags that has not been extended yet."""
    have = set(runs["config_hash"])
    out = []
    src = runs[runs["tag"].isin(only) & (runs["end"] != end)]
    for _, row in src.sort_values(["tag", "start", "run_at"]).iterrows():
        cfg = replay_config(row, end)
        out.append((row["tag"], cfg, cfg.hash() in have))
    return out


def run_group(label: str, items: list[tuple[str, StrategyConfig]], start: str, end: str, chain_features: bool,
              workers: int) -> list[tuple[str, object]]:
    print(f"\n=== {label}: {len(items)} runs, window {start} .. {end} ===", flush=True)
    t0 = time.time()
    chains = load_chains(start, end, source="unified")
    if chain_features:
        # vrp09's filters (atm_iv_rank ...) need chain-native features, exactly as notebook 09 builds them
        from lab.features import full_features
        from lab.market_data import load_market
        feats = full_features(load_market(), chains)
    else:
        feats = load_features()
    print(f"    loaded {len(chains):,} chain rows in {time.time() - t0:.0f}s", flush=True)

    def one(item):
        tag, cfg = item
        try:
            return tag, cfg, run_backtest(cfg, chains=chains, features=feats), None
        except Exception as e:  # a config that yields no trades must not lose the rest
            return tag, cfg, None, f"{type(e).__name__}: {e}"

    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(one, items))
    del chains
    return results


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--only", default=",".join(REPLAY_TAGS + ["wf_oos"]), help="comma-separated tags")
    ap.add_argument("--workers", type=int, default=4)
    args = ap.parse_args()
    only = {t.strip() for t in args.only.split(",") if t.strip()}

    end = latest_chain_date()
    runs = pd.read_parquet(ROOT / "results" / "runs.parquet")
    todo = plan(runs, end, only & set(REPLAY_TAGS))
    print(f"end date {end}, source unified", flush=True)
    summary = pd.DataFrame([(t, c.start, s) for t, c, s in todo], columns=["tag", "start", "stored"])
    if len(summary):
        print(summary.groupby(["tag", "start"]).agg(configs=("stored", "size"), already_done=("stored", "sum")).to_string(), flush=True)

    wf_done = set(runs.loc[runs["tag"] == "wf_oos", "start"])
    wf_needed = "wf_oos" in only and not {"2024-01-01", "2025-01-01"} <= wf_done
    print(f"wf_oos: {'extend to 2024 and 2025' if wf_needed else 'nothing to do'}", flush=True)
    if args.dry_run:
        return 0

    failures, saved = [], 0
    pending = [(t, c) for t, c, stored in todo if not stored]
    # group by start date (one chain load each); vrp09 gets its own group because it needs chain features
    groups: dict[tuple[str, bool], list] = {}
    for t, c in pending:
        groups.setdefault((c.start, t == "vrp09"), []).append((t, c))
    for (start, chain_features), items in sorted(groups.items()):
        label = ",".join(sorted({t for t, _ in items})) + (" (chain features)" if chain_features else "")
        for tag, cfg, res, err in run_group(label, items, start, end, chain_features, args.workers):
            if err:
                failures.append((tag, cfg.name, err))
                print(f"    FAILED {tag} {cfg.name[:60]}: {err}", flush=True)
                continue
            save_run(res, tag=tag)
            saved += 1
            warns = truncation_check(res, start or "2016-01-01", end)
            m = res.metrics
            print(f"    saved {tag:8s} {res.config_hash} trades {int(m.get('total_trades', 0)):4d} "
                  f"sharpe {m.get('sharpe_ratio', float('nan')):5.2f}  {cfg.name[:52]}"
                  + (f"   WARN {'; '.join(warns)}" if warns else ""), flush=True)

    if wf_needed:
        print(f"\n=== wf_oos: walk-forward, tests 2024 and 2025 ===", flush=True)
        from lab.backtest import StrategyConfig as SC
        base = SC.from_yaml(ROOT / "configs" / "short_put_45dte.yaml")
        t0 = time.time()
        wf = walk_forward(base, WF_GRID, train_years=4, test_years=1, first_year=WF_FIRST_YEAR, last_year=WF_LAST_YEAR)
        print(f"    done in {time.time() - t0:.0f}s", flush=True)
        print(wf.windows.round(3).to_string(index=False), flush=True)

    print(f"\nsaved {saved} runs, {len(failures)} failed", flush=True)
    for f in failures:
        print("  failed:", f, flush=True)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
