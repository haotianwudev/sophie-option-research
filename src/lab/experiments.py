"""Parameter search, walk-forward validation, and the results store.

Every run is recorded in results/runs.parquet keyed by config hash, with the
full trade log in results/trades/{hash}.parquet — so any past experiment can
be reloaded and compared from a notebook without re-running it.
"""

import itertools
import json
import threading
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Optional

import pandas as pd

from . import RESULTS_DIR
from .backtest import RunResult, StrategyConfig, load_chains, load_features, run_backtest

RUNS_PATH = RESULTS_DIR / "runs.parquet"
TRADES_DIR = RESULTS_DIR / "trades"

# save_run does a read-modify-write of runs.parquet; grid_sweep calls it from
# parallel threads, so the whole operation must be serialized
_STORE_LOCK = threading.Lock()


# ---------------------------------------------------------------------------
# Results store
# ---------------------------------------------------------------------------


def save_run(result: RunResult, tag: str = "") -> str:
    """Append a run's metrics to the store and persist its trade log."""
    TRADES_DIR.mkdir(parents=True, exist_ok=True)
    row = {
        "config_hash": result.config_hash,
        "name": result.config.name,
        "strategy": result.config.strategy,
        "entry_filter": result.config.entry_filter or "",
        "start": result.config.start or "",
        "end": result.config.end or "",
        "params_json": json.dumps(result.config.to_dict()["params"], default=str),
        "tag": tag,
        "run_at": datetime.now().isoformat(timespec="seconds"),
        **{k: v for k, v in result.metrics.items() if isinstance(v, (int, float))},
    }
    with _STORE_LOCK:
        runs = pd.DataFrame([row])
        if RUNS_PATH.exists():
            existing = pd.read_parquet(RUNS_PATH)
            # Latest run for a hash+tag wins; keep the store deduplicated
            existing = existing[
                ~((existing["config_hash"] == row["config_hash"]) & (existing["tag"] == tag))
            ]
            runs = pd.concat([existing, runs], ignore_index=True)
        runs.to_parquet(RUNS_PATH, index=False)
        result.trade_log.to_parquet(TRADES_DIR / f"{result.config_hash}.parquet", index=False)
    return result.config_hash


def load_runs(tag: Optional[str] = None) -> pd.DataFrame:
    if not RUNS_PATH.exists():
        return pd.DataFrame()
    runs = pd.read_parquet(RUNS_PATH)
    return runs[runs["tag"] == tag] if tag else runs


def load_trades(config_hash: str) -> pd.DataFrame:
    return pd.read_parquet(TRADES_DIR / f"{config_hash}.parquet")


# ---------------------------------------------------------------------------
# Grid sweep
# ---------------------------------------------------------------------------


def grid_sweep(
    base: StrategyConfig,
    param_grid: dict[str, list],
    tag: str = "grid",
    n_jobs: int = -1,
    save: bool = True,
) -> pd.DataFrame:
    """Run every combination in *param_grid* (values applied via cfg.replace).

    Grid keys can be strategy params (stop_loss), top-level fields
    (entry_filter), or dotted leg-delta targets like "leg1_delta.target"
    (which shifts min/max by the same amount to keep the band width).

    Returns one row per combo with params + metrics, sorted by Sharpe.
    """
    from joblib import Parallel, delayed

    keys = list(param_grid)
    combos = list(itertools.product(*param_grid.values()))
    chains = load_chains(base.start, base.end)
    features = load_features()

    def one(combo: tuple) -> dict:
        overrides = dict(zip(keys, combo))
        cfg = _apply_overrides(base, overrides)
        try:
            result = run_backtest(cfg, chains=chains, features=features)
        except Exception as exc:  # zero-trade bands etc. — record and continue
            return {**overrides, "config_hash": cfg.hash(), "error": str(exc)[:200]}
        if save:
            save_run(result, tag=tag)
        return {
            **overrides,
            "config_hash": result.config_hash,
            "n_trades": len(result.trade_log),
            **{k: v for k, v in result.metrics.items() if isinstance(v, (int, float))},
        }

    rows = Parallel(n_jobs=n_jobs, backend="threading")(delayed(one)(c) for c in combos)
    out = pd.DataFrame(rows)
    if "sharpe_ratio" in out.columns:
        out = out.sort_values("sharpe_ratio", ascending=False, ignore_index=True)
    return out


