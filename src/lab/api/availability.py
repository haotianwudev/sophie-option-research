"""Availability of the raw SPX option-chain data -- what exists on disk, what is missing, what is stale.

Read-only, and (like readmodel) engine-free: it never imports optopsy, lab.backtest or lab.market_data.
The unified archive path mirrors lab.backtest.UNIFIED_CHAIN_DIR (same env var, same default) without
importing it.

Three questions, answered from the files themselves:

1. COVERAGE  -- which trading sessions have a chain file, which don't, and which files exist on days
   that were not sessions. "Expected sessions" needs a trading calendar; none is installed, so NYSE
   holidays are computed here and the calendar is VALIDATED against the cached SPX price series (a real
   record of which days traded) on every call. The result reports how many days it agreed on, so a wrong
   calendar shows up as a validation failure instead of as phantom "missing" days.
2. CONTENT   -- a file can exist and still be useless (a truncated chain, a monthly-only corpus). Each file
   is read for row count, distinct expirations and source, and days that are far thinner than their
   neighbours are flagged. This is why day-presence alone is not availability.
3. FRESHNESS -- how many sessions old the newest chain is, and how old each cached market series is.

The archive spans three sources with different root symbols (optionsdx `SPX_UNSPLIT`, then thetadata
`SPX`/`SPXW`, then cboe_live). Those seams are reported, because a backtest crossing one is crossing a
schema change.
"""

from __future__ import annotations

import os
import threading
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Optional

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

from lab import CHAIN_DIR, MARKET_DIR

from .readmodel import clean

UNIFIED_DIR = Path(os.environ.get("SPX_CHAIN_UNIFIED_DIR", r"F:\workspace\sophie-pipeline\data\spx_chain_unified"))

THIN_RATIO = 0.5          # rows (or expirations) below this fraction of the local median => "thin"
THIN_WINDOW = 21          # sessions in the local median
FRESH_OK_SESSIONS = 1     # newest chain at most this many sessions behind => ok
FRESH_WARN_SESSIONS = 5   # up to this many => warning; beyond => stale
MARKET_SERIES = ["spx", "vix", "vix3m", "put", "bxm"]


# ---------------------------------------------------------------------------
# NYSE calendar
# ---------------------------------------------------------------------------

def _easter(y: int) -> date:
    a, b, c = y % 19, y // 100, y % 100
    d, e = b // 4, b % 4
    g = (8 * b + 13) // 25
    h = (19 * a + b - d - g + 15) % 30
    j, k = c // 4, c % 4
    m = (a + 11 * h) // 319
    r = (2 * e + 2 * j - k - h + m + 32) % 7
    month = (h - m + r + 90) // 25
    return date(y, month, (h - m + r + month + 19) % 32)


def _nth_weekday(y: int, month: int, weekday: int, n: int) -> date:
    d = date(y, month, 1)
    d += timedelta(days=(weekday - d.weekday()) % 7)
    return d + timedelta(weeks=n - 1)


def _last_weekday(y: int, month: int, weekday: int) -> date:
    d = date(y, month + 1, 1) - timedelta(days=1) if month < 12 else date(y, 12, 31)
    return d - timedelta(days=(d.weekday() - weekday) % 7)


def _observed(d: date, saturday_rolls_back: bool = True) -> Optional[date]:
    """Sunday -> Monday. Saturday -> the Friday before (NYSE), except New Year's Day (open that Friday)."""
    if d.weekday() == 6:
        return d + timedelta(days=1)
    if d.weekday() == 5:
        return d - timedelta(days=1) if saturday_rolls_back else None
    return d


# One-off closures (national days of mourning, weather). Extend when the calendar validation flags one.
SPECIAL_CLOSURES = {date(2012, 10, 29), date(2012, 10, 30), date(2018, 12, 5), date(2025, 1, 9)}


def nyse_holidays(year: int) -> set[date]:
    h = {
        _observed(date(year, 1, 1), saturday_rolls_back=False),
        _nth_weekday(year, 1, 0, 3),                      # Martin Luther King Jr. Day
        _nth_weekday(year, 2, 0, 3),                      # Washington's Birthday
        _easter(year) - timedelta(days=2),                # Good Friday
        _last_weekday(year, 5, 0),                        # Memorial Day
        _observed(date(year, 7, 4)),                      # Independence Day
        _nth_weekday(year, 9, 0, 1),                      # Labor Day
        _nth_weekday(year, 11, 3, 4),                     # Thanksgiving
        _observed(date(year, 12, 25)),                    # Christmas
    }
    if year >= 2022:
        h.add(_observed(date(year, 6, 19)))               # Juneteenth
    h |= {d for d in SPECIAL_CLOSURES if d.year == year}
    return {d for d in h if d is not None}


