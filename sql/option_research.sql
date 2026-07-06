-- Option research: backtest runs, equity curves, and AI evaluations
-- pushed from sophie-option-research (results/runs.parquet is the local
-- working store; these tables are the publication layer).

CREATE TABLE IF NOT EXISTS option_research_run (
    id SERIAL PRIMARY KEY,
    config_hash  VARCHAR(12) NOT NULL,
    biz_date     DATE NOT NULL,              -- date the run was pushed
    name         TEXT NOT NULL,
    strategy     VARCHAR(50) NOT NULL,       -- optopsy strategy fn (short_puts, ...)
    study_tag    VARCHAR(50) NOT NULL DEFAULT '',
    entry_filter TEXT,                       -- feature expression, '' if unconditioned
    window_start DATE,
    window_end   DATE,
    params       JSONB NOT NULL DEFAULT '{}'::jsonb,   -- strategy params
    metrics      JSONB NOT NULL DEFAULT '{}'::jsonb,   -- full metrics dict
    is_featured  BOOLEAN NOT NULL DEFAULT FALSE,
    created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT unique_option_research_run UNIQUE (config_hash)
);

CREATE INDEX IF NOT EXISTS idx_option_research_run_tag ON option_research_run(study_tag);

COMMENT ON TABLE option_research_run IS 'SPX option strategy backtest runs (sophie-option-research); one row per config hash';
COMMENT ON COLUMN option_research_run.config_hash IS 'sha256[:12] of the StrategyConfig — stable id across re-pushes';
COMMENT ON COLUMN option_research_run.metrics IS 'Flat dict: sharpe_ratio, premium_capture, probabilistic_sharpe, max_drawdown, ...';


CREATE TABLE IF NOT EXISTS option_research_equity (
    id SERIAL PRIMARY KEY,
    config_hash VARCHAR(12) NOT NULL,
    biz_date    DATE NOT NULL,
    equity      NUMERIC(14, 2) NOT NULL,

    CONSTRAINT unique_option_research_equity UNIQUE (config_hash, biz_date)
);

CREATE INDEX IF NOT EXISTS idx_option_research_equity_hash ON option_research_equity(config_hash);

COMMENT ON TABLE option_research_equity IS 'Equity curve points (per trade close) for charting pushed runs';


CREATE TABLE IF NOT EXISTS option_research_evaluation (
    id SERIAL PRIMARY KEY,
    study_key  VARCHAR(50) NOT NULL,         -- study tag, e.g. vrp09
    biz_date   DATE NOT NULL,
    memo       JSONB NOT NULL DEFAULT '{}'::jsonb,   -- structured research memo
    narrative  TEXT,                                  -- AI-written explanation
    model      VARCHAR(50),                           -- which model wrote it
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT unique_option_research_evaluation UNIQUE (study_key, biz_date)
);

COMMENT ON TABLE option_research_evaluation IS 'AI research memos per study (mirrors investment_clock_evaluation pattern)';
