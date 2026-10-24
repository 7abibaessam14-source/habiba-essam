"""
ml_models.py - forecasting + anomaly detection for the LCL power-consumption data.

Input schema (same as FACT_CONSUMPTION in Snowflake / cleaned_power_data in the lake):
    LCLid (str), DateTime (timestamp), KWH_hh (float, kWh per half hour)

What this module does:
  1. to_hourly()            half-hourly rows  ->  one regular hourly series
  2. add_features()         calendar + lag + rolling features (no data leakage)
  3. train_and_compare()    Random Forest vs XGBoost vs a naive baseline, time-based split
  4. detect_anomalies()     residual z-score (robust) + Isolation Forest
  5. run_pipeline()         all of the above in one call

IMPORTANT: run detect_anomalies() on data the model did NOT train on (the test
period, or new data arriving later). On training data the model has seen the
answers, residuals are artificially small and nothing looks anomalous.
"""

import warnings

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest, RandomForestRegressor
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score

try:
    from xgboost import XGBRegressor

    HAS_XGB = True
except ImportError:  # xgboost is in requirements.txt; this keeps the module importable without it
    HAS_XGB = False

FEATURES = [
    "hour",
    "dayofweek",
    "month",
    "is_weekend",
    "lag_1",
    "lag_2",
    "lag_24",
    "lag_168",
    "roll_mean_24",
    "roll_std_24",
]


# --------------------------------------------------------------------------
# 1) half-hourly rows -> hourly series
# --------------------------------------------------------------------------
def to_hourly(df, household=None):
    """
    Returns a regular hourly pd.Series (kWh per hour).

    household=None  -> "average household": mean of all households per half hour,
                       then summed to hours. Robust when some meters miss readings.
    household="MAC000002" -> that single household.

    An hour needs both half-hour readings, otherwise it is NaN. Gaps of up to
    3 hours are linearly interpolated; longer gaps stay NaN and are dropped
    later, so lag features never silently jump across a hole.
    """
    d = df[["LCLid", "DateTime", "KWH_hh"]].copy()
    d["DateTime"] = pd.to_datetime(d["DateTime"])
    d["KWH_hh"] = pd.to_numeric(d["KWH_hh"], errors="coerce")

    if household is not None:
        s = d[d["LCLid"] == household].groupby("DateTime")["KWH_hh"].sum()
    else:
        s = d.groupby("DateTime")["KWH_hh"].mean()

    s = s.sort_index()
    hourly = s.resample("h").sum(min_count=2)
    hourly = hourly.interpolate(limit=3)
    hourly.name = "kwh"
    return hourly


# --------------------------------------------------------------------------
# 2) features
# --------------------------------------------------------------------------
def add_features(hourly):
    """Calendar features + lags. Every lag/rolling feature uses only the past."""
    df = hourly.to_frame("kwh")
    idx = df.index
    df["hour"] = idx.hour
    df["dayofweek"] = idx.dayofweek
    df["month"] = idx.month
    df["is_weekend"] = (idx.dayofweek >= 5).astype(int)

    df["lag_1"] = df["kwh"].shift(1)
    df["lag_2"] = df["kwh"].shift(2)
    df["lag_24"] = df["kwh"].shift(24)  # same hour yesterday
    df["lag_168"] = df["kwh"].shift(168)  # same hour last week
    past = df["kwh"].shift(1)
    df["roll_mean_24"] = past.rolling(24).mean()
    df["roll_std_24"] = past.rolling(24).std()
    return df.dropna()


# --------------------------------------------------------------------------
# 3) train + compare
# --------------------------------------------------------------------------
def _metrics(y_true, y_pred):
    return {
        "MAE": float(mean_absolute_error(y_true, y_pred)),
        "RMSE": float(np.sqrt(mean_squared_error(y_true, y_pred))),
        "R2": float(r2_score(y_true, y_pred)),
    }


