"""Publication layer: push selected runs/studies to the Sophie PostgreSQL DB.

Follows the sophie-pipeline conventions: psycopg2, DB_USER/DB_PASSWORD/DB_HOST/
DB_NAME (+DB_SSLMODE) or DATABASE_URL env vars, ON CONFLICT upserts. The local
parquet store stays the working store — only explicitly pushed runs go up
(never raw sweep trials).

Env resolution order: process env -> .env in this repo -> sophie-pipeline/.env
(so the shared Sophie credentials just work).
"""

import json
import os
from datetime import date
from pathlib import Path
from typing import Optional

import pandas as pd

from . import ROOT
from .backtest import RunResult

SOPHIE_PIPELINE_ENV = Path("F:/workspace/sophie-pipeline/.env")
SQL_PATH = ROOT / "sql" / "option_research.sql"


def get_db_connection():
    """Same resolution as sophie-pipeline's get_db_connection."""
    import psycopg2
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
    if not os.environ.get("DB_HOST") and not os.environ.get("DATABASE_URL"):
        load_dotenv(SOPHIE_PIPELINE_ENV)

    conn_str = os.environ.get("DATABASE_URL") or (
        f"postgresql://{os.environ.get('DB_USER', '')}:{os.environ.get('DB_PASSWORD', '')}"
        f"@{os.environ.get('DB_HOST', '')}/{os.environ.get('DB_NAME', '')}"
        f"?sslmode={os.environ.get('DB_SSLMODE', 'require')}"
    )
    return psycopg2.connect(conn_str)


def ensure_tables() -> None:
    """Create the option_research_* tables if they don't exist."""
    conn = get_db_connection()
    try:
        with conn, conn.cursor() as cur:
            cur.execute(SQL_PATH.read_text(encoding="utf-8"))
    finally:
        conn.close()


def _json_safe(d: dict) -> str:
    return json.dumps(
        {k: (None if pd.isna(v) else v) if isinstance(v, float) else v
         for k, v in d.items()},
        default=str,
    )


def push_run(result: RunResult, study_tag: str = "", featured: bool = False,
             conn=None) -> str:
    """Upsert one run + its equity curve. Returns the config hash."""
    own = conn is None
    if own:
        conn = get_db_connection()
    cfg = result.config
    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO option_research_run
                    (config_hash, biz_date, name, strategy, study_tag, entry_filter,
                     window_start, window_end, params, metrics, is_featured, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s,
                        CURRENT_TIMESTAMP)
                ON CONFLICT (config_hash) DO UPDATE SET
                    biz_date = EXCLUDED.biz_date,
                    name = EXCLUDED.name,
                    study_tag = EXCLUDED.study_tag,
                    entry_filter = EXCLUDED.entry_filter,
                    metrics = EXCLUDED.metrics,
                    is_featured = EXCLUDED.is_featured,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (result.config_hash, date.today(), cfg.name, cfg.strategy, study_tag,
                 cfg.entry_filter or "", cfg.start, cfg.end,
                 _json_safe(cfg.params), _json_safe(result.metrics), featured),
            )
            eq = result.equity_curve
            if eq is not None and len(eq):
                from psycopg2.extras import execute_values

                rows = [(result.config_hash, pd.Timestamp(d).date(), float(v))
                        for d, v in eq.groupby(eq.index).last().items()]
                execute_values(cur,
                    """
                    INSERT INTO option_research_equity (config_hash, biz_date, equity)
                    VALUES %s
                    ON CONFLICT (config_hash, biz_date) DO UPDATE SET equity = EXCLUDED.equity
                    """, rows)
    finally:
        if own:
            conn.close()
    return result.config_hash


def push_study(tag: str, featured_hashes: Optional[list[str]] = None) -> int:
    """Push every stored run under a results-store tag. Returns rows pushed.

    Reads from the local parquet store (metrics + trade-derived equity), so
    runs don't need to be re-executed to publish them.
    """
    from .experiments import load_runs, load_trades

    runs = load_runs(tag=tag)
    if runs.empty:
        raise ValueError(f"no runs with tag '{tag}' in the results store")
    featured_hashes = set(featured_hashes or [])

    conn = get_db_connection()
    n = 0
    meta_cols = {"config_hash", "name", "strategy", "entry_filter", "start", "end",
                 "params_json", "tag", "run_at"}
    try:
        for _, row in runs.iterrows():
            metrics = {k: v for k, v in row.items()
                       if k not in meta_cols and pd.notna(v)}
            trades = load_trades(row["config_hash"])
            eq = (trades.set_index("exit_date")["equity"]
                  if {"exit_date", "equity"} <= set(trades.columns) else pd.Series(dtype=float))
            from .backtest import StrategyConfig

            result = RunResult(
                config=StrategyConfig(
                    name=row["name"], strategy=row["strategy"],
                    params=json.loads(row["params_json"]),
                    entry_filter=row["entry_filter"] or None,
                    start=row["start"] or None, end=row["end"] or None),
                config_hash=row["config_hash"],
                trade_log=trades, equity_curve=eq, metrics=metrics,
            )
            push_run(result, study_tag=tag,
                     featured=row["config_hash"] in featured_hashes, conn=conn)
            n += 1
    finally:
        conn.close()
    return n


def push_evaluation(study_key: str, memo: dict, narrative: str = "",
                    model: str = "") -> None:
    """Upsert an AI research memo for a study (one per study per day)."""
    conn = get_db_connection()
    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO option_research_evaluation
                    (study_key, biz_date, memo, narrative, model, updated_at)
                VALUES (%s, %s, %s::jsonb, %s, %s, CURRENT_TIMESTAMP)
                ON CONFLICT (study_key, biz_date) DO UPDATE SET
                    memo = EXCLUDED.memo,
                    narrative = EXCLUDED.narrative,
                    model = EXCLUDED.model,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (study_key, date.today(), json.dumps(memo, default=str),
                 narrative, model),
            )
    finally:
        conn.close()
