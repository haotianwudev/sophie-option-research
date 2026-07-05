"""Options strategy research platform built on optopsy.

Modules map to research stages:
    market_data  - daily SPX/VIX from Yahoo Finance (cached)
    features     - daily feature matrix (VIX rank, RSI, realized vol, ...)
    backtest     - config-driven strategy backtests
    experiments  - grid/Optuna sweeps, walk-forward, results store
    ml           - meta-labeling with tree models
    report       - tearsheets and run comparison
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "data"
CHAIN_DIR = DATA_DIR / "processed"
MARKET_DIR = DATA_DIR / "market"
RESULTS_DIR = ROOT / "results"
CONFIG_DIR = ROOT / "configs"
