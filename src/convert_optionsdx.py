"""Convert OptionsDX option chain files to optopsy's long format.

OptionsDX ships wide-format CSVs: one row holds both the call (C_*) and put
(P_*) quote for a strike/expiration. optopsy needs one row per contract with
columns: underlying_symbol, underlying_price, option_type, expiration,
quote_date, strike, bid, ask, delta (+ optional volume).

Handles raw .csv/.txt files and .zip/.7z archives dropped in data/raw/.
Writes one parquet per input file to data/processed/.

Usage:
    python src/convert_optionsdx.py [--symbol SPX] [--raw-dir data/raw] [--out-dir data/processed]
"""

import argparse
import io
import tempfile
import zipfile
from pathlib import Path

import pandas as pd

# Wide-format source columns shared by both legs
SHARED_COLS = {
    "QUOTE_DATE": "quote_date",
    "UNDERLYING_LAST": "underlying_price",
    "EXPIRE_DATE": "expiration",
    "STRIKE": "strike",
}

LEG_COLS = {"BID": "bid", "ASK": "ask", "DELTA": "delta", "VOLUME": "volume"}


def normalize_headers(df: pd.DataFrame) -> pd.DataFrame:
    """Strip brackets and whitespace from OptionsDX headers ('[QUOTE_DATE]' -> 'QUOTE_DATE')."""
    df.columns = [c.strip().strip("[]").strip() for c in df.columns]
    return df


def melt_to_long(df: pd.DataFrame, symbol: str) -> pd.DataFrame:
    df = normalize_headers(df)

    missing = [c for c in SHARED_COLS if c not in df.columns]
    if missing:
        raise ValueError(f"Missing expected OptionsDX columns: {missing}")

    # Intraday products carry multiple snapshots per day; keep the last one
    if "QUOTE_TIME_HOURS" in df.columns:
        last_time = df.groupby("QUOTE_DATE")["QUOTE_TIME_HOURS"].transform("max")
        df = df[df["QUOTE_TIME_HOURS"] == last_time]

    legs = []
    for prefix, option_type in (("C", "c"), ("P", "p")):
        cols = dict(SHARED_COLS)
        cols.update({f"{prefix}_{src}": dst for src, dst in LEG_COLS.items() if f"{prefix}_{src}" in df.columns})
        leg = df[list(cols)].rename(columns=cols)
        leg["option_type"] = option_type
        legs.append(leg)

    out = pd.concat(legs, ignore_index=True)
    out["underlying_symbol"] = symbol
    out["quote_date"] = pd.to_datetime(out["quote_date"].astype(str).str.strip())
    out["expiration"] = pd.to_datetime(out["expiration"].astype(str).str.strip())
    for col in ("underlying_price", "strike", "bid", "ask", "delta", "volume"):
        if col in out.columns:
            out[col] = pd.to_numeric(out[col], errors="coerce")

    # Drop unquotable rows: no market on either side
    out = out.dropna(subset=["strike", "bid", "ask"])
    out = out[(out["bid"] > 0) | (out["ask"] > 0)]

    col_order = [c for c in (
        "underlying_symbol", "underlying_price", "option_type", "expiration",
        "quote_date", "strike", "bid", "ask", "delta", "volume",
    ) if c in out.columns]
    return out[col_order].reset_index(drop=True)


def iter_raw_frames(raw_dir: Path):
    """Yield (name, DataFrame) for every csv/txt inside raw_dir, descending into zip/7z archives."""
    for path in sorted(raw_dir.iterdir()):
        if path.suffix.lower() in (".csv", ".txt"):
            yield path.stem, pd.read_csv(path, low_memory=False)
        elif path.suffix.lower() == ".zip":
            with zipfile.ZipFile(path) as zf:
                for member in zf.namelist():
                    if member.lower().endswith((".csv", ".txt")):
                        with zf.open(member) as fh:
                            yield Path(member).stem, pd.read_csv(io.BytesIO(fh.read()), low_memory=False)
        elif path.suffix.lower() == ".7z":
            import py7zr

            with tempfile.TemporaryDirectory() as tmp:
                with py7zr.SevenZipFile(path) as zf:
                    zf.extractall(tmp)
                for extracted in sorted(Path(tmp).rglob("*")):
                    if extracted.suffix.lower() in (".csv", ".txt"):
                        yield extracted.stem, pd.read_csv(extracted, low_memory=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", default="SPX")
    parser.add_argument("--raw-dir", default="data/raw")
    parser.add_argument("--out-dir", default="data/processed")
    args = parser.parse_args()

    raw_dir, out_dir = Path(args.raw_dir), Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    total = 0
    for name, raw in iter_raw_frames(raw_dir):
        long_df = melt_to_long(raw, args.symbol)
        dest = out_dir / f"{name}.parquet"
        long_df.to_parquet(dest, index=False)
        total += len(long_df)
        print(f"{name}: {len(raw):,} wide rows -> {len(long_df):,} contracts "
              f"({long_df['quote_date'].min():%Y-%m-%d} to {long_df['quote_date'].max():%Y-%m-%d}) -> {dest}")

    if total == 0:
        print(f"No csv/txt/zip files found in {raw_dir}. Download OptionsDX data there first.")
    else:
        print(f"Done: {total:,} contract rows written to {out_dir}")


if __name__ == "__main__":
    main()
