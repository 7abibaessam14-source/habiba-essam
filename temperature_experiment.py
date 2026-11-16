"""
temperature_experiment.py - does the outdoor temperature make the forecast better? (run on your PC, once)

    py temperature_experiment.py

The UCI household is in Sceaux, near Paris (Dec 2006 - Nov 2010). This script gets the real hourly temperature of
that place and time from the Open-Meteo archive (free, no key), adds it as one more model input, and trains the same
Random Forest and XGBoost on the same time split with and without it. The result goes to
results/temperature_experiment.json, which the Forecast page shows. The live forecast is NOT changed.
"""

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import requests
from sklearn.ensemble import RandomForestRegressor
from sklearn.metrics import mean_absolute_error, mean_squared_error

from ml_models import FEATURES, add_features, to_hourly
from uci_loader import load_uci, uci_path

try:
    from xgboost import XGBRegressor
except ImportError:
    XGBRegressor = None

HERE = Path(__file__).parent
LAT, LON = 48.7786, 2.2906  # Sceaux, France
TEMP_CACHE = HERE / "data" / "uci_temperature.csv"
OUT = HERE / "results" / "temperature_experiment.json"


def get_temperature(start, end):
    if TEMP_CACHE.exists():
        t = pd.read_csv(TEMP_CACHE, parse_dates=["time"]).set_index("time")["temp"]
        if t.index.min() <= start and t.index.max() >= end:
            return t
    r = requests.get("https://archive-api.open-meteo.com/v1/archive", params={
        "latitude": LAT, "longitude": LON, "start_date": f"{start:%Y-%m-%d}", "end_date": f"{end:%Y-%m-%d}",
        "hourly": "temperature_2m", "timezone": "Europe/Paris"}, timeout=60)
    r.raise_for_status()
    h = r.json()["hourly"]
    t = pd.Series(h["temperature_2m"], index=pd.to_datetime(h["time"]), name="temp").astype(float)
    TEMP_CACHE.parent.mkdir(exist_ok=True)
    t.rename_axis("time").to_frame().to_csv(TEMP_CACHE)
    return t


def models():
    m = {"RandomForest": RandomForestRegressor(n_estimators=200, max_depth=14, min_samples_leaf=2, n_jobs=-1, random_state=42)}
    if XGBRegressor:
        m["XGBoost"] = XGBRegressor(n_estimators=400, learning_rate=0.05, max_depth=6, subsample=0.8,
                                    colsample_bytree=0.8, random_state=42, n_jobs=-1)
    return m


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    fact, _, _ = load_uci(uci_path(HERE / "data"))
    feats = add_features(to_hourly(fact))
    temp = get_temperature(feats.index.min(), feats.index.max())
    feats = feats.join(temp.reindex(feats.index).interpolate(limit=3).rename("temp")).dropna()
    print(f"{len(feats):,} hours with temperature ({feats.index.min():%Y-%m-%d} to {feats.index.max():%Y-%m-%d})")
    print(f"Correlation of hourly kWh with temperature: {feats['kwh'].corr(feats['temp']):+.2f}")

    split = int(len(feats) * 0.8)
    train, test = feats.iloc[:split], feats.iloc[split:]
    rows = []
    for name, _ in models().items():
        for label, cols in (("without temperature", FEATURES), ("with temperature", FEATURES + ["temp"])):
            m = models()[name]
            m.fit(train[cols], train["kwh"])
            p = m.predict(test[cols])
            rows.append({"model": name, "inputs": label,
                         "RMSE": float(np.sqrt(mean_squared_error(test["kwh"], p))), "MAE": float(mean_absolute_error(test["kwh"], p))})
            print(f"{name:13s} {label:20s} RMSE {rows[-1]['RMSE']:.4f}  MAE {rows[-1]['MAE']:.4f}")
    res = pd.DataFrame(rows)
    change = {}
    for name in res["model"].unique():
        a = res[(res.model == name) & (res.inputs == "without temperature")]["RMSE"].iloc[0]
        b = res[(res.model == name) & (res.inputs == "with temperature")]["RMSE"].iloc[0]
        change[name] = (b - a) / a
        print(f"{name}: RMSE {'down' if b < a else 'up'} {abs(change[name]):.1%} with temperature")
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps({
        "place": "Sceaux, France (UCI household)", "source": "Open-Meteo historical weather archive",
        "hours": len(feats), "test_hours": len(test), "corr_kwh_temp": float(feats["kwh"].corr(feats["temp"])),
        "rows": rows, "rmse_change": change}, indent=1), encoding="utf-8")
    print(f"Saved {OUT.relative_to(HERE)}")


if __name__ == "__main__":
    main()