def sessions(start: date, end: date) -> list[date]:
    """Trading sessions in [start, end]."""
    hol: set[date] = set()
    for y in range(start.year, end.year + 2):
        hol |= nyse_holidays(y)
    days = pd.bdate_range(start, end)
    return [d.date() for d in days if d.date() not in hol]


# ---------------------------------------------------------------------------
# Scanning the archive (cached; a full scan is ~10 s)
# ---------------------------------------------------------------------------

_lock = threading.Lock()
_scan_cache: dict[str, Any] = {"key": None, "df": None}


def _chain_files() -> list[Path]:
    return sorted(UNIFIED_DIR.glob("year=*/month=*/day=*/chain.parquet"))


def _day_of(p: Path) -> date:
    y, m, d = (int(part.split("=")[1]) for part in p.parts[-4:-1])
    return date(y, m, d)


def scan() -> pd.DataFrame:
    """One row per chain file: date, rows, n_expirations, roots, source, size. Cached on file count + newest mtime."""
    files = _chain_files()
    key = (len(files), max((f.stat().st_mtime_ns for f in files), default=0))
    with _lock:
        if _scan_cache["key"] == key:
            return _scan_cache["df"]
        rows = []
        for f in files:
            try:
                t = pq.read_table(f, columns=["root", "expiration", "source"]).to_pandas()
                rows.append({
                    "date": _day_of(f), "rows": len(t), "n_exp": int(t["expiration"].nunique()),
                    "roots": "+".join(sorted(map(str, t["root"].unique()))),
                    "source": "+".join(sorted(map(str, t["source"].unique()))),
                    "kb": round(f.stat().st_size / 1024), "error": None,
                })
            except Exception as e:  # an unreadable file is itself an availability finding
                rows.append({"date": _day_of(f), "rows": 0, "n_exp": 0, "roots": "", "source": "",
                             "kb": round(f.stat().st_size / 1024), "error": f"{type(e).__name__}: {e}"[:120]})
        df = pd.DataFrame(rows).sort_values("date").reset_index(drop=True)
        _scan_cache.update(key=key, df=df)
        return df


# ---------------------------------------------------------------------------
# Building the report
# ---------------------------------------------------------------------------

def _cache_series() -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    today = date.today()
    for name in MARKET_SERIES:
        p = MARKET_DIR / f"{name}_daily.parquet"
        if not p.exists():
            out[name] = {"exists": False}
            continue
        d = pd.read_parquet(p, columns=["quote_date"])["quote_date"]
        last = d.max().date()
        out[name] = {"exists": True, "rows": len(d), "first": d.min().date().isoformat(),
                     "last": last.isoformat(), "age_days": (today - last).days}
    return out


def _calendar_validation() -> dict[str, Any]:
    """Compare computed sessions with the days the cached SPX series actually has."""
    p = MARKET_DIR / "spx_daily.parquet"
    if not p.exists():
        return {"validated": False, "reason": "no cached SPX price series to validate against"}
    px = {d.date() for d in pd.read_parquet(p, columns=["quote_date"])["quote_date"]}
    lo, hi = min(px), max(px)
    lo = max(lo, date(2010, 1, 4))
    mine = set(sessions(lo, hi))
    theirs = {d for d in px if lo <= d <= hi}
    return {"validated": not (mine ^ theirs), "against": "cached SPX daily prices",
            "from": lo.isoformat(), "to": hi.isoformat(), "sessions_compared": len(theirs),
            "calendar_only": sorted(d.isoformat() for d in mine - theirs)[:20],
            "prices_only": sorted(d.isoformat() for d in theirs - mine)[:20]}


def _legacy_months() -> dict[str, Any]:
    have = set()
    if CHAIN_DIR.exists():
        for f in CHAIN_DIR.glob("spx_eod_*.parquet"):
            tag = f.stem.split("_")[-1]
            if len(tag) == 6 and tag.isdigit():
                have.add((int(tag[:4]), int(tag[4:])))
    if not have:
        return {"exists": False}
    want = {(y, m) for y in range(2010, 2024) for m in range(1, 13)}
    return {"exists": True, "n_files": len(have), "first": "%04d-%02d" % min(have), "last": "%04d-%02d" % max(have),
            "missing_months": sorted("%04d-%02d" % ym for ym in want - have)}