def train_and_compare(feat_df, test_frac=0.2, model_path=None):
    """
    Time-based split (never shuffle a time series). Trains Random Forest and
    XGBoost, compares them with a naive "same hour yesterday" baseline, and
    keeps the model with the lowest RMSE.
    """
    split = int(len(feat_df) * (1 - test_frac))
    train, test = feat_df.iloc[:split], feat_df.iloc[split:]

    models = {
        "RandomForest": RandomForestRegressor(
            n_estimators=200,
            max_depth=14,
            min_samples_leaf=2,
            n_jobs=-1,
            random_state=42,
        )
    }
    if HAS_XGB:
        models["XGBoost"] = XGBRegressor(
            n_estimators=400,
            learning_rate=0.05,
            max_depth=6,
            subsample=0.8,
            colsample_bytree=0.8,
            random_state=42,
            n_jobs=-1,
        )
    else:
        warnings.warn("xgboost is not installed - comparing Random Forest to the baseline only.")

    rows, fitted = [], {}
    rows.append({"model": "Baseline (same hour yesterday)", **_metrics(test["kwh"], test["lag_24"])})
    for name, model in models.items():
        model.fit(train[FEATURES], train["kwh"])
        rows.append({"model": name, **_metrics(test["kwh"], model.predict(test[FEATURES]))})
        fitted[name] = model

    metrics = pd.DataFrame(rows).sort_values("RMSE").reset_index(drop=True)
    best_name = metrics[metrics["model"].isin(fitted)].iloc[0]["model"]
    best_model = fitted[best_name]

    if model_path:
        joblib.dump({"model": best_model, "name": best_name, "features": FEATURES}, model_path)

    return {
        "metrics": metrics,
        "best_name": best_name,
        "best_model": best_model,
        "train": train,
        "test": test,
    }


def load_model(model_path):
    bundle = joblib.load(model_path)
    return bundle["model"], bundle["name"]


# --------------------------------------------------------------------------
# 4) anomaly detection
# --------------------------------------------------------------------------
def detect_anomalies(feat_df, model, z_thresh=3.5, contamination=0.01, mode="both"):
    """
    Two independent detectors on UNSEEN data:
      - residual : robust z-score (median/MAD) of actual - predicted.
                   Catches "this hour is far from what the model expected".
      - isolation: Isolation Forest on (kwh, hour, dayofweek, residual).
                   Catches unusual combinations the z-score misses.

    mode="both"   -> flag only when both agree (fewer false alarms, default)
    mode="either" -> flag when either fires (more sensitive)
    """
    d = feat_df.copy()
    d["pred"] = model.predict(d[FEATURES])
    d["residual"] = d["kwh"] - d["pred"]

    med = d["residual"].median()
    mad = (d["residual"] - med).abs().median()
    mad = mad if mad > 1e-9 else 1e-9
    d["z"] = 0.6745 * (d["residual"] - med) / mad
    d["flag_residual"] = d["z"].abs() > z_thresh

    iso = IsolationForest(n_estimators=200, contamination=contamination, random_state=42)
    d["flag_iso"] = iso.fit_predict(d[["kwh", "hour", "dayofweek", "residual"]]) == -1

    if mode == "either":
        d["is_anomaly"] = d["flag_residual"] | d["flag_iso"]
    else:
        d["is_anomaly"] = d["flag_residual"] & d["flag_iso"]
    return d


# --------------------------------------------------------------------------
# 4b) forecast the next hours (recursive: each prediction feeds the next lag)
# --------------------------------------------------------------------------
def forecast_next(model, hourly, steps=24):
    """
    Predict the next `steps` hours after the end of `hourly`.
    Recursive forecasting: errors accumulate, so keep the horizon short
    (24 hours is fine, 7 days will drift toward the average pattern).
    """
    h = hourly.loc[: hourly.last_valid_index()].interpolate(limit_direction="both")
    if len(h) < 169:
        raise ValueError("Need at least 169 hours of history to forecast.")

    values = list(h.iloc[-336:].values)
    ts = h.index[-1]
    out_idx, out_val = [], []
    for _ in range(steps):
        ts = ts + pd.Timedelta(hours=1)
        last24 = values[-24:]
        row = {
            "hour": ts.hour,
            "dayofweek": ts.dayofweek,
            "month": ts.month,
            "is_weekend": int(ts.dayofweek >= 5),
            "lag_1": values[-1],
            "lag_2": values[-2],
            "lag_24": values[-24],
            "lag_168": values[-168],
            "roll_mean_24": float(np.mean(last24)),
            "roll_std_24": float(np.std(last24, ddof=1)),
        }
        pred = max(float(model.predict(pd.DataFrame([row])[FEATURES])[0]), 0.0)
        values.append(pred)
        out_idx.append(ts)
        out_val.append(pred)
    return pd.Series(out_val, index=pd.DatetimeIndex(out_idx), name="forecast_kwh")


# --------------------------------------------------------------------------
# 5) everything in one call
# --------------------------------------------------------------------------
def run_pipeline(df, household=None, model_path=None, **anomaly_kwargs):
    hourly = to_hourly(df, household=household)
    feats = add_features(hourly)
    if len(feats) < 500:
        raise ValueError(
            f"Only {len(feats)} usable hourly rows - need at least ~500 (about 3 weeks) to train."
        )
    result = train_and_compare(feats, model_path=model_path)
    result["anomalies"] = detect_anomalies(result["test"], result["best_model"], **anomaly_kwargs)
    return result
