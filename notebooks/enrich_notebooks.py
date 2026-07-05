"""Add markdown explanations to each notebook step."""
import json
from pathlib import Path

def enrich_notebook(nb_path, explanations):
    """Insert markdown cells before code cells.

    explanations = {code_cell_index: markdown_text}
    """
    nb = json.load(open(nb_path, encoding='utf-8'))
    new_cells = []
    code_count = 0

    for i, cell in enumerate(nb['cells']):
        if cell['cell_type'] == 'code':
            if code_count in explanations:
                md = {
                    'cell_type': 'markdown',
                    'metadata': {},
                    'source': [line + '\n' for line in explanations[code_count].split('\n')]
                }
                new_cells.append(md)
            code_count += 1
        new_cells.append(cell)

    nb['cells'] = new_cells
    json.dump(nb, open(nb_path, 'w', encoding='utf-8'), indent=1)
    print(f"enriched {nb_path.name}: added {len(explanations)} explanations")

nb_dir = Path('/f/workspace/sophie-option-research/notebooks')

# 01_data_quality: 7 code cells
enrich_notebook(nb_dir / '01_data_quality.ipynb', {
    0: """### Load the full chain dataset
This loads all parquet files from `data/processed/` (monthly files covering 2010–2023).
We'll examine their coverage, quote quality, and underlying price path.""",
    1: """### Monthly coverage check
Count the contract rows and trading days per month. We expect ~21 trading days per month.
If a month has <18 days or very few rows, it might have bad data or missing downloads.""",
    2: """### Quote sanity: are bids and asks reasonable?
- Crossed markets (bid > ask) indicate bad data
- Negative bids are nonsensical
- Spread distribution tells us about liquidity
- Delta should be negative for puts, positive for calls""",
    3: """### SPX price path from the chains
Extract the underlying SPX price from the quotes' `underlying_price` column.
This is our baseline for all options valuations—if it looks wrong, the chain data is suspect.""",
})

# 02_features: 4 code cells
enrich_notebook(nb_dir / '02_features.ipynb', {
    0: """### Build the daily feature matrix
Compute all entry-condition features from SPX bars and VIX closes.
These are the same features used by:
- Entry filters in backtests (e.g., "only enter when vix_rank > 0.5")
- ML models as inputs to predict winning trades""",
    1: """### Implied vs realized volatility
VIX is implied vol from index options. Realized vol (RV) is the actual price movement.
When VIX > RV, short vol is attractive (vol-risk premium is positive).
When RV > VIX, the market underpriced volatility—beware.""",
    2: """### RSI and VIX rank: regime detection
- RSI(14) below 30 = oversold (SPX fell hard recently)
- VIX percentile rank: where is vol relative to the past year?
- Scatter plot shows the co-movement: high vol often happens with oversold conditions""",
    3: """### Entry filter prevalence
Check how often each candidate filter is true in the data.
Filters that are too rare produce few trades (noisy results).
Filters that are too common don't add selectivity.""",
})

# 03_baseline_backtests: 6 code cells
enrich_notebook(nb_dir / '03_baseline_backtests.ipynb', {
    0: """### Unconditioned short puts: the baseline
Run short puts with no entry filter—every calendar date with quoted options is a potential entry.
This is our control: all trades vs. only signal-conditioned trades.""",
    1: """### Conditioned entries: vol and oversold filters
Test three variants:
- **vix_rank > 0.5**: only when vol is elevated (above 1-year median)
- **vix_rank > 0.8**: very high vol (top 20% of the year)
- **rsi14 < 40 AND vix_rank > 0.5**: both conditions must hold

The equity curves show which filters add edge. Overlapping is fine; better to be selective.""",
    2: """### Results table: rank by Sharpe ratio
Compare win rate, return, Sharpe, max drawdown. Which filter does best?
Note: everything here is in-sample (we saw all the data)—chapter 05 is the honest check.""",
    3: """### Iron condor baseline
The baseline strategy without any entry filter. Iron condors collect premium from
four legs (two shorts + two wings) so they need careful delta targeting.
Entry filter expression in the config filters these entries too.""",
    4: """### Performance by VIX-rank regime
Split trades by the VIX-rank quartile at entry. Does the strategy do better in
high-vol or low-vol environments? Understanding regimes helps with risk management.""",
})

