"""Read model over the local results store -- what the viewer API serves.

Reads results/runs.parquet and results/trades/{hash}.parquet with plain pandas. It never
imports optopsy (lab.backtest does, at module level), so the browse path stays fast and
keeps working when the engine is broken. The one deliberate exception is the study memo,
which lives in memo.py behind a lazy import.

Everything here is derived from the store as it exists, not from a schema we wish it had.
The store has real quirks, and this module is where they get absorbed so the UI never sees
them:

- Strategy params are a JSON blob (`params_json`), not columns. They are parsed per row
  and flattened to dotted keys (`leg1_delta.target`), the same convention grid_sweep uses.
- `leg1_delta.min/max` are band edges that grid_sweep shifts together with `.target`
  (constant +-0.10 for short puts, checked on the store), so they are derived, never an
  axis. They stay visible in a run's full params.
- `sim` and `data_source` are hash inputs that are NOT persisted, but they are recoverable:
  config_hash is sha256 over the config dict (see StrategyConfig.hash), and every stored field
  except those two is on the row. Re-hashing the row under each candidate (no data_source key =
  written before the field existed, 2026-09-12, when the legacy OptionsDX conversion was the only
  chain source; "unified"; "legacy") with the default sim, and matching config_hash, proves both.
  All 107 stored runs match, so every run's sim is the default and its source is known. Anything
  that does not match reports "unknown" rather than a guess. This deliberately mirrors
  lab.backtest's hashing without importing it (that pulls in optopsy); scripts/check_api.py
  asserts full coverage, so drift in either algorithm is caught.
- Identical params can appear under two hashes with different results. A grid cell that holds
  several runs is reported as such and flagged when they disagree; it is never silently
  resolved. (For the five such pairs in the store the difference is the chain data source.)
- The metric column set is open: discovered from dtypes, not hardcoded.
- NaN is not valid JSON. Everything leaving this module has been through `clean()`.

A "study" is (strategy, tag, window): the unit inside which runs are comparable. Tags mix
windows (optuna04 holds 36 runs on 2016-2023 and 4 on 2022-06..2023-12), and colouring
runs from different market periods in one grid would compare unlike things. The exception
is walk-forward, where the window IS the varying dimension.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Optional

import numpy as np
import pandas as pd

from lab import RESULTS_DIR

RUNS_PATH = RESULTS_DIR / "runs.parquet"
TRADES_DIR = RESULTS_DIR / "trades"

META_COLS = {"config_hash", "name", "strategy", "entry_filter", "start", "end",
             "params_json", "tag", "run_at"}
# Columns this module adds to the runs frame; never mistaken for metrics.
DERIVED_COLS = {"window", "study_window", "params", "data_source", "provenance"}

WALK_FORWARD = "walk-forward"
DISCRETE_MAX_LEVELS = 8       # more distinct values than this is a continuous param
ROUND_DECIMALS = 3            # a value that survives round(v, 3) looks like a chosen grid value
DEFAULT_METRIC = "sharpe_ratio"
MIN_TRADES = 30               # explain.py's threshold for "treat as anecdotal"
PSR_ESTABLISHED = 0.9         # README: probabilistic Sharpe below this => edge not established
WF_MIN_RUNS = 3

NOT_RECORDED = ["sim", "data_source"]     # not stored on the row; see _derive_provenance
SIM_DEFAULT = {"capital": 100000, "quantity": 1, "max_positions": 1}   # configs/*.yaml sim block

# Metrics a picker should lead with; the rest are discovered.
HEADLINE_METRICS = [
    "total_trades", "win_rate", "premium_capture", "sharpe_ratio", "probabilistic_sharpe",
    "sortino_ratio", "max_drawdown", "pnl_per_day_in_trade", "worst_trade_over_avg_credit",
    "cagr",
]


class NotFound(LookupError):
    """A hash, study or column that is not in the store."""


class Conflict(RuntimeError):
    """A change that cannot be made in the current state (e.g. the store changed underneath us)."""


class BadRequest(ValueError):
    """A request that names an axis, metric or value the study does not have."""


# ---------------------------------------------------------------------------
# JSON hygiene
# ---------------------------------------------------------------------------

def clean(v: Any) -> Any:
    """Make a value JSON-safe: NaN/inf -> None, numpy scalars -> python, recursively."""
    if v is None:
        return None
    if isinstance(v, dict):
        return {str(k): clean(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [clean(x) for x in v]
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (np.floating, float)):
        f = float(v)
        return None if (math.isnan(f) or math.isinf(f)) else f
    if isinstance(v, (np.bool_,)):
        return bool(v)
    if isinstance(v, (pd.Timestamp, datetime, date)):
        return v.isoformat()
    if v is pd.NaT:
        return None
    return v


def label(v: Any) -> str:
    """Stable string form of a param level. A missing take_profit/stop_loss is 'none'."""
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "none"
    if isinstance(v, (float, np.floating)):
        return format(float(v), "g")
    return str(v)


# ---------------------------------------------------------------------------
# Loading (cached on the store file's mtime, so a notebook can write while we serve)
# ---------------------------------------------------------------------------

@dataclass
class Store:
    runs: pd.DataFrame          # one row per run, params flattened into P, study keys added
    P: pd.DataFrame             # axis-eligible params (band edges dropped) + entry_filter
    metric_cols: list[str]
    mtime_ns: int
    key: tuple = (0, 0)      # (runs.parquet mtime, removed_runs.json mtime): the cache is valid while it holds
    n_hidden: int = 0        # runs hidden by removal


_cache: Optional[Store] = None

# Runs the user removed from the viewer. A sidecar file, so runs.parquet is not touched by a soft remove.
REMOVED_PATH = RESULTS_DIR / "removed_runs.json"


def read_tombstones() -> list[dict]:
    """The removed-run records. A missing file means none; an unreadable one is an error, never 'none' --
    treating a corrupt file as empty would quietly un-hide every removed run."""
    try:
        return json.loads(REMOVED_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []


def invalidate() -> None:
    global _cache
    _cache = None


def _store_key() -> tuple:
    return (RUNS_PATH.stat().st_mtime_ns, REMOVED_PATH.stat().st_mtime_ns if REMOVED_PATH.exists() else 0)


def _flatten(params_json: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in json.loads(params_json or "{}").items():
        if isinstance(v, dict):
            for kk, vv in v.items():
                out[f"{k}.{kk}"] = vv
        else:
            out[k] = v
    return out


def _config_hash(d: dict) -> str:
    """Same algorithm as StrategyConfig.hash() in lab/backtest.py."""
    return hashlib.sha256(json.dumps(d, sort_keys=True, default=str).encode()).hexdigest()[:12]


def _none(v: Any) -> Any:
    """'' / NaN / None -> None. (`v or None` is wrong here: a missing string is NaN, and NaN is truthy.)"""
    return None if (v is None or v == "" or (isinstance(v, float) and math.isnan(v))) else v


def _derive_provenance(row: pd.Series) -> tuple[Optional[str], str]:
    """Chain data source for a run, proven by re-hashing its config. (None, why) if it can't be."""
    base = {"name": row["name"], "strategy": row["strategy"], "params": json.loads(row["params_json"]),
            "entry_filter": _none(row["entry_filter"]), "start": _none(row["start"]),
            "end": _none(row["end"]), "sim": SIM_DEFAULT}
    for source, extra, how in (
        ("legacy", {}, "hash matches the config schema from before data_source existed (2026-09-12); "
                       "the legacy OptionsDX conversion was then the only chain source"),
        ("unified", {"data_source": "unified"}, 'hash matches data_source="unified"'),
        ("legacy", {"data_source": "legacy"}, 'hash matches data_source="legacy"'),
    ):
        if _config_hash({**base, **extra}) == row["config_hash"]:
            return source, how
    return None, "config hash does not match any known config schema with the default sim"


