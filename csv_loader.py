"""
csv_loader.py - read the smart-meter CSV files straight from the `data` folder.

Works with both layouts of the London smart-meter data (column names are matched
loosely, case and spaces do not matter):
    repo / LCL-June2015v2_*.csv :  LCLid, stdorToU, DateTime, KWH/hh (per half hour)
    Kaggle halfhourly_dataset   :  LCLid, tstp, energy(kWh/hh)

Returns the same three tables the app normally gets from Snowflake:
    fact (LCLid, DateTime, KWH_hh, date_only, hour_of_day, day_of_week, is_weekend, day_type),
    agg (LCLid, avg_consumption), households (LCLid)
"""

from pathlib import Path

import numpy as np
import pandas as pd

ALIASES = {
    "LCLid": ["lclid", "lcl_id"],
    "DateTime": ["datetime", "tstp", "timestamp"],
    "KWH_hh": ["kwh/hh (per half hour)", "energy(kwh/hh)", "kwh_hh", "kwh/hh", "energy"],
}


def csv_files(folder):
    return sorted(Path(folder).glob("*.csv")) if Path(folder).is_dir() else []


def csv_signature(folder):
    """Changes whenever a CSV is added, removed or edited (used as a cache key)."""
    return tuple((f.name, f.stat().st_size, int(f.stat().st_mtime)) for f in csv_files(folder))


def _read_one(path):
    df = pd.read_csv(path, low_memory=False)
    lookup = {c.strip().lower(): c for c in df.columns}
    chosen = {}
    for target, options in ALIASES.items():
        for opt in options:
            if opt in lookup:
                chosen[target] = lookup[opt]
                break
        else:
            raise ValueError(
                f"{Path(path).name}: could not find the '{target}' column. Columns found: {list(df.columns)}"
            )
    out = pd.DataFrame(
        {
            "LCLid": df[chosen["LCLid"]].astype(str).str.strip(),
            "DateTime": pd.to_datetime(df[chosen["DateTime"]], errors="coerce"),
            "KWH_hh": pd.to_numeric(df[chosen["KWH_hh"]], errors="coerce"),
        }
    )
    return out.dropna()


def load_csv_folder(folder, max_households=60, max_files=None):
    """
    Returns (fact, agg, households), or None when the folder has no CSV files.
    max_households keeps the dashboard fast on very large datasets (first N ids, sorted).
    """
    files = csv_files(folder)
    if not files:
        return None
    if max_files:
        files = files[:max_files]

    fact = pd.concat([_read_one(f) for f in files], ignore_index=True)
    if max_households:
        keep = sorted(fact["LCLid"].unique())[:max_households]
        fact = fact[fact["LCLid"].isin(keep)]
    fact = fact.drop_duplicates(["LCLid", "DateTime"]).sort_values(["LCLid", "DateTime"]).reset_index(drop=True)

    fact["date_only"] = fact["DateTime"].dt.normalize()
    fact["hour_of_day"] = fact["DateTime"].dt.hour
    fact["day_of_week"] = fact["DateTime"].dt.dayofweek
    fact["is_weekend"] = (fact["day_of_week"] >= 5).astype(int)
    fact["day_type"] = np.where(fact["is_weekend"] == 1, "Weekend", "Weekday")

    agg = fact.groupby("LCLid")["KWH_hh"].mean().reset_index().rename(columns={"KWH_hh": "avg_consumption"})
    households = pd.DataFrame({"LCLid": sorted(fact["LCLid"].unique())})
    return fact, agg, households
