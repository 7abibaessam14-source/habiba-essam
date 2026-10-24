"""
demo_data.py - FAKE data in the same shape as the Snowflake tables.

app.py uses this automatically when it cannot connect to Snowflake, so you can
see every tab working. Household ids start with DEMO so it is obvious the data
is not real. The last part of the period contains a few injected spikes so the
Anomalies and Alerts tabs have something to find.
"""

import numpy as np
import pandas as pd


def make_demo_frames(n_households=5, days=120, seed=42):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2013-01-01", periods=days * 48, freq="30min")
    hour = np.asarray(idx.hour + idx.minute / 60)
    weekend = np.asarray(idx.dayofweek >= 5)

    frames = []
    for i in range(n_households):
        scale = 0.7 + 0.2 * i
        base = scale * (
            0.15
            + 0.30 * np.exp(-((hour - 19) ** 2) / 4)  # evening peak
            + 0.18 * np.exp(-((hour - 7.5) ** 2) / 2)  # morning peak
            + 0.06 * weekend
        )
        kwh = np.clip(base + rng.normal(0, 0.03 * scale, len(idx)), 0, None)

        start = int(len(idx) * 0.85)  # spikes only in the last 15%
        pos = rng.choice(np.arange(start, len(idx) - 2), 3, replace=False)
        kwh[pos] += 2.0

        frames.append(pd.DataFrame({"LCLid": f"DEMO{i + 1:03d}", "DateTime": idx, "KWH_hh": kwh}))

    fact = pd.concat(frames, ignore_index=True)
    fact["date_only"] = fact["DateTime"].dt.normalize()
    fact["hour_of_day"] = fact["DateTime"].dt.hour
    fact["day_of_week"] = fact["DateTime"].dt.dayofweek
    fact["is_weekend"] = (fact["day_of_week"] >= 5).astype(int)
    fact["day_type"] = np.where(fact["is_weekend"] == 1, "Weekend", "Weekday")

    agg = (
        fact.groupby("LCLid")["KWH_hh"].mean().reset_index().rename(columns={"KWH_hh": "avg_consumption"})
    )
    households = pd.DataFrame({"LCLid": sorted(fact["LCLid"].unique())})
    return fact, agg, households
