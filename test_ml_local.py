"""
test_ml_local.py - sanity test on SYNTHETIC data in the LCL schema.

It injects known spikes into the unseen (test) period and checks that the
pipeline finds them. Run:  python test_ml_local.py
Replace make_demo_data() with your real dataframe to run on real data.
"""

import numpy as np
import pandas as pd

from alerts import build_alerts, compute_threshold
from ml_models import HAS_XGB, run_pipeline


def make_demo_data(days=120, n_spikes=6, seed=7):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2012-10-12", periods=days * 48, freq="30min")
    hour = idx.hour + idx.minute / 60
    base = (
        0.15
        + 0.30 * np.exp(-((hour - 19) ** 2) / 4)  # evening peak
        + 0.18 * np.exp(-((hour - 7.5) ** 2) / 2)  # morning peak
        + 0.06 * (idx.dayofweek >= 5)  # weekend bump
    )
    kwh = np.clip(np.asarray(base) + rng.normal(0, 0.03, len(idx)), 0, None)

    # inject spikes into the last 20% (the part the model never trains on)
    start = int(len(idx) * 0.85)
    spike_pos = rng.choice(np.arange(start, len(idx) - 2), n_spikes, replace=False)
    kwh[spike_pos] += 2.0
    spike_hours = {idx[p].floor("h") for p in spike_pos}

    df = pd.DataFrame(
        {
            "LCLid": "MAC000002",
            "stdorToU": "Std",
            "DateTime": idx,
            "KWH_hh": kwh,
        }
    )
    return df, spike_hours


if __name__ == "__main__":
    df, spike_hours = make_demo_data()
    print(f"rows: {len(df):,} | xgboost installed: {HAS_XGB}\n")

    res = run_pipeline(df, household="MAC000002", model_path="best_model.joblib")
    print("Model comparison (lower RMSE is better):")
    print(res["metrics"].round(4).to_string(index=False))
    print(f"\nBest model: {res['best_name']}")

    an = res["anomalies"]
    found = set(an.index[an["is_anomaly"]])
    hit = spike_hours & found
    print(f"\nInjected spikes: {len(spike_hours)} | detected: {len(hit)} | "
          f"total flagged: {len(found)} | false flags: {len(found - spike_hours)}")

    thr = compute_threshold(res["train"]["kwh"])
    alerts = build_alerts(an, household="MAC000002", threshold_kwh=thr)
    print(f"\nThreshold (p99 of training hours): {thr:.2f} kWh | alerts raised: {len(alerts)}")
    print(alerts.head(8).to_string(index=False))