def _is_band_edge(key: str) -> bool:
    return key.endswith(".min") or key.endswith(".max")


def load() -> Store:
    global _cache
    if not RUNS_PATH.exists():
        raise NotFound(f"no results store at {RUNS_PATH}")
    mtime = RUNS_PATH.stat().st_mtime_ns
    key = _store_key()
    if _cache is not None and _cache.key == key:
        return _cache

    runs = pd.read_parquet(RUNS_PATH)
    hidden = {t["hash"] for t in read_tombstones()}
    n_hidden = int(runs["config_hash"].isin(hidden).sum())
    runs = runs[~runs["config_hash"].isin(hidden)].reset_index(drop=True)   # index is rebuilt: P below is built from it
    for c in ("entry_filter", "start", "end"):
        runs[c] = runs[c].where(runs[c].notna() & (runs[c] != ""), None)
    runs["window"] = [f"{s or '..'}..{e or '..'}" if (s or e) else "all"
                      for s, e in zip(runs["start"], runs["end"])]

    flat = [_flatten(p) for p in runs["params_json"]]
    runs["params"] = flat
    P = pd.DataFrame([{k: v for k, v in f.items() if not _is_band_edge(k)} for f in flat],
                     index=runs.index)
    P["entry_filter"] = runs["entry_filter"]

    # walk-forward: a (strategy, tag) whose runs each have their own distinct window
    runs["study_window"] = runs["window"]
    for _, g in runs.groupby(["strategy", "tag"]):
        if len(g) >= WF_MIN_RUNS and g["window"].is_unique:
            runs.loc[g.index, "study_window"] = WALK_FORWARD

    prov = [_derive_provenance(r) for _, r in runs.iterrows()]
    runs["data_source"] = [p[0] for p in prov]
    runs["provenance"] = [p[1] for p in prov]

    numeric = [c for c in runs.columns
               if c not in META_COLS and c not in DERIVED_COLS
               and pd.api.types.is_numeric_dtype(runs[c])]
    _cache = Store(runs=runs, P=P, metric_cols=numeric, mtime_ns=mtime, key=key, n_hidden=n_hidden)
    return _cache


