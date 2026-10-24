"""
uci_loader.py - UCI "Individual Household Electric Power Consumption" (household_power_consumption.txt)

Raw file: one household, one reading per MINUTE, ';' separated, dates as dd/mm/yyyy.
    Global_active_power  kW (minute average)
    Sub_metering_1       Wh per minute - kitchen (dishwasher, oven, microwave)
    Sub_metering_2       Wh per minute - laundry (washing machine, dryer, fridge, light)
    Sub_metering_3       Wh per minute - electric water heater + air conditioner
    "other" = Global_active_power*1000/60 - sub1 - sub2 - sub3   (everything not sub-metered)

Converted here to the same shape the app uses (half-hourly kWh), plus the appliance columns:
    LCLid, DateTime, KWH_hh, kitchen_kwh, laundry_kwh, heater_ac_kwh, other_kwh,
    date_only, hour_of_day, day_of_week, is_weekend, day_type
"""

from pathlib import Path

import numpy as np
import pandas as pd

UCI_FILENAME = "household_power_consumption.txt"
UCI_ZIPNAME = "household_power_consumption.zip"  # the same file zipped (19.5 MB), small enough for GitHub

# column in the fact table -> label shown in the dashboard
APPLIANCE_COLUMNS = {
    "kitchen_kwh": "Kitchen",
    "laundry_kwh": "Laundry",
    "heater_ac_kwh": "Water heater & AC",
    "other_kwh": "Other (not sub-metered)",
}


def uci_path(folder):
    """The UCI file in `folder`: the .txt, else its zipped copy (pandas reads both), else None."""
    for name in (UCI_FILENAME, UCI_ZIPNAME):
        p = Path(folder) / name
        if p.exists():
            return p
    return None


def uci_signature(folder):
    """Changes when the file is added or edited (used as a cache key)."""
    p = uci_path(folder)
    if p is None:
        return ()
    return ((p.name, p.stat().st_size, int(p.stat().st_mtime)),)


def load_uci(path, household_id="HOUSE001", min_minutes=15):
    """
    Returns (fact, agg, households) like the Snowflake loaders.
    A half hour is kept only if at least `min_minutes` of its 30 minutes have a
    reading; the energy is then scaled from the minutes that exist, so the ~1.25%
    missing minutes do not make consumption look lower than it was.
    """
    raw = pd.read_csv(
        path,
        sep=";",
        na_values=["?", ""],
        low_memory=False,
        usecols=["Date", "Time", "Global_active_power", "Sub_metering_1", "Sub_metering_2", "Sub_metering_3"],
    )
    ts = pd.to_datetime(raw["Date"] + " " + raw["Time"], format="%d/%m/%Y %H:%M:%S", errors="coerce")
    d = pd.DataFrame(
        {
            "gap": pd.to_numeric(raw["Global_active_power"], errors="coerce").values,
            "s1": pd.to_numeric(raw["Sub_metering_1"], errors="coerce").values,
            "s2": pd.to_numeric(raw["Sub_metering_2"], errors="coerce").values,
            "s3": pd.to_numeric(raw["Sub_metering_3"], errors="coerce").values,
        },
        index=ts,
    )
    d = d[d.index.notna()].sort_index()

    grp = d.resample("30min")
    mean = grp.mean()
    n_minutes = grp["gap"].count()

    out = pd.DataFrame(
        {
            "KWH_hh": mean["gap"] * 0.5,  # kW x 0.5 h
            "kitchen_kwh": mean["s1"] * 30 / 1000,  # Wh/min x 30 min / 1000
            "laundry_kwh": mean["s2"] * 30 / 1000,
            "heater_ac_kwh": mean["s3"] * 30 / 1000,
        }
    )
    out = out[n_minutes >= min_minutes].dropna()
    out["other_kwh"] = (
        out["KWH_hh"] - out[["kitchen_kwh", "laundry_kwh", "heater_ac_kwh"]].sum(axis=1)
    ).clip(lower=0)

    fact = out.rename_axis("DateTime").reset_index()
    fact.insert(0, "LCLid", household_id)
    fact["date_only"] = fact["DateTime"].dt.normalize()
    fact["hour_of_day"] = fact["DateTime"].dt.hour
    fact["day_of_week"] = fact["DateTime"].dt.dayofweek
    fact["is_weekend"] = (fact["day_of_week"] >= 5).astype(int)
    fact["day_type"] = np.where(fact["is_weekend"] == 1, "Weekend", "Weekday")

    agg = pd.DataFrame({"LCLid": [household_id], "avg_consumption": [fact["KWH_hh"].mean()]})
    households = pd.DataFrame({"LCLid": [household_id]})
    return fact, agg, households
