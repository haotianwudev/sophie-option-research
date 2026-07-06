# sophie-option-research

SPX options strategy research platform — a simplified but complete version of
an industrial quant research workflow. Built on
[optopsy](https://github.com/goldspanlabs/optopsy) 2.2 as the backtest engine,
free EOD option-chain data from [OptionsDX](https://www.optionsdx.com/)
(2010–2023, ~31M contract rows), and daily SPX/VIX/benchmark series from Yahoo
Finance. Python + Jupyter throughout; results publish to the Sophie platform's
PostgreSQL.

**What you can research here:** entry filters (VIX rank, RSI, VRP, IV rank,
term structure), strategy parameters (delta, DTE, exits), trade management
(profit-taking, time stops, rolling), position sizing, and ML trade filtering —
each with the validation discipline (walk-forward, purged CV, benchmarks,
caveat tracking) that separates research from curve-fitting.

---

## The research workflow

Nine notebooks, one per stage. Each stage feeds the next; every run lands in a
local results store keyed by config hash.

| # | Stage | Notebook | Modules | What it answers |
|---|---|---|---|---|
| 01 | Data quality | `01_data_quality` | `convert_optionsdx.py` | Is the chain data complete and sane (coverage, crossed quotes, delta signs)? |
| 02 | Features | `02_features` | `lab/features.py`, `lab/market_data.py` | What do the regime signals (VIX rank, RSI, VRP, term slope) look like, and how often is each filter true? |
| 03 | Baselines | `03_baseline_backtests` | `lab/backtest.py` | Do short puts / iron condors work at all? Do signal-conditioned entries beat unconditioned? |
| 04 | Parameter search | `04_param_sweep` | `lab/experiments.py` | Which delta/DTE/exit combos look best (grid + Optuna)? Is the optimum stable or a spike? Plus the tastylive management matrix. |
| 05 | Walk-forward | `05_walk_forward` | `lab/experiments.py` | Does the tuned edge survive out-of-sample? (IS→OOS Sharpe decay, parameter stability) |
| 06 | ML meta-labeling | `06_ml_metalabel` | `lab/ml.py` | Can a LightGBM model, trained under purged+embargoed CV, tell good entries from bad? (SHAP, leakage check) |
| 07 | Reporting + publish | `07_tearsheet` | `lab/report.py`, `lab/explain.py`, `lab/db.py` | Final tearsheets, PUT/BXM benchmark comparison, research memo, push to Sophie DB. |
| 08 | Rolling & sizing | `08_rolling` | `lab/rolling.py`, `lab/sizing.py` | When to roll: offensive vs defensive by delta, tastylive time roll; fixed vs VIX-scaled vs Kelly sizing. |
| 09 | VRP study | `09_vrp_study` | `lab/features.py` | Is the volatility risk premium measurable and filterable (VRP z-score, chain-native ATM IV rank, term slope)? |

---

## Setup

### 1. Environment

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### 2. Chain data (free, one-time)

1. Register at [optionsdx.com](https://www.optionsdx.com/) and download the
   **SPX Option Chain — End of Day** yearly bundles ($0) into a folder.
2. Convert to optopsy's long format (one parquet per month into
   `data/processed/`):

```powershell
python src\convert_optionsdx.py --symbol SPX --raw-dir <download-folder>
```

The converter reads `.txt`/`.csv`/`.zip`/`.7z`, normalizes the bracketed
OptionsDX headers, splits call/put wide rows into long rows, and drops
unquotable contracts.

### 3. Market data (automatic)

SPX (`^GSPC`), VIX, VIX3M, and the CBOE benchmark indexes PUT/BXM download
from Yahoo Finance on first use and cache to `data/market/` (refreshed when
older than 7 days). Offline? The cache is used as-is, and SPX can be derived
from the chains themselves.

### 4. Database (optional — only for publishing)

Publishing uses the Sophie platform's PostgreSQL. Credentials resolve from
process env → `.env` in this repo → `F:/workspace/sophie-pipeline/.env`
(`DATABASE_URL`, or `DB_USER`/`DB_PASSWORD`/`DB_HOST`/`DB_NAME`/`DB_SSLMODE`).
Tables are created on first push (`sql/option_research.sql`).

---

## Quick start

```python
import sys; sys.path.insert(0, "src")
from lab.backtest import StrategyConfig, run_backtest

cfg = StrategyConfig.from_yaml("configs/short_put_45dte.yaml").replace(
    start="2022-01-01", end="2023-12-31",
    entry_filter="vix_rank > 0.5 and rsi14 < 40",  # any expression over the feature matrix
)
res = run_backtest(cfg)
print(res.metrics)        # sharpe, premium_capture, probabilistic_sharpe, ...
res.equity_curve.plot()
```

A config fully describes an experiment — strategy, params, entry filter,
window, simulation settings — and hashes to a stable 12-char id used
everywhere (results store, trade logs, DB). Sweep helpers derive configs with
`cfg.replace(...)`, so every trial stays reproducible.

```yaml
# configs/short_put_45dte.yaml
name: short_put_45dte
strategy: short_puts            # any optopsy strategy fn (38 available)
params:
  max_entry_dte: 45
  exit_dte: 21                  # manage at 21 DTE
  leg1_delta: {min: 0.20, target: 0.30, max: 0.40}
  take_profit: 0.5              # close at 50% of max profit
  stop_loss: -2.0               # or at 2x premium loss (negative by convention)
entry_filter: null              # e.g. "vrp_z > 0.5"
sim: {capital: 100000, quantity: 1, max_positions: 1}
```

---

## Module reference (`src/lab/`)

### `market_data.py` — daily series
`load_market()` → SPX OHLCV + `vix` + `vix3m` columns. `load_benchmark("PUT")`
→ CBOE PutWrite index closes for benchmarking. All Yahoo-sourced, parquet-cached.

### `features.py` — the shared feature store
One daily matrix serves both entry filters and ML models (the industrial
"single feature store" pattern). No lookahead: everything is computed from
same-day closes.

| Feature | Meaning |
|---|---|
| `vix_rank` | VIX percentile within trailing 252 days (0–1) |
| `rsi14` | Wilder RSI on SPX |
| `realized_vol_20d` | Annualized 20-day realized vol |
| `vol_risk_premium` | VIX − realized vol (the VRP, in vol points) |
| `vrp_z` | EWM z-score of the VRP (is the premium rich vs its own history?) |
| `atm_iv`, `atm_iv_rank` | Chain-native ATM IV (Brenner–Subrahmanyam from the ~30-DTE straddle) and its 1y rank |
| `term_slope` | VIX3M − VIX; negative = backwardation = stress |
| `ret_5d`, `ret_21d`, `sma50_above_sma200`, `dist_from_high_52w`, `day_of_week` | Trend/momentum context |

Entry filters are plain pandas expressions over this matrix
(`"vrp_z > 0.5 and rsi14 < 40"`), converted to optopsy `entry_dates`.

### `backtest.py` — config-driven runner
`run_backtest(config)` → `RunResult` with capital-tracked trade log, equity
curve, and the full metrics dict. `run_raw_trades(config)` → every candidate
trade unconstrained by capital (the ML training universe). Chain loading is
month-partitioned, so short windows are fast.

### `metrics.py` — options-grade metrics
Added to optopsy's Sharpe/Sortino/VaR set on every run:

- **Options-specific**: `premium_capture` (net P&L ÷ gross credit),
  `avg/ann_return_on_margin` (RegT naked-put BPR proxy), `pnl_per_day_in_trade`,
  `exposure` (% days in market), `worst_trade_over_avg_credit` (tail vs income)
- **Statistical honesty**: `cagr`, `ann_vol`, `trade_pnl_tstat`,
  `probabilistic_sharpe` (Bailey–López de Prado — P[true Sharpe > 0]; <0.9
  means the edge is not established), `max_dd_days`
- **Benchmark-relative**: `corr_/beta_/alpha_ann_/excess_cagr_` vs SPX or PUT

### `experiments.py` — search, validation, results store
- `grid_sweep(base, {"leg1_delta.target": [...], "take_profit": [...]})` —
  parallel grid; dotted keys shift whole delta bands
- `optuna_search(base, space_fn, n_trials)` — TPE for larger spaces
- `walk_forward(base, grid, train_years, test_years)` — tune on train, freeze,
  evaluate OOS; returns per-window IS/OOS decay + stitched OOS trades
- Results store: `results/runs.parquet` (+ `results/trades/{hash}.parquet`),
  thread-safe, deduplicated by hash+tag, queryable from any notebook

### `ml.py` — meta-labeling
The strategy proposes trades; the model filters them. `build_dataset` joins
entry-day features to raw trades; `PurgedTimeSeriesSplit` embargoes overlapping
holding periods; LightGBM + SHAP; `leakage_check` (shuffled labels must give
AUC ≈ 0.5); score-filter vs baseline compared on out-of-fold trades only.

### `rolling.py` — position management
optopsy models one entry + one exit, so rolling has its own campaign simulator
that tracks each short put daily on the EOD chains:

- **Defensive roll**: |Δ| ≥ threshold → buy back, re-sell at target delta
  further out (out + down)
- **Offensive roll**: |Δ| ≤ threshold → lock the early win, re-strike
- **Time roll** (tastylive): at N DTE, same strike, next expiration
- `compare_variants` runs all policies on identical entry dates; leg-level
  ledger shows what each roll cost/earned

### `sizing.py` — sizing overlays
Post-processing on a finished trade log (entries/exits identical, only
contract counts change): fixed 1-lot, VIX-scaled (`target_vix/VIX`, capped),
fractional Kelly from prior trades only. Compare on `return_over_maxdd`,
not total P&L.

### `report.py` — tearsheets & comparisons
quantstats HTML tearsheets, `compare_runs` tables from the store,
`regime_breakdown` (performance by feature quantile at entry),
`param_heatmap` for sweep results.

### `explain.py` + `db.py` — memo & publication
`build_memo(tag)` → structured research memo: hypothesis, method, headline
results, and **auto-generated caveats** (EOD mid fills, no costs, in-sample
flags, low-trade-count warnings). `render_memo` → markdown in
`results/reports/`. `publish_study(tag)` → runs + equity curves + memo
upserted into the Sophie Postgres:

- `option_research_run` — one row per config hash (params + metrics as JSONB)
- `option_research_equity` — equity-curve points for charting
- `option_research_evaluation` — memo JSONB + AI narrative per study

The **`/option-research-explain <tag>`** Claude skill reads the memo and
writes a four-section narrative (what we tested / what we found / mechanism /
what would falsify it), then upserts it into the evaluation table — same
pattern as the Investment Clock analysis skill. A GraphQL resolver + Next.js
page can be added later following the standard Sophie feature pattern.

---

## Research discipline baked in

- **No lookahead** — features are close-of-day; a trade entered on day T sees
  only data through T's close (matching EOD chain quotes).
- **Reproducible** — the config hash addresses every run, its params, trade
  log, and DB rows.
- **In-sample honesty** — sweep notebooks are labeled in-sample; walk-forward
  reports the IS→OOS decay; memos auto-flag studies with no OOS runs.
- **Leak-proof ML** — purged + embargoed CV, shuffled-label check, out-of-fold
  evaluation only.
- **Benchmarked** — a strategy earns its complexity only if it beats CBOE PUT
  (the public "just sell puts" index) and SPX.
- **Standing caveats** (stated in every memo): fills are EOD mids with no
  slippage/commission unless configured; triggers fire at the close; SPX is
  European-style so early assignment doesn't exist, but that also means these
  results do not transfer to American-style equity options unmodified.

## Layout

```
configs/                   YAML strategy configs (declarative, hashable)
notebooks/01..09           the research workflow, one notebook per stage
sql/option_research.sql    Sophie Postgres schema
src/lab/                   platform modules (see reference above)
src/convert_optionsdx.py   OptionsDX wide -> optopsy long converter
src/run_backtest.py        original CLI demo (predates the platform)
spx_backtest.ipynb         original exploration notebook (predates the platform)
data/processed/            chain parquets, one per month   (gitignored)
data/market/               cached Yahoo daily bars          (gitignored)
results/                   runs.parquet + trade logs + reports (gitignored)
```

## optopsy notes

- Chains must be long format: one row per contract with `underlying_symbol,
  option_type, expiration, quote_date, strike, bid, ask, delta`.
- Per-leg delta targets take dicts: `leg1_delta={"min":0.2,"target":0.3,"max":0.4}`.
  Bands that are too narrow silently produce zero trades — start wide.
- `stop_loss` is negative (multiple of premium), `take_profit` positive
  (fraction of max profit).
- `entry_dates` accepts any `(underlying_symbol, quote_date)` DataFrame —
  that's how feature-based entry filters plug in.
- 38 strategies are available (`op.short_puts`, `op.iron_condor`,
  `op.short_strangles`, calendars, butterflies, ...) — any of them work with
  this platform's config runner unchanged.

## Key research sources

- [AQR — Understanding the Volatility Risk Premium](https://www.aqr.com/-/media/AQR/Documents/Whitepapers/Understanding-the-Volatility-Risk-Premium.pdf)
- [Quantpedia — Volatility Risk Premium Effect](https://quantpedia.com/strategies/volatility-risk-premium-effect) · [Short-vol strategies overview](https://quantpedia.com/overview-of-different-short-volatility-strategies/)
- [arXiv — Sizing the Risk: Kelly, VIX, and Hybrid Approaches in Put-Writing](https://arxiv.org/html/2508.16598v1)
- [CBOE BXM methodology](https://cdn.cboe.com/api/global/us_indices/governance/BXM_Methodology.pdf) · [Wilshire — Options-Based Benchmark Indexes](https://cdn.cboe.com/resources/spx/wilshire-options-based-benchmark-indexes-2019.pdf)
- [Option Samurai — IV vs RV backtests](https://optionsamurai.com/blog/implied-volatility-backtest-pt-3-iv-and-rv/)
- Bailey & López de Prado — *The Sharpe Ratio Efficient Frontier* (probabilistic/deflated Sharpe); López de Prado — *Advances in Financial Machine Learning* (meta-labeling, purged CV)
- tastylive mechanics (45 DTE, 50% profit-take, 21 DTE management) — practitioner canon
