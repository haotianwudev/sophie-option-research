"""Config-driven backtest runner: YAML/dataclass config -> optopsy run.

A StrategyConfig fully describes one experiment: the optopsy strategy, its
parameters, an optional entry-filter expression over the feature matrix
(e.g. "vix_rank > 0.5 and rsi14 < 40"), and simulation settings. Configs are
hashable so every run is reproducible and addressable in the results store.
"""

import hashlib
import json
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

import optopsy as op
import pandas as pd

from . import CHAIN_DIR
from .features import build_features, entry_dates_from_expr
from .market_data import load_market


@dataclass
class StrategyConfig:
    name: str
    strategy: str                              # optopsy function name, e.g. "short_puts"
    params: dict = field(default_factory=dict)  # strategy kwargs (deltas, DTE, exits, slippage)
    entry_filter: Optional[str] = None         # expression over the feature matrix
    start: Optional[str] = None                # slice of chain data, "YYYY-MM-DD"
    end: Optional[str] = None
    sim: dict = field(default_factory=dict)    # capital, quantity, max_positions, selector

    @classmethod
    def from_yaml(cls, path: str | Path) -> "StrategyConfig":
        import yaml

        with open(path) as fh:
            raw = yaml.safe_load(fh)
        return cls(**raw)

    def to_dict(self) -> dict:
        return {
            "name": self.name, "strategy": self.strategy, "params": self.params,
            "entry_filter": self.entry_filter, "start": self.start, "end": self.end,
            "sim": self.sim,
        }

    def hash(self) -> str:
        blob = json.dumps(self.to_dict(), sort_keys=True, default=str)
        return hashlib.sha256(blob.encode()).hexdigest()[:12]

    def replace(self, **overrides: Any) -> "StrategyConfig":
        """New config with top-level fields and/or params/sim keys overridden.

        Keys not matching a top-level field are treated as strategy params —
        this is what parameter sweeps use: cfg.replace(stop_loss=-2.0).
        """
        d = self.to_dict()
        d["params"] = dict(d["params"])
        d["sim"] = dict(d["sim"])
        for key, val in overrides.items():
            if key in ("name", "strategy", "entry_filter", "start", "end"):
                d[key] = val
            elif key in ("params", "sim"):
                d[key].update(val)
            else:
                d["params"][key] = val
        return StrategyConfig(**d)


@dataclass
class RunResult:
    config: StrategyConfig
    config_hash: str
    trade_log: pd.DataFrame     # one row per simulated trade (entry/exit/pnl)
    equity_curve: pd.Series     # equity after each trade close
    metrics: dict               # flat performance summary from op.simulate


# ---------------------------------------------------------------------------
# Data loading (cached per session)
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1)
def _all_chain_files() -> tuple:
    return tuple(sorted(CHAIN_DIR.glob("*.parquet")))


def load_chains(start: Optional[str] = None, end: Optional[str] = None) -> pd.DataFrame:
    """Load chain parquets, pre-filtering by month from filenames (spx_eod_YYYYMM)."""
    files = _all_chain_files()
    if not files:
        raise SystemExit(f"No chain parquets in {CHAIN_DIR}. Run src/convert_optionsdx.py.")
    if start or end:
        lo = pd.Timestamp(start).strftime("%Y%m") if start else "000000"
        hi = pd.Timestamp(end).strftime("%Y%m") if end else "999999"
        files = [f for f in files if lo <= f.stem[-6:] <= hi]
    df = pd.concat((pd.read_parquet(f) for f in files), ignore_index=True)
    if start:
        df = df[df["quote_date"] >= pd.Timestamp(start)]
    if end:
        df = df[df["quote_date"] <= pd.Timestamp(end)]
    return df.reset_index(drop=True)


@lru_cache(maxsize=1)
def load_features() -> pd.DataFrame:
    return build_features(load_market())


# ---------------------------------------------------------------------------
# Running
# ---------------------------------------------------------------------------


def _strategy_fn(name: str):
    fn = getattr(op, name, None)
    if fn is None or not callable(fn):
        raise ValueError(f"optopsy has no strategy '{name}'")
    return fn


def _entry_dates(config: StrategyConfig, features: pd.DataFrame) -> Optional[pd.DataFrame]:
    if not config.entry_filter:
        return None
    dates = entry_dates_from_expr(features, config.entry_filter)
    if dates.empty:
        raise ValueError(f"entry_filter '{config.entry_filter}' matched no dates")
    return dates


def run_backtest(
    config: StrategyConfig,
    chains: Optional[pd.DataFrame] = None,
    features: Optional[pd.DataFrame] = None,
) -> RunResult:
    """Run one capital-tracked simulation described by *config*."""
    if chains is None:
        chains = load_chains(config.start, config.end)
    if features is None:
        features = load_features()

    kwargs = dict(config.params)
    entry_dates = _entry_dates(config, features)
    if entry_dates is not None:
        kwargs["entry_dates"] = entry_dates

    sim = op.simulate(chains, _strategy_fn(config.strategy), **config.sim, **kwargs)
    return RunResult(
        config=config,
        config_hash=config.hash(),
        trade_log=sim.trade_log,
        equity_curve=sim.equity_curve,
        metrics=dict(sim.summary),
    )


def run_raw_trades(
    config: StrategyConfig,
    chains: Optional[pd.DataFrame] = None,
    features: Optional[pd.DataFrame] = None,
) -> pd.DataFrame:
    """All candidate trades (raw=True), unconstrained by capital/position limits.

    This is the trade universe the ML meta-labeling stage learns from.
    """
    if chains is None:
        chains = load_chains(config.start, config.end)
    if features is None:
        features = load_features()

    kwargs = dict(config.params)
    entry_dates = _entry_dates(config, features)
    if entry_dates is not None:
        kwargs["entry_dates"] = entry_dates
    kwargs["raw"] = True
    return _strategy_fn(config.strategy)(chains, **kwargs)


if __name__ == "__main__":
    cfg = StrategyConfig(
        name="smoke_short_puts",
        strategy="short_puts",
        params={"max_entry_dte": 45, "exit_dte": 21,
                "leg1_delta": {"min": 0.20, "target": 0.30, "max": 0.40}},
        start="2022-01-01", end="2023-12-31",
    )
    res = run_backtest(cfg)
    print(f"hash={res.config_hash}  trades={len(res.trade_log)}")
    print({k: round(v, 3) for k, v in res.metrics.items() if isinstance(v, (int, float))})