def _row(s: Store, h: str) -> pd.Series:
    m = s.runs[s.runs["config_hash"] == h]
    if m.empty:
        raise NotFound(f"no run {h}")
    return m.sort_values("run_at").iloc[-1]


# ---------------------------------------------------------------------------
# Param space + study classification
# ---------------------------------------------------------------------------

def _level_key(v: Any):
    """Sort key: numbers ascending, missing ('none') last, strings alphabetical."""
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return (2, 0.0, "")
    if isinstance(v, (int, float, np.integer, np.floating)):
        return (0, float(v), "")
    return (1, 0.0, str(v))


def _param_info(col: pd.Series, key: str) -> dict[str, Any]:
    vals = [None if (isinstance(v, float) and math.isnan(v)) else v for v in col.tolist()]
    levels = sorted({(v.item() if isinstance(v, np.generic) else v) for v in vals}, key=_level_key)
    counts = {label(l): sum(1 for v in vals if label(v) == label(l)) for l in levels}
    non_null = [l for l in levels if l is not None]
    if key == "entry_filter" or any(isinstance(l, str) for l in non_null):
        kind = "categorical"
    elif len(non_null) > DISCRETE_MAX_LEVELS or any(round(float(l), ROUND_DECIMALS) != float(l)
                                                    for l in non_null):
        kind = "continuous"
    else:
        kind = "discrete"
    return {"key": key, "kind": kind, "n_levels": len(levels),
            "levels": levels, "labels": [label(l) for l in levels], "counts": counts}


def _varying(idx: pd.Index, s: Store) -> list[dict[str, Any]]:
    out = []
    for key in s.P.columns:
        col = s.P.loc[idx, key]
        if col.isna().all():
            continue
        info = _param_info(col, key)
        if info["n_levels"] > 1:
            out.append(info)
    return out


def _classify(varying: list[dict], walk_forward: bool) -> str:
    if walk_forward:
        return "walk_forward"
    if not varying:
        return "single"
    numeric = [v for v in varying if v["kind"] != "categorical"]
    if not numeric:
        return "filter_set"
    if len(numeric) == 1:
        return "line"
    return "scatter" if any(v["kind"] == "continuous" for v in numeric) else "grid"


def _study_idx(s: Store, strategy: str, tag: str, window: str) -> pd.Index:
    r = s.runs
    m = r[(r["strategy"] == strategy) & (r["tag"] == tag) & (r["study_window"] == window)]
    if m.empty:
        raise NotFound(f"no study {strategy}/{tag}/{window}")
    return m.index


def _flags(row: pd.Series) -> dict[str, Any]:
    tr, psr = row.get("total_trades"), row.get("probabilistic_sharpe")
    return {
        "anecdotal": bool(pd.notna(tr) and tr < MIN_TRADES),
        "edge_established": (None if pd.isna(psr) else bool(psr >= PSR_ESTABLISHED)),
    }


