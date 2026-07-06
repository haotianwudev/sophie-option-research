# sophie-option-research

SPX options strategy research platform — a simplified version of an
industrial quant research workflow, built on
[optopsy](https://github.com/goldspanlabs/optopsy) 2.2 with free EOD chain
data from [OptionsDX](https://www.optionsdx.com/) (2010–2023, ~31M contract
rows) and daily SPX/VIX from Yahoo Finance.

Every stage a hedge-fund options researcher runs, kept lean:

| Stage | Module | Notebook |
|---|---|---|
| Data quality | `src/convert_optionsdx.py`, `src/lab/market_data.py` | `01_data_quality` |
| Features / signals | `src/lab/features.py` (VIX rank, RSI, RV, VRP, trend) | `02_features` |
| Backtests | `src/lab/backtest.py` (YAML config → optopsy simulate) | `03_baseline_backtests` |
| Parameter search | `src/lab/experiments.py` (grid + Optuna) | `04_param_sweep` |
| OOS validation | `src/lab/experiments.py` (walk-forward, IS→OOS decay) | `05_walk_forward` |
| ML meta-labeling | `src/lab/ml.py` (LightGBM, purged CV, SHAP) | `06_ml_metalabel` |
| Reporting | `src/lab/report.py` (quantstats tearsheets, regimes) | `07_tearsheet` |
| Roll management | `src/lab/rolling.py` (delta/time-triggered rolls) | `08_rolling` |
| VRP study | `src/lab/features.py` (vrp_z, chain ATM IV rank, term slope) | `09_vrp_study` |
| Metrics | `src/lab/metrics.py` (premium capture, ROM, probabilistic Sharpe, benchmarks) | all |
| Sizing | `src/lab/sizing.py` (fixed / VIX-scaled / fractional Kelly) | `08_rolling` |
| Publication | `src/lab/db.py` + `src/lab/explain.py` (Sophie Postgres + research memos) | `07_tearsheet` |

## Layout

```
configs/               YAML strategy configs (declarative, hashable, reproducible)
notebooks/01..09       the research workflow, one notebook per stage
sql/                   Sophie Postgres schema (option_research_* tables)
src/lab/               platform modules (see table above)
src/convert_optionsdx.py   OptionsDX wide -> optopsy long converter
data/processed/        chain parquets, one per month (gitignored)
data/market/           cached Yahoo Finance daily bars (gitignored)
results/               runs.parquet store + trade logs + HTML tearsheets (gitignored)
```

## Setup

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### Getting chain data (free)

1. Register at [optionsdx.com](https://www.optionsdx.com/), download the SPX
   EOD yearly bundles ($0) into a folder.
2. Convert: `python src\convert_optionsdx.py --symbol SPX --raw-dir <folder>`

SPX/VIX daily bars download automatically from Yahoo on first use
(`lab.market_data.load_market()`).

## Quick start

```python
import sys; sys.path.insert(0, "src")
from lab.backtest import StrategyConfig, run_backtest

cfg = StrategyConfig.from_yaml("configs/short_put_45dte.yaml").replace(
    start="2022-01-01", end="2023-12-31",
    entry_filter="vix_rank > 0.5 and rsi14 < 40",   # any expression over the feature matrix
)
res = run_backtest(cfg)
print(res.metrics)          # sharpe, sortino, max_drawdown, win_rate, ...
res.equity_curve.plot()
```

Then work through `notebooks/01` → `07`; each stage feeds the next and every
run is recorded in `results/runs.parquet` keyed by config hash.

## Research discipline baked in

- **No lookahead**: features use only same-day-close data; entry filters
  apply to the entry date's features.
- **Everything reproducible**: a config hash addresses each run and its
  trade log in the results store.
- **In-sample honesty**: notebook 04 (tuning) is explicitly labeled
  in-sample; notebook 05 walk-forward reports the IS→OOS Sharpe decay.
- **Leak-proof ML**: purged + embargoed time-series CV; a shuffled-label
  check must return AUC ≈ 0.5; filters are evaluated on out-of-fold trades only.

## Publishing results

Studies are published to the Sophie platform's PostgreSQL (same DB as
investment_clock; credentials resolve from `.env` or
`F:/workspace/sophie-pipeline/.env`):

```python
from lab.explain import publish_study
publish_study("vrp09", hypothesis="...")   # runs + equity curves + memo -> DB
```

Tables: `option_research_run`, `option_research_equity`,
`option_research_evaluation` (see `sql/option_research.sql`). The
`/option-research-explain <tag>` Claude skill writes the AI narrative for a
study and upserts it into the evaluation table — the GraphQL/frontend layer
can be added later following the standard Sophie feature pattern.

## optopsy notes

- Chains must be long format: one row per contract with
  `underlying_symbol, option_type, expiration, quote_date, strike, bid, ask, delta`.
- Per-leg delta targets take dicts: `leg1_delta={"min":0.2,"target":0.3,"max":0.4}`.
  Bands that are too narrow silently produce zero trades — start wide.
- `stop_loss` is negative (multiple of premium), `take_profit` positive
  (fraction of max profit). `entry_dates` accepts any
  `(underlying_symbol, quote_date)` DataFrame — that's how feature-based
  entry filters plug in.
