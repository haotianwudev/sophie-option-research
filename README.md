# optopsy-spx

Backtest SPX option strategies with [optopsy](https://github.com/michaelchu/optopsy) using free EOD chain data from [OptionsDX](https://www.optionsdx.com/).

## Layout

```
spx option/          raw OptionsDX .7z yearly/quarterly archives (2010-2022)
spx_eod_2023/        raw OptionsDX monthly .txt files (2023)
data/raw/            alternative drop zone for raw files or archives
data/processed/      converted long-format parquet, one per month (2010-2023, ~31M rows)
src/convert_optionsdx.py   wide -> long converter (optopsy schema; reads .txt/.csv/.zip/.7z)
src/run_backtest.py        demo backtests: long calls, short puts, iron condor
spx_backtest.ipynb         interactive notebook: studies, exits, trade-level plots
```

## Setup

```powershell
.venv\Scripts\Activate.ps1     # Python 3.12 venv, already created
# or recreate: python -m venv .venv; pip install optopsy pandas pyarrow
```

Installed: optopsy 2.2.0, pandas, pyarrow.

## Getting data (free)

1. Register a free account at [optionsdx.com](https://www.optionsdx.com/).
2. Add the **SPX Option Chain — End of Day** yearly bundles ($0) to cart and download.
3. Drop the `.txt` / `.zip` files into a folder (e.g. `spx_eod_2023/`).

## Usage

```powershell
# Convert raw OptionsDX files to optopsy-ready parquet
python src\convert_optionsdx.py --symbol SPX --raw-dir spx_eod_2023

# Run the demo backtests
python src\run_backtest.py
```

## Notes

- optopsy requires long format: one row per contract with
  `underlying_symbol, option_type, expiration, quote_date, strike, bid, ask, delta`
  (`delta` is mandatory in v2.2; `underlying_price` and `volume` are carried as extras).
- OptionsDX ships wide format (call + put per row) with bracketed headers
  (`[QUOTE_DATE]`) — the converter normalizes both, and collapses intraday
  snapshots to the last one per day if you feed it intraday products.
- Put deltas stay negative; optopsy filters on `abs(delta)` internally.
- Per-leg delta targets take dicts: `leg1_delta={"min":0.03,"target":0.10,"max":0.20}`.
  Bands that are too narrow silently produce zero trades — start wide.
- Returned stats are grouped by DTE/delta buckets: `count, mean, win_rate,
  profit_factor`, etc. Pass `raw=True` for trade-level output, or use
  `op.simulate()` / `op.simulate_portfolio()` for capital-tracked simulations.