def _apply_overrides(base: StrategyConfig, overrides: dict[str, Any]) -> StrategyConfig:
    flat: dict[str, Any] = {}
    for key, val in overrides.items():
        if "." in key:  # e.g. "leg1_delta.target": shift the whole band
            leg, part = key.split(".", 1)
            band = dict(base.params[leg])
            if part == "target":
                shift = val - band["target"]
                band = {"min": round(band["min"] + shift, 4),
                        "target": val,
                        "max": round(band["max"] + shift, 4)}
            else:
                band[part] = val
            flat[leg] = band
        else:
            flat[key] = val
    name = base.name + "|" + ",".join(f"{k}={v}" for k, v in overrides.items())
    return base.replace(name=name, **flat)


# ---------------------------------------------------------------------------
# Optuna search
# ---------------------------------------------------------------------------


def optuna_search(
    base: StrategyConfig,
    search_space: Callable[["optuna.Trial"], dict],  # noqa: F821
    n_trials: int = 50,
    metric: str = "sharpe_ratio",
    tag: str = "optuna",
    seed: int = 42,
):
    """TPE search over an arbitrary space.

    *search_space* receives an optuna Trial and returns an overrides dict,
    e.g. {"leg1_delta.target": trial.suggest_float("delta", 0.1, 0.4)}.
    Returns the optuna Study (best_params, trials_dataframe, ...).
    """
    import optuna

    chains = load_chains(base.start, base.end)
    features = load_features()

    def objective(trial: "optuna.Trial") -> float:
        cfg = _apply_overrides(base, search_space(trial))
        try:
            result = run_backtest(cfg, chains=chains, features=features)
        except Exception:
            return float("-inf")
        if len(result.trade_log) < 10:  # too few trades to trust the metric
            return float("-inf")
        save_run(result, tag=tag)
        return float(result.metrics.get(metric, float("-inf")))

    optuna.logging.set_verbosity(optuna.logging.WARNING)
    study = optuna.create_study(
        direction="maximize", sampler=optuna.samplers.TPESampler(seed=seed)
    )
    study.optimize(objective, n_trials=n_trials, show_progress_bar=False)
    return study


# ---------------------------------------------------------------------------
# Walk-forward validation
# ---------------------------------------------------------------------------


@dataclass
class WalkForwardResult:
    windows: pd.DataFrame        # one row per split: chosen params, IS + OOS metrics
    oos_trades: pd.DataFrame     # concatenated out-of-sample trade logs

    @property
    def decay(self) -> pd.DataFrame:
        """In-sample vs out-of-sample metric comparison per window."""
        cols = [c for c in self.windows.columns
                if c.startswith("is_") or c.startswith("oos_")]
        return self.windows[["train", "test", "best_params"] + cols]


def walk_forward(
    base: StrategyConfig,
    param_grid: dict[str, list],
    train_years: int = 4,
    test_years: int = 1,
    first_year: int = 2010,
    last_year: int = 2023,
    metric: str = "sharpe_ratio",
    min_trades: int = 10,
) -> WalkForwardResult:
    """Rolling walk-forward: tune on train window, evaluate frozen params OOS.

    The honest-researcher check — reported performance comes only from data
    the parameter choice never saw.
    """
    rows, oos_logs = [], []
    year = first_year
    while year + train_years + test_years - 1 <= last_year:
        tr0, tr1 = f"{year}-01-01", f"{year + train_years - 1}-12-31"
        te0 = f"{year + train_years}-01-01"
        te1 = f"{year + train_years + test_years - 1}-12-31"

        is_cfg = base.replace(start=tr0, end=tr1)
        sweep = grid_sweep(is_cfg, param_grid, tag=f"wf_is_{year}", save=False)
        ok = sweep[sweep.get("n_trades", 0) >= min_trades] if "n_trades" in sweep else sweep
        if ok.empty or metric not in ok.columns:
            year += test_years
            continue
        best = ok.iloc[0]
        best_overrides = {k: best[k] for k in param_grid}

        oos_cfg = _apply_overrides(base.replace(start=te0, end=te1), best_overrides)
        oos = run_backtest(oos_cfg)
        save_run(oos, tag="wf_oos")
        log = oos.trade_log.copy()
        log["window"] = f"{year + train_years}"
        oos_logs.append(log)

        rows.append({
            "train": f"{year}-{year + train_years - 1}",
            "test": f"{year + train_years}",
            "best_params": json.dumps(best_overrides, default=str),
            **{f"is_{metric}": best[metric], "is_n_trades": best.get("n_trades")},
            **{f"oos_{k}": v for k, v in oos.metrics.items()
               if k in (metric, "win_rate", "total_return", "max_drawdown", "total_trades")},
        })
        year += test_years

    return WalkForwardResult(
        windows=pd.DataFrame(rows),
        oos_trades=pd.concat(oos_logs, ignore_index=True) if oos_logs else pd.DataFrame(),
    )