# 04_param_sweep: 4 code cells
enrich_notebook(nb_dir / '04_param_sweep.ipynb', {
    0: """### Define the parameter grid
We'll try different combinations of:
- **leg1_delta.target**: shift the delta band for short puts (0.10, 0.16, 0.30 = 10-, 16-, 30-delta)
- **take_profit**: exit at % of max profit (e.g., 0.5 = 50%)
- **stop_loss**: hard stop at multiple of entry premium (e.g., -2.0 = 2x loss)

`grid_sweep` runs all combos in parallel, records results in `results/runs.parquet`.""",
    1: """### Heatmap: Sharpe ratio over deltas × take-profit
Light shades = low Sharpe (underperforming), dark = high (good).
Look for a smooth peak: if one cell is great and neighbors are bad, it's overfit noise.
Smooth decay around a peak suggests a real edge.""",
    2: """### Parameter stability check
Max drawdown heatmap. Stable parameters degrade smoothly as you move away from the optimum.
Unstable zones (sudden cliffs) indicate fragility—avoid those regions.""",
    3: """### Optuna Bayesian optimization
Instead of exhaustive grid, use Optuna's TPE sampler to intelligently explore the space.
We search over delta, take_profit, and a new dimension: entry_filter vix_rank threshold.
This fits more parameters in fewer trials.""",
})

# 05_walk_forward: 4 code cells
enrich_notebook(nb_dir / '05_walk_forward.ipynb', {
    0: """### Walk-forward validation: the honest test
Divide time into rolling windows: train on years 1–4, test on year 5, then shift forward.
- Tune parameters on the training window (using grid_sweep)
- Freeze those parameters
- Evaluate on the test window data the optimizer never saw

This reveals **in-sample overfitting**: if IS Sharpe >> OOS Sharpe, the parameter choice
was lucky, not skill.""",
    1: """### IS vs OOS comparison per year
- **IS Sharpe**: Sharpe on the window we tuned on (likely inflated)
- **OOS Sharpe**: Sharpe on the holdout year (the honest measure)

If OOS decays much faster than IS, the market changed or we overfit.
A good strategy should show decay, but not collapse.""",
    2: """### Walk-forward equity curve
Stitch together the OOS trade logs from each window. This is what you'd actually
make if you re-tuned parameters once per year. It's the most realistic return estimate.""",
    3: """### Parameter stability across time
Did we choose the same parameters each year? If yes, the edge is stable.
If parameters flip wildly, the edge is fragile—might not survive live trading.""",
})

# 06_ml_metalabel: 6 code cells
enrich_notebook(nb_dir / '06_ml_metalabel.ipynb', {
    0: """### Build the ML dataset
Run the strategy with `raw=True` to get all candidate trades (unconstrained by capital/positions).
For each trade, join the entry-day market features (VIX rank, RSI, etc.).
Label each trade: win (1) if pct_change > 0, else lose (0).

This dataset is the training set for a meta-classifier: given entry conditions,
should we take this trade or skip it?""",
    1: """### Train with purged + embargoed time-series CV
Standard k-fold CV leaks on overlapping trades. Instead:
- **Purge**: remove training samples whose exit date overlaps the test trade's holding period
- **Embargo**: exclude trades entered within `embargo_days` of the test boundary

This prevents the model from seeing outcomes it couldn't know at decision time.""",
    2: """### Out-of-fold AUC and win rates
- **Mean OOF AUC**: if > 0.55–0.60, the model has signal (AUC 0.5 = random)
- **Top-half win rate**: among high-confidence predictions, what's the win rate?
- If top-half win rate is close to base rate, the model isn't separating winners from losers""",
    3: """### Leakage check: shuffle the labels
If we train on random labels, AUC should collapse to 0.5 (no signal).
If it stays high, we have **information leakage**—the model is memorizing the data, not learning.

This check passed, so the model isn't cheating.""",
    4: """### SHAP feature importance
Which features drive the model's predictions? SHAP values explain each prediction.
Look for domain-sensible importances: VIX rank matters more than day-of-week, etc.
If the top features are nonsense, be skeptical of the model.""",
    5: """### Score filter comparison: baseline vs filtered trades
- **Baseline**: all OOF trades (unfiltered)
- **score >= 0.6**: only trades the model is confident about (top 60%)

Does filtering improve win rate? Total return? Or does it just reduce trade count?
If filtering helps, you have an edge worth using. If not, skip the ML layer.""",
})

# 07_tearsheet: 4 code cells
enrich_notebook(nb_dir / '07_tearsheet.ipynb', {
    0: """### Rank all runs in the results store
`results/runs.parquet` has every backtest we ran: baseline, conditioned, swept, walked-forward.
Sort by Sharpe and show the best performers. This is the final summary.""",
    1: """### Reload and re-run the best config
Pull the parameters from the best run, re-execute it end-to-end.
Generate its quantstats HTML tearsheet: Sharpe, sortino, max DD, monthly returns, etc.""",
    2: """### Regime breakdown: how does the strategy perform by VIX regime?
Split trades by VIX-rank quartile at entry:
- Low vol (bottom 25%): how did we do?
- High vol (top 25%): how did we do?

This tells you if the strategy is vol-dependent or works across regimes.""",
    3: """### Final equity curve
The cumulative return over time for the best strategy on the full backtest window.
Look for:
- Monotonic growth (good) vs. saw-tooth or drawdowns (rough)
- When did the largest DD occur? (market regime change?)
- Does it keep working in recent years?""",
})

print("\nAll notebooks enriched with step-by-step explanations.")