def availability() -> dict[str, Any]:
    if not UNIFIED_DIR.exists() or not any(UNIFIED_DIR.glob("year=*")):
        return clean({"archive": {"exists": False, "path": str(UNIFIED_DIR)},
                      "market_caches": _cache_series(), "legacy_processed": _legacy_months()})

    df = scan()
    today = date.today()
    first, last = df["date"].min(), df["date"].max()
    present = set(df["date"])

    last_session = max(s for s in sessions(last, today) if s < today) if any(
        s < today for s in sessions(last, today)) else last
    # Gaps are judged only up to the newest file. Sessions after it are not 'missing', they have not arrived
    # yet, and that is reported separately as freshness (counting them in both places paints the newest
    # month as a hole and inflates the gap count).
    expected = sessions(first, last)
    exp_set = set(expected)
    missing = [d for d in expected if d not in present]
    unexpected = sorted(d for d in present if d not in exp_set)
    after_last = [d for d in sessions(last, today) if d > last and d < today]

    # --- thin days: judged against the local median WITHIN a source, so a source change is not "thin" ----
    df["thin_rows"] = False
    df["thin_exp"] = False
    for _, g in df.groupby(["source", "roots"]):
        med_rows = g["rows"].rolling(THIN_WINDOW, min_periods=5, center=True).median()
        med_exp = g["n_exp"].rolling(THIN_WINDOW, min_periods=5, center=True).median()
        df.loc[g.index, "thin_rows"] = g["rows"] < THIN_RATIO * med_rows
        df.loc[g.index, "thin_exp"] = g["n_exp"] < THIN_RATIO * med_exp
        df.loc[g.index, "med_rows"] = med_rows
        df.loc[g.index, "med_exp"] = med_exp
    thin = df[df["thin_rows"] | df["thin_exp"] | (df["error"].notna())]

    # --- source segments (consecutive runs of the same source + root symbols) ------------------------------
    seg_id = ((df["source"] != df["source"].shift()) | (df["roots"] != df["roots"].shift())).cumsum()
    segments = [{"source": g["source"].iloc[0], "roots": g["roots"].iloc[0], "from": g["date"].min().isoformat(),
                 "to": g["date"].max().isoformat(), "days": len(g),
                 "median_rows": int(g["rows"].median()), "median_expirations": int(g["n_exp"].median())}
                for _, g in df.groupby(seg_id)]

    # --- per year + per month ------------------------------------------------------------------------------
    df["year"] = [d.year for d in df["date"]]
    exp_df = pd.DataFrame({"date": expected})
    exp_df["year"] = [d.year for d in exp_df["date"]]
    exp_df["month"] = [d.month for d in exp_df["date"]]
    exp_df["present"] = [d in present for d in exp_df["date"]]
    years = []
    for y, e in exp_df.groupby("year"):
        g = df[df["year"] == y]
        years.append({"year": int(y), "expected": len(e), "present": int(e["present"].sum()),
                      "missing": int((~e["present"]).sum()), "median_rows": int(g["rows"].median()) if len(g) else None,
                      "median_expirations": int(g["n_exp"].median()) if len(g) else None,
                      "sources": sorted(set("+".join(g["source"].unique()).split("+")) - {""}),
                      "thin_days": int(df.loc[g.index][["thin_rows", "thin_exp"]].any(axis=1).sum())})
    months = [{"year": int(y), "month": int(m), "expected": len(e), "present": int(e["present"].sum())}
              for (y, m), e in exp_df.groupby(["year", "month"])]

    # --- freshness ----------------------------------------------------------------------------------------
    lag = len(after_last)
    status = "ok" if lag <= FRESH_OK_SESSIONS else ("warning" if lag <= FRESH_WARN_SESSIONS else "stale")

    return clean({
        "as_of": today.isoformat(),
        "archive": {"exists": True, "path": str(UNIFIED_DIR), "n_files": len(df),
                    "first": first.isoformat(), "last": last.isoformat(),
                    "unreadable_files": int(df["error"].notna().sum())},
        "freshness": {"status": status, "lag_sessions": lag, "latest_chain": last.isoformat(),
                      "latest_expected_session": last_session.isoformat(),
                      "sessions_since": [d.isoformat() for d in after_last],
                      "thresholds": {"ok_at_most": FRESH_OK_SESSIONS, "warning_at_most": FRESH_WARN_SESSIONS},
                      "note": "counts sessions strictly before today, since end-of-day data lands after the close"},
        "coverage": {"expected_sessions": len(expected), "present_sessions": len(expected) - len(missing),
                     "missing_sessions": [d.isoformat() for d in missing],
                     "files_on_non_sessions": [d.isoformat() for d in unexpected]},
        "content": {"thin_days": [
            {"date": r["date"].isoformat(), "rows": int(r["rows"]), "median_rows": r.get("med_rows"),
             "expirations": int(r["n_exp"]), "median_expirations": r.get("med_exp"),
             "source": r["source"], "error": r["error"]} for _, r in thin.iterrows()],
            "rule": f"rows or expirations below {THIN_RATIO:.0%} of the {THIN_WINDOW}-session median within the same source"},
        "segments": segments, "years": years, "months": months,
        "series": {"date": [d.isoformat() for d in df["date"]], "rows": df["rows"].tolist(),
                   "expirations": df["n_exp"].tolist()},
        "calendar": _calendar_validation(),
        "market_caches": _cache_series(),
        "legacy_processed": _legacy_months(),
    })