def _best(s: Store, idx: pd.Index, metric: str = DEFAULT_METRIC) -> Optional[dict]:
    if metric not in s.runs.columns:
        return None
    col = s.runs.loc[idx, metric].dropna()
    if col.empty:
        return None
    i = col.idxmax()
    return {"hash": s.runs.at[i, "config_hash"], "metric": metric, "value": float(col.loc[i])}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def health() -> dict[str, Any]:
    s = load()
    src = s.runs["data_source"].fillna("unknown").value_counts().to_dict()
    return clean({
        "store": str(RUNS_PATH), "n_runs": len(s.runs), "n_removed": s.n_hidden, "n_metrics": len(s.metric_cols),
        "data_sources": src,
        "mtime": datetime.fromtimestamp(s.mtime_ns / 1e9).isoformat(timespec="seconds"),
        "n_trade_logs": len(list(TRADES_DIR.glob("*.parquet"))) if TRADES_DIR.exists() else 0,
        "not_recorded_in_store": NOT_RECORDED,
    })


def strategies() -> list[dict[str, Any]]:
    s = load()
    out = []
    for strat, g in s.runs.groupby("strategy"):
        starts = [x for x in g["start"] if x]
        ends = [x for x in g["end"] if x]
        out.append({
            "id": strat, "n_runs": len(g),
            "n_studies": g[["tag", "study_window"]].drop_duplicates().shape[0],
            "tags": sorted(g["tag"].unique().tolist()),
            "date_span": [min(starts) if starts else None, max(ends) if ends else None],
        })
    return clean(sorted(out, key=lambda d: -d["n_runs"]))


def studies(strategy: str) -> list[dict[str, Any]]:
    s = load()
    g = s.runs[s.runs["strategy"] == strategy]
    if g.empty:
        raise NotFound(f"no strategy {strategy}")
    out = []
    for (tag, win), sub in g.groupby(["tag", "study_window"]):
        varying = _varying(sub.index, s)
        kind = _classify(varying, win == WALK_FORWARD)
        # cells holding >1 run of identical params: the "runs that disagree" cases
        dup = sub.duplicated(["entry_filter", "params_json", "window"], keep=False)
        wf = kind == "walk_forward"
        med = sub[DEFAULT_METRIC].dropna()
        out.append({
            "strategy": strategy, "tag": tag, "window": win, "n_runs": len(sub), "kind": kind,
            "varying": [{k: v[k] for k in ("key", "kind", "n_levels")} for v in varying],
            # Walk-forward runs are independent out-of-sample years: "the best year" is a
            # selection-bias summary, so those studies report the median instead.
            "best": None if wf else _best(s, sub.index),
            "median": ({"metric": DEFAULT_METRIC, "value": float(med.median())}
                       if wf and len(med) else None),
            "n_duplicate_runs": int(dup.sum()),
            "data_sources": sorted({d for d in sub["data_source"] if d}) or [None],
            "runs_from": sub["run_at"].min(), "runs_to": sub["run_at"].max(),
        })
    return clean(sorted(out, key=lambda d: (d["tag"], d["window"])))


def study_params(strategy: str, tag: str, window: str) -> dict[str, Any]:
    s = load()
    idx = _study_idx(s, strategy, tag, window)
    varying = _varying(idx, s)
    varying_keys = {v["key"] for v in varying}
    constant = {}
    for key in s.P.columns:
        if key in varying_keys or s.P.loc[idx, key].isna().all():
            continue
        constant[key] = clean(s.P.loc[idx, key].iloc[0])
    kind = _classify(varying, window == WALK_FORWARD)
    numeric = sorted((v for v in varying if v["kind"] != "categorical"),
                     key=lambda v: -v["n_levels"])
    return clean({
        "strategy": strategy, "tag": tag, "window": window, "n_runs": len(idx), "kind": kind,
        "varying": varying, "constant": constant,
        "default_axes": [v["key"] for v in numeric[:2]],
        "metrics": [m for m in HEADLINE_METRICS if m in s.metric_cols]
                   + [m for m in s.metric_cols if m not in HEADLINE_METRICS],
        "default_metric": DEFAULT_METRIC,
        "band_note": "leg*_delta.min/.max move with .target (fixed width) and are not axes.",
    })


