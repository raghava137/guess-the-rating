"""Population stability index (PSI): how far live inputs have moved from the training data.

Training saves decile bin edges and expected shares per feature; the backend compares the
latest requests against them. Rule of thumb: PSI below 0.1 is stable, above 0.2 is drift.
"""

import numpy as np

DRIFT_FEATURES = ["acpl", "mean_move_time", "plies"]
BINS = 10


def make_reference(df):
    """Decile edges and expected bin shares for each drift feature."""
    reference = {}
    for feature in DRIFT_FEATURES:
        values = df[feature].dropna().to_numpy()
        edges = np.unique(np.quantile(values, np.linspace(0, 1, BINS + 1)[1:-1]))
        counts = np.histogram(values, bins=[-np.inf, *edges, np.inf])[0]
        reference[feature] = {"edges": edges.tolist(), "expected": (counts / counts.sum()).tolist()}
    return reference


def psi(feature_reference, values, eps=1e-4):
    """PSI between the reference bins and a list of new values (NaN ignored)."""
    values = [v for v in values if v == v]
    if not values:
        return float("nan")
    counts = np.histogram(values, bins=[-np.inf, *feature_reference["edges"], np.inf])[0]
    actual = np.clip(counts / counts.sum(), eps, None)
    expected = np.clip(np.array(feature_reference["expected"]), eps, None)
    return float(np.sum((actual - expected) * np.log(actual / expected)))
