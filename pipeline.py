"""
pipeline.py - the weekly data pipeline (raw zone -> curated zone), run for free by GitHub Actions
(.github/workflows/pipeline.yml). It follows the same steps as the Spark notebooks
(Milestone2_CSV_Transformation): read raw, validate the schema, check data quality, remove nulls,
store the cleaned data, aggregate per household, store the aggregates, then validate the output.
The Azure Data Factory version of the same pipeline is adf/adf_template.json.

    py pipeline.py check-raw       stage 1: is the raw file there and does it have the right columns?
    py pipeline.py transform       stage 2: clean + aggregate -> curated/
    py pipeline.py check-curated   stage 3: are the curated files complete and valid?
    py pipeline.py all             the three stages in order

Raw zone: data/ (the UCI household file). Curated zone: curated/ (CSV files + _run_summary.json).
"""

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from uci_loader import load_uci, uci_path

RAW_DIR = "data"
CURATED_DIR = "curated"
RAW_COLUMNS = ["Date", "Time", "Global_active_power", "Sub_metering_1", "Sub_metering_2", "Sub_metering_3"]
OUTPUTS = {  # curated file -> columns it must have
    "cleaned_power_data.csv": ["LCLid", "DateTime", "KWH_hh"],
    "avg_consumption.csv": ["LCLid", "avg_consumption"],
    "daily_consumption.csv": ["date", "kwh"],
    "hourly_profile.csv": ["hour_of_day", "day_type", "avg_kwh_hh"],
}
SUMMARY = "_run_summary.json"


def summary_line(text):
    """Print, and add to the GitHub Actions run page when running there."""
    print(text)
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(text + "\n")


def check_raw(raw_dir=RAW_DIR):
    """Stage 1. Returns (ok, message)."""
    path = uci_path(raw_dir)
    if path is None:
        return False, f"no raw file in {raw_dir}/"
    header = pd.read_csv(path, sep=";", nrows=5)
    missing = [c for c in RAW_COLUMNS if c not in header.columns]
    if missing:
        return False, f"{path.name}: missing columns {missing}"
    return True, f"{path.name} found ({path.stat().st_size / 1e6:.1f} MB), all {len(RAW_COLUMNS)} columns present"


def transform(raw_dir=RAW_DIR, out_dir=CURATED_DIR):
    """Stage 2. Writes the curated files and returns the run summary (dict)."""
    path = uci_path(raw_dir)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    # read + schema validation: '?' becomes null, text becomes numbers / timestamps
    raw = pd.read_csv(path, sep=";", na_values=["?", ""], usecols=RAW_COLUMNS, low_memory=False)
    ts = pd.to_datetime(raw["Date"] + " " + raw["Time"], format="%d/%m/%Y %H:%M:%S", errors="coerce")
    power = pd.to_numeric(raw["Global_active_power"], errors="coerce")

    # data quality: nulls per column, then the rows that would be removed
    nulls = {c: int(raw[c].isna().sum()) for c in RAW_COLUMNS}
    nulls["bad_timestamp"] = int(ts.isna().sum())
    valid = int((ts.notna() & power.notna()).sum())

    # cleaning: drop null readings, half-hourly kWh (same shape as the London smart-meter data)
    fact, agg, _ = load_uci(path)
    fact[["LCLid", "DateTime", "KWH_hh", "kitchen_kwh", "laundry_kwh", "heater_ac_kwh", "other_kwh"]].to_csv(
        out / "cleaned_power_data.csv", index=False)

    # aggregation: average per household (as in the notebook), plus daily totals and the hourly profile
    agg.to_csv(out / "avg_consumption.csv", index=False)
    daily = fact.groupby("date_only")["KWH_hh"].sum().rename_axis("date").reset_index(name="kwh")
    daily.to_csv(out / "daily_consumption.csv", index=False)
    profile = (fact.groupby(["hour_of_day", "day_type"])["KWH_hh"].mean()
               .reset_index(name="avg_kwh_hh"))
    profile.to_csv(out / "hourly_profile.csv", index=False)

    summary = {
        "run_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"),
        "raw_file": path.name,
        "raw_rows": int(len(raw)),
        "valid_rows": valid,
        "removed_rows": int(len(raw)) - valid,
        "nulls": nulls,
        "cleaned_rows": int(len(fact)),
        "first_reading": str(fact["DateTime"].min()),
        "last_reading": str(fact["DateTime"].max()),
        "days": int(len(daily)),
        "avg_kwh_hh": round(float(agg["avg_consumption"].iloc[0]), 4),
        "files": {name: int(len(pd.read_csv(out / name, usecols=[0]))) for name in OUTPUTS},
    }
    (out / SUMMARY).write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