def grid(strategy: str, tag: str, window: str, x: Optional[str] = None, y: Optional[str] = None,
         metric: str = DEFAULT_METRIC, fix: Optional[dict[str, str]] = None) -> dict[str, Any]:
    s = load()
    if metric not in s.metric_cols:
        raise BadRequest(f"unknown metric {metric!r}")
    idx = _study_idx(s, strategy, tag, window)
    varying = _varying(idx, s)
    by_key = {v["key"]: v for v in varying}
    kind = _classify(varying, window == WALK_FORWARD)
    if kind not in ("grid", "scatter", "line"):
        raise BadRequest(f"study kind {kind!r} has no param view")

    numeric = sorted((v for v in varying if v["kind"] != "categorical"),
                     key=lambda v: -v["n_levels"])
    x = x or numeric[0]["key"]
    if y is None and kind != "line":
        y = next(v["key"] for v in numeric if v["key"] != x)
    for ax in (x, y):
        if ax is not None and ax not in by_key:
            raise BadRequest(f"{ax!r} does not vary in this study "
                             f"(varies: {sorted(by_key)})")
    if x == y:
        raise BadRequest("x and y must differ")

    # Every non-axis param that still varies is pinned, so each cell means one thing.
    fixed: dict[str, str] = {}
    explicit = dict(fix or {})
    for key, want in explicit.items():
        if key not in by_key:
            raise BadRequest(f"cannot fix {key!r}: it does not vary in this study")
        if want not in by_key[key]["labels"]:
            raise BadRequest(f"{want!r} is not a level of {key!r} ({by_key[key]['labels']})")
        fixed[key] = want
    for v in varying:
        if v["key"] in (x, y) or v["key"] in fixed:
            continue
        # scatter plots the whole cloud: only pin params that are not part of the cloud
        if kind == "scatter" and v["kind"] in ("continuous", "categorical"):
            continue
        fixed[v["key"]] = max(v["labels"], key=lambda l, v=v: (v["counts"][l], -v["labels"].index(l)))

    keep = pd.Series(True, index=idx)
    for key, want in fixed.items():
        keep &= s.P.loc[idx, key].map(label) == want
    sub = s.runs.loc[idx[keep.values]]
    Psub = s.P.loc[sub.index]

    def ax_info(key: Optional[str]):
        if key is None:
            return None
        v = by_key[key]
        return {"key": key, "kind": v["kind"], "levels": v["levels"], "labels": v["labels"]}

    free = [{"key": v["key"], "kind": v["kind"], "labels": v["labels"], "counts": v["counts"]}
            for v in varying if v["key"] not in (x, y)]
    vals = sub[metric].dropna()
    out: dict[str, Any] = {
        "strategy": strategy, "tag": tag, "window": window, "kind": "scatter" if kind == "scatter" else "grid",
        "study_kind": kind, "metric": metric, "x": ax_info(x), "y": ax_info(y),
        "fixed": fixed, "free": free, "n_runs_shown": len(sub),
        "metric_range": [float(vals.min()), float(vals.max())] if len(vals) else [None, None],
    }

    if kind == "scatter":
        out["points"] = [{
            "hash": r["config_hash"], "x": clean(Psub.at[i, x]), "y": clean(Psub.at[i, y]),
            "value": clean(r[metric]), "total_trades": clean(r.get("total_trades")),
            "entry_filter": r["entry_filter"], "run_at": r["run_at"],
        } for i, r in sub.iterrows()]
        return clean(out)

    xs, ys = by_key[x]["labels"], (by_key[y]["labels"] if y else [None])
    cells = []
    for xl in xs:
        for yl in ys:
            m = (Psub[x].map(label) == xl)
            if y:
                m &= (Psub[y].map(label) == yl)
            hit = sub[m.values]
            if hit.empty:
                cells.append({"x": xl, "y": yl, "ran": False, "n_runs": 0, "hash": None,
                              "value": None, "conflict": False, "spread": None, "hashes": [],
                              "total_trades": None, "cell_params": None})
                continue
            newest = hit.sort_values("run_at").iloc[-1]
            mv = hit[metric].dropna()
            spread = float(mv.max() - mv.min()) if len(mv) > 1 else None
            cells.append({
                "x": xl, "y": yl, "ran": True, "n_runs": len(hit), "hash": newest["config_hash"],
                "value": clean(newest[metric]), "total_trades": clean(newest.get("total_trades")),
                "conflict": bool(spread is not None and spread > 1e-9), "spread": spread,
                "data_sources": sorted({d for d in hit["data_source"] if d}),
                "hashes": hit.sort_values("run_at")["config_hash"].tolist(),
            })
    out["cells"] = cells
    out["n_cells"] = len(cells)
    out["n_cells_run"] = sum(c["ran"] for c in cells)
    out["n_cells_conflict"] = sum(c["conflict"] for c in cells)
    return clean(out)


