"""ML meta-labeling: the strategy proposes trades, a tree model filters them.

Pipeline: raw candidate trades -> join entry-day features -> LightGBM
classifier (win / lose) evaluated with purged, embargoed time-series CV so
overlapping trades never leak between train and test -> SHAP importances ->
score-threshold filter compared against the unfiltered baseline.
"""

from dataclasses import dataclass
from typing import Iterator, Optional

import numpy as np
import pandas as pd

from .backtest import StrategyConfig, load_features, run_raw_trades
from .features import ML_FEATURES

TRADE_FEATURES = ["dte_entry", "abs_delta_entry"]


# ---------------------------------------------------------------------------
# Dataset
# ---------------------------------------------------------------------------


def build_dataset(
    config: StrategyConfig,
    features: Optional[pd.DataFrame] = None,
) -> pd.DataFrame:
    """One row per candidate trade: entry-day market features + trade
    descriptors + labels (win, pct_change) + dates needed for purging."""
    if features is None:
        features = load_features()
    trades = run_raw_trades(config)

    ds = trades.merge(
        features, left_on="quote_date_entry", right_on="quote_date", how="inner"
    )
    ds["abs_delta_entry"] = ds["delta_entry"].abs()
    exit_dte = config.params.get("exit_dte", 0) or 0
    hold_days = (ds["dte_entry"] - exit_dte).clip(lower=1)
    ds["entry_date"] = ds["quote_date_entry"]
    ds["exit_date"] = ds["entry_date"] + pd.to_timedelta(hold_days, unit="D")
    ds["win"] = (ds["pct_change"] > 0).astype(int)
    keep = (
        ["entry_date", "exit_date", "strike", "pct_change", "win"]
        + TRADE_FEATURES + ML_FEATURES
    )
    return ds[keep].dropna(subset=ML_FEATURES).sort_values("entry_date", ignore_index=True)


# ---------------------------------------------------------------------------
# Purged time-series cross-validation
# ---------------------------------------------------------------------------


class PurgedTimeSeriesSplit:
    """Walk-forward CV for overlapping-label data (Lopez de Prado style).

    Test folds are contiguous blocks in entry-date order. Training samples
    are strictly earlier trades whose *exit* falls at least `embargo_days`
    before the test block starts — so no open position spans the boundary.
    """

    def __init__(self, n_splits: int = 5, embargo_days: int = 5):
        self.n_splits = n_splits
        self.embargo_days = embargo_days

    def split(self, ds: pd.DataFrame) -> Iterator[tuple[np.ndarray, np.ndarray]]:
        dates = ds["entry_date"].to_numpy()
        # n_splits test blocks over the latter part of the sample; the first
        # block still needs earlier data to train on, so use n_splits+1 chunks
        edges = pd.Series(dates).quantile(
            np.linspace(0, 1, self.n_splits + 2)
        ).to_numpy()
        for i in range(1, self.n_splits + 1):
            test_start, test_end = edges[i], edges[i + 1]
            test_idx = np.where((dates >= test_start) & (dates <= test_end))[0]
            cutoff = test_start - np.timedelta64(self.embargo_days, "D")
            train_idx = np.where(ds["exit_date"].to_numpy() < cutoff)[0]
            if len(train_idx) and len(test_idx):
                yield train_idx, test_idx


# ---------------------------------------------------------------------------
# Training / evaluation
# ---------------------------------------------------------------------------

LGB_DEFAULTS = dict(
    n_estimators=300, learning_rate=0.05, num_leaves=15, min_child_samples=50,
    subsample=0.8, colsample_bytree=0.8, random_state=42, verbosity=-1,
)


@dataclass
class MetaLabelResult:
    dataset: pd.DataFrame        # with out-of-fold `score` column (NaN pre-first-fold)
    fold_metrics: pd.DataFrame   # per-fold AUC / hit rates
    model: "lightgbm.LGBMClassifier"  # noqa: F821 — fit on all data, for SHAP

    def apply_filter(self, threshold: float = 0.5) -> pd.DataFrame:
        """Baseline vs score-filtered comparison on out-of-fold trades only."""
        oof = self.dataset.dropna(subset=["score"])
        picked = oof[oof["score"] >= threshold]
        def stats(t: pd.DataFrame, label: str) -> dict:
            return {
                "trades": len(t),
                "win_rate": t["win"].mean(),
                "avg_pct_change": t["pct_change"].mean(),
                "total_pct_change": t["pct_change"].sum(),
                "worst_trade": t["pct_change"].min(),
                "subset": label,
            }
        return pd.DataFrame([stats(oof, "baseline"), stats(picked, f"score>={threshold}")]
                            ).set_index("subset")


def train_metalabel(
    ds: pd.DataFrame,
    n_splits: int = 5,
    embargo_days: int = 5,
    params: Optional[dict] = None,
) -> MetaLabelResult:
    from lightgbm import LGBMClassifier
    from sklearn.metrics import roc_auc_score

    feature_cols = TRADE_FEATURES + ML_FEATURES
    X, y = ds[feature_cols], ds["win"]
    ds = ds.copy()
    ds["score"] = np.nan
    lgb_params = {**LGB_DEFAULTS, **(params or {})}

    folds = []
    for k, (tr, te) in enumerate(PurgedTimeSeriesSplit(n_splits, embargo_days).split(ds)):
        model = LGBMClassifier(**lgb_params)
        model.fit(X.iloc[tr], y.iloc[tr])
        scores = model.predict_proba(X.iloc[te])[:, 1]
        ds.iloc[te, ds.columns.get_loc("score")] = scores
        folds.append({
            "fold": k,
            "train_n": len(tr),
            "test_n": len(te),
            "test_start": ds["entry_date"].iloc[te].min(),
            "auc": roc_auc_score(y.iloc[te], scores) if y.iloc[te].nunique() > 1 else np.nan,
            "base_win_rate": y.iloc[te].mean(),
            "top_half_win_rate": y.iloc[te][scores >= np.median(scores)].mean(),
        })

    final = LGBMClassifier(**lgb_params).fit(X, y)
    return MetaLabelResult(dataset=ds, fold_metrics=pd.DataFrame(folds), model=final)


def shap_importance(result: MetaLabelResult, max_display: int = 15):
    """SHAP beeswarm for the full-sample model. Returns the Explanation."""
    import shap

    feature_cols = TRADE_FEATURES + ML_FEATURES
    explainer = shap.TreeExplainer(result.model)
    values = explainer(result.dataset[feature_cols])
    if len(values.shape) == 3:  # binary classifier -> take positive class
        values = values[:, :, 1]
    shap.plots.beeswarm(values, max_display=max_display)
    return values


def leakage_check(ds: pd.DataFrame, n_splits: int = 5, seed: int = 0) -> float:
    """Shuffled labels must score ~0.5 AUC under the same CV, else leakage."""
    shuffled = ds.copy()
    shuffled["win"] = (
        shuffled["win"].sample(frac=1, random_state=seed).reset_index(drop=True)
    )
    res = train_metalabel(shuffled, n_splits=n_splits)
    return float(res.fold_metrics["auc"].mean())
