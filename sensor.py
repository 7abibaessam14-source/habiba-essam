"""
sensor.py - reads the ESP32 + ACS712 readings from the Supabase table "sensor_readings".

The ESP32 adds one row every 5 seconds with the PUBLISHABLE key (it may only insert).
The website reads them here with the SECRET key from [supabase] in the Streamlit secrets (store.supabase_config),
so the readings stay private. Table: hardware/sensor_setup.sql.
"""

from datetime import datetime, timedelta, timezone

import pandas as pd
import requests

import store

OFFLINE_AFTER_S = 30  # no row for 30 s (the ESP32 sends every 5 s) = device offline
COLUMNS = ["created_at", "device_id", "current_a", "peak_a", "threshold_a", "over_threshold", "samples", "rssi_dbm"]


def fetch_readings(hours=1, cfg=None, now=None, timeout=15):
    """(DataFrame of the last `hours` of readings, oldest first, error text or None)."""
    cfg = cfg or store.supabase_config()
    if not cfg:
        return pd.DataFrame(columns=COLUMNS), "Supabase is not set up ([supabase] url and key in the secrets)."
    url, key = cfg
    since = (now or datetime.now(timezone.utc)) - timedelta(hours=hours)
    try:
        r = requests.get(
            f"{url}/rest/v1/sensor_readings",
            params={"select": ",".join(COLUMNS), "created_at": f"gte.{since.isoformat(timespec='seconds')}",
                    "order": "created_at.asc", "limit": "5000"},
            headers=store._headers(key), timeout=timeout,
        )
    except requests.RequestException:
        return pd.DataFrame(columns=COLUMNS), "Could not reach the database."
    if r.status_code == 404:
        return pd.DataFrame(columns=COLUMNS), "The table sensor_readings does not exist yet: run hardware/sensor_setup.sql in Supabase."
    if r.status_code != 200:
        return pd.DataFrame(columns=COLUMNS), f"The database answered HTTP {r.status_code}."
    try:
        return to_frame(r.json()), None
    except (ValueError, KeyError, TypeError):
        return pd.DataFrame(columns=COLUMNS), "The database sent data in an unexpected form."


def to_frame(rows):
    df = pd.DataFrame(rows, columns=COLUMNS)
    df["created_at"] = pd.to_datetime(df["created_at"], utc=True, format="ISO8601")
    for c in ("current_a", "peak_a", "threshold_a"):
        df[c] = df[c].astype(float)
    df["over_threshold"] = df["over_threshold"].astype(bool)
    return df.sort_values("created_at").reset_index(drop=True)


def summary(df, now=None, volts=12.0):
    """The numbers shown at the top of the page. Watts are an ESTIMATE: current x the supply voltage typed in."""
    now = now or datetime.now(timezone.utc)
    if df.empty:
        return {"has_data": False, "online": False}
    last = df.iloc[-1]
    age_s = (pd.Timestamp(now) - last["created_at"]).total_seconds()
    return {
        "has_data": True,
        "online": age_s <= OFFLINE_AFTER_S,
        "age_s": max(age_s, 0.0),
        "device_id": last["device_id"],
        "current_a": float(last["current_a"]),
        "peak_a": float(last["peak_a"]),
        "threshold_a": float(last["threshold_a"]),
        "over": bool(last["over_threshold"]),
        "watts": float(last["current_a"]) * volts,
        "avg_a": float(df["current_a"].mean()),
        "max_a": float(df["peak_a"].max()),
        "alerts": int(df["over_threshold"].sum()),
        "rssi_dbm": None if pd.isna(last["rssi_dbm"]) else int(last["rssi_dbm"]),
        "rows": len(df),
    }


def age_text(seconds):
    s = int(seconds)
    if s < 60:
        return f"{s} s ago"
    if s < 3600:
        return f"{s // 60} min ago"
    if s < 86400:
        return f"{s // 3600} h ago"
    return f"{s // 86400} days ago"