def runs(strategy: Optional[str] = None, tag: Optional[str] = None, sort: str = DEFAULT_METRIC,
         limit: int = 200, descending: bool = True) -> list[dict[str, Any]]:
    """`tag=None` means no filter; an explicit empty string is a real (empty) tag filter."""
    s = load()
    r = s.runs
    if strategy is not None:
        r = r[r["strategy"] == strategy]
    if tag is not None:
        r = r[r["tag"] == tag]
    if sort not in r.columns:
        raise BadRequest(f"cannot sort by {sort!r}")
    r = r.sort_values(sort, ascending=not descending, na_position="last").head(limit)
    return [_run_row(s, row) for _, row in r.iterrows()]


def _run_row(s: Store, row: pd.Series) -> dict[str, Any]:
    return clean({
        "hash": row["config_hash"], "name": row["name"], "strategy": row["strategy"],
        "tag": row["tag"], "window": row["window"], "study_window": row["study_window"],
        "entry_filter": row["entry_filter"], "run_at": row["run_at"],
        "params": {k: v for k, v in row["params"].items() if not _is_band_edge(k)},
        "data_source": row["data_source"], "provenance": row["provenance"],
        "sim": SIM_DEFAULT if row["data_source"] else None,
        "metrics": {m: row[m] for m in s.metric_cols},
        "flags": _flags(row),
    })


def run(h: str) -> dict[str, Any]:
    s = load()
    rows = s.runs[s.runs["config_hash"] == h]
    if rows.empty:
        raise NotFound(f"no run {h}")
    row = rows.sort_values("run_at").iloc[-1]
    same = s.runs[(s.runs["config_hash"] != h) & (s.runs["strategy"] == row["strategy"])
                  & (s.runs["window"] == row["window"])
                  & (s.runs["params_json"] == row["params_json"])
                  & (s.runs["entry_filter"].fillna("") == ("" if pd.isna(row["entry_filter"]) else row["entry_filter"]))]
    out = _run_row(s, row)
    out.update({
        "tags": sorted(rows["tag"].unique().tolist()),
        "params_full": row["params"],
        "has_trade_log": (TRADES_DIR / f"{h}.parquet").exists(),
        "not_recorded_in_store": NOT_RECORDED,
        "same_params_other_hashes": [
            {"hash": o["config_hash"], "run_at": o["run_at"], "data_source": o["data_source"],
             "total_trades": o.get("total_trades"), DEFAULT_METRIC: o.get(DEFAULT_METRIC)}
            for _, o in same.sort_values("run_at").iterrows()],
    })
    return clean(out)


# ---------------------------------------------------------------------------
# Trade-level data (read straight from the per-run parquet)
# ---------------------------------------------------------------------------

def _trades_df(h: str) -> pd.DataFrame:
    load()  # ensure the hash is a known run before touching disk
    if _row(load(), h) is None:
        raise NotFound(f"no run {h}")
    p = TRADES_DIR / f"{h}.parquet"
    if not p.exists():
        raise NotFound(f"run {h} has no trade log")
    return pd.read_parquet(p)


def _strike(desc: Any) -> Optional[float]:
    try:
        return float(str(desc).split()[-1])
    except (ValueError, IndexError):
        return None


def trades(h: str, offset: int = 0, limit: int = 100) -> dict[str, Any]:
    t = _trades_df(h)
    page = t.iloc[offset: offset + limit].copy()
    page["strike"] = page["description"].map(_strike)
    for c in ("entry_date", "exit_date", "expiration"):
        page[c] = pd.to_datetime(page[c]).dt.strftime("%Y-%m-%d")
    by_exit = (t.groupby("exit_type")["realized_pnl"]
                 .agg(n="count", total="sum", mean="mean").reset_index().to_dict("records"))
    return clean({"total": len(t), "offset": offset, "limit": limit,
                  "rows": page.to_dict("records"), "by_exit_type": by_exit})


