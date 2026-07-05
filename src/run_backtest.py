"""Run sample optopsy backtests against processed OptionsDX data.

Loads every parquet in data/processed/ and runs three demo studies:
  1. Long calls grouped by DTE / delta buckets
  2. Short puts grouped by DTE / delta buckets
  3. Iron condor with ~45 DTE entries and per-leg delta targets

Usage:
    python src/run_backtest.py [--data-dir data/processed]
"""

import argparse
from pathlib import Path

import optopsy as op
import pandas as pd


def load_data(data_dir: Path) -> pd.DataFrame:
    files = sorted(data_dir.glob("*.parquet"))
    if not files:
        raise SystemExit(f"No parquet files in {data_dir}. Run src/convert_optionsdx.py first.")
    df = pd.concat((pd.read_parquet(f) for f in files), ignore_index=True)
    print(f"Loaded {len(df):,} contract rows from {len(files)} file(s), "
          f"{df['quote_date'].min():%Y-%m-%d} to {df['quote_date'].max():%Y-%m-%d}\n")
    return df


def show(title: str, results: pd.DataFrame, top: int = 15):
    print(f"=== {title} ===")
    if results is None or len(results) == 0:
        print("(no trades matched the filters)\n")
        return
    with pd.option_context("display.width", 200, "display.max_columns", None):
        print(results.head(top).to_string(index=False))
    print(f"[{len(results)} result rows]\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", default="data/processed")
    args = parser.parse_args()

    data = load_data(Path(args.data_dir))

    show("Long calls by DTE/delta bucket", op.long_calls(data, max_entry_dte=60, dte_interval=7))

    show("Short puts by DTE/delta bucket", op.short_puts(data, max_entry_dte=60, dte_interval=7))

    show(
        "Iron condor (entries up to 50 DTE, ~16-delta shorts / ~10-delta wings)",
        op.iron_condor(
            data,
            max_entry_dte=50,
            leg1_delta={"min": 0.03, "target": 0.10, "max": 0.20},  # long put wing
            leg2_delta={"min": 0.10, "target": 0.16, "max": 0.30},  # short put
            leg3_delta={"min": 0.10, "target": 0.16, "max": 0.30},  # short call
            leg4_delta={"min": 0.03, "target": 0.10, "max": 0.20},  # long call wing
        ),
    )


if __name__ == "__main__":
    main()