def check_curated(out_dir=CURATED_DIR):
    """Stage 3. Returns a list of problems (empty = all good)."""
    out = Path(out_dir)
    problems = []
    if not (out / SUMMARY).exists():
        return [f"{SUMMARY} is missing - did the transform stage run?"]
    summary = json.loads((out / SUMMARY).read_text(encoding="utf-8"))
    for name, columns in OUTPUTS.items():
        if not (out / name).exists():
            problems.append(f"{name} is missing")
            continue
        df = pd.read_csv(out / name)
        if df.empty:
            problems.append(f"{name} is empty")
        if missing := [c for c in columns if c not in df.columns]:
            problems.append(f"{name}: missing columns {missing}")
            continue
        if df[columns].isna().any().any():
            problems.append(f"{name}: has empty values")
        if len(df) != summary["files"].get(name):
            problems.append(f"{name}: {len(df)} rows, the summary says {summary['files'].get(name)}")
    if not problems:
        cleaned = pd.read_csv(out / "cleaned_power_data.csv", parse_dates=["DateTime"])
        if (cleaned["KWH_hh"] < 0).any():
            problems.append("cleaned_power_data.csv: negative kWh")
        if not cleaned["DateTime"].is_monotonic_increasing:
            problems.append("cleaned_power_data.csv: readings are not in time order")
        daily = pd.read_csv(out / "daily_consumption.csv")
        if abs(daily["kwh"].sum() - cleaned["KWH_hh"].sum()) > 0.01 * cleaned["KWH_hh"].sum():
            problems.append("daily totals do not add up to the cleaned data")
    return problems


def main(stage):
    if stage in ("check-raw", "all"):
        ok, msg = check_raw()
        summary_line(f"### 1. Check raw zone\n{'✅' if ok else '❌'} {msg}\n")
        if not ok:
            return 1
    if stage in ("transform", "all"):
        s = transform()
        summary_line("### 2. Transform (clean + aggregate)\n"
                     "| Step | Result |\n|---|---|\n"
                     f"| Raw rows read | {s['raw_rows']:,} |\n"
                     f"| Rows with a missing reading or bad time | {s['removed_rows']:,} |\n"
                     f"| Valid minute readings | {s['valid_rows']:,} |\n"
                     f"| Cleaned half-hour rows | {s['cleaned_rows']:,} |\n"
                     f"| Period | {s['first_reading'][:10]} to {s['last_reading'][:10]} ({s['days']:,} days) |\n"
                     f"| Average kWh per half hour | {s['avg_kwh_hh']} |\n")
        summary_line("Curated files: " + ", ".join(f"`{n}` ({r:,} rows)" for n, r in s["files"].items()) + "\n")
    if stage in ("check-curated", "all"):
        problems = check_curated()
        summary_line("### 3. Check curated zone\n" + ("\n".join(f"❌ {p}" for p in problems) if problems
                                                      else f"✅ all {len(OUTPUTS)} curated files complete and valid") + "\n")
        if problems:
            return 1
    return 0


if __name__ == "__main__":
    stages = ("check-raw", "transform", "check-curated", "all")
    if len(sys.argv) != 2 or sys.argv[1] not in stages:
        sys.exit(f"usage: py pipeline.py {{{'|'.join(stages)}}}")
    sys.exit(main(sys.argv[1]))