def equity(h: str) -> dict[str, Any]:
    from lab.metrics import daily_equity   # numpy/pandas only -- no optopsy

    t = _trades_df(h)
    curve = t.set_index("exit_date")["equity"]
    daily = daily_equity(curve)
    dd = daily / daily.cummax() - 1.0
    # Starting capital is not stored, but the trade log carries equity and cumulative P&L, and
    # capital = equity - cumulative_pnl. Only claim it when it is genuinely constant.
    cap = (t["equity"] - t["cumulative_pnl"])
    inferred = float(cap.iloc[0]) if np.allclose(cap, cap.iloc[0], atol=0.01) else None
    return clean({
        "n_trades": len(t),
        "capital_inferred": inferred,
        "points": [{"date": d.strftime("%Y-%m-%d"), "equity": float(e), "drawdown": float(k)}
                   for d, e, k in zip(daily.index, daily.values, dd.values)],
        "note": (f"Starting capital ${inferred:,.0f}, inferred as equity minus cumulative P&L (it is not stored). "
                 if inferred is not None else "Starting capital is not stored and could not be inferred. ")
                + "The curve starts at the first closed trade.",
    })


# ---------------------------------------------------------------------------
# Regime breakdown (performance by feature quantile at entry)
# ---------------------------------------------------------------------------

_features: Optional[pd.DataFrame] = None


class FeaturesUnavailable(RuntimeError):
    """The cached market data the regime breakdown needs is not on disk."""


def _load_features() -> pd.DataFrame:
    """Daily feature matrix built from the CACHED market parquets only.

    Deliberately not lab.market_data.load_market(): that imports lab.db -> lab.backtest ->
    optopsy, tries the Sophie Postgres `prices` table first, and downloads from Yahoo and
    rewrites the cache when it is stale. A read-only local viewer should do none of that. The
    join below mirrors load_market (SPX bars + vix + optional vix3m); staleness is fine, since
    regime buckets only need the dates trades were entered on.
    """
    global _features
    if _features is None:
        from lab import MARKET_DIR
        from lab.features import build_features   # pandas/numpy only

        def cached(sym: str) -> pd.DataFrame:
            p = MARKET_DIR / f"{sym.lower()}_daily.parquet"
            if not p.exists():
                raise FeaturesUnavailable(f"no cached market data at {p}")
            return pd.read_parquet(p)

        market = cached("SPX").merge(
            cached("VIX")[["quote_date", "close"]].rename(columns={"close": "vix"}),
            on="quote_date", how="left")
        try:
            market = market.merge(
                cached("VIX3M")[["quote_date", "close"]].rename(columns={"close": "vix3m"}),
                on="quote_date", how="left")
        except FeaturesUnavailable:
            market["vix3m"] = float("nan")   # term structure is optional, as in load_market
        _features = build_features(market)
    return _features


def regimes(h: str, feature: str = "vix_rank", bins: int = 4) -> dict[str, Any]:
    t = _trades_df(h)
    f = _load_features()
    if feature not in f.columns or feature in ("quote_date",):
        raise BadRequest(f"unknown feature {feature!r}")
    val = pd.to_datetime(t["entry_date"]).map(f.set_index("quote_date")[feature])
    ok = val.notna()
    if ok.sum() < bins:
        return clean({"feature": feature, "bins": bins, "rows": [],
                      "note": "too few trades with a feature value to bucket"})
    bucket = pd.qcut(val[ok], bins, duplicates="drop")
    g = t[ok].groupby(bucket, observed=True)["realized_pnl"]
    win = t[ok].assign(w=t.loc[ok, "realized_pnl"] > 0).groupby(bucket, observed=True)["w"].mean()
    order = list(g.count().index)
    rows = [{"bucket": f"Q{i + 1} · {k.left:.2f} to {k.right:.2f}", "trades": int(g.count()[k]),
             "win_rate": float(win[k]), "avg_pnl": float(g.mean()[k]), "total_pnl": float(g.sum()[k])}
            for i, k in enumerate(order)]
    return clean({"feature": feature, "bins": bins, "rows": rows,
                  "features_available": [c for c in f.columns if c != "quote_date"]})
