"""Tests for pipeline.py on a small made-up raw file (no data folder needed). Run: py test_pipeline_local.py"""
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

import pipeline

passed = failed = 0


def check(name, ok):
    global passed, failed
    print(("PASS  " if ok else "FAIL  ") + name)
    passed, failed = passed + ok, failed + (not ok)


def make_raw(folder, days=3):
    """A UCI-style file: one reading per minute, ';' separated, with a few '?' and one bad date."""
    idx = pd.date_range("2008-01-01", periods=days * 1440, freq="min")
    rng = np.random.default_rng(0)
    power = np.round(rng.uniform(0.2, 3.0, len(idx)), 3).astype(object)
    power[100:110] = "?"  # 10 missing readings
    df = pd.DataFrame({
        "Date": idx.strftime("%d/%m/%Y"), "Time": idx.strftime("%H:%M:%S"),
        "Global_active_power": power, "Global_reactive_power": 0.1, "Voltage": 240.0, "Global_intensity": 5.0,
        "Sub_metering_1": 1.0, "Sub_metering_2": 2.0, "Sub_metering_3": 3.0,
    })
    df.loc[500, "Date"] = "31/02/2008"  # a date that does not exist
    df.to_csv(Path(folder) / "household_power_consumption.txt", sep=";", index=False)
    return len(df)


with tempfile.TemporaryDirectory() as tmp:
    raw_dir, out_dir = Path(tmp) / "data", Path(tmp) / "curated"
    raw_dir.mkdir()

    ok, msg = pipeline.check_raw(raw_dir)
    check("stage 1 fails when the raw zone is empty", not ok and "no raw file" in msg)

    pd.DataFrame({"Date": ["01/01/2008"], "Time": ["00:00:00"]}).to_csv(
        raw_dir / "household_power_consumption.txt", sep=";", index=False)
    ok, msg = pipeline.check_raw(raw_dir)
    check("stage 1 fails when columns are missing", not ok and "missing columns" in msg)

    n = make_raw(raw_dir)
    ok, msg = pipeline.check_raw(raw_dir)
    check("stage 1 passes on a good raw file", ok)

    check("stage 3 fails before the transform has run", pipeline.check_curated(out_dir) != [])

    s = pipeline.transform(raw_dir, out_dir)
    check("all raw rows are read", s["raw_rows"] == n)
    check("the 10 '?' readings are counted as nulls", s["nulls"]["Global_active_power"] == 10)
    check("the impossible date is counted", s["nulls"]["bad_timestamp"] == 1)
    check("removed rows = nulls + bad date", s["removed_rows"] == 11)
    check("3 days give 144 half-hour rows", s["cleaned_rows"] == 144)
    check("3 daily totals", s["days"] == 3)
    check("all curated files written", all((out_dir / f).exists() for f in pipeline.OUTPUTS))

    cleaned = pd.read_csv(out_dir / "cleaned_power_data.csv")
    check("no empty kWh in cleaned data", cleaned["KWH_hh"].notna().all())
    avg = pd.read_csv(out_dir / "avg_consumption.csv")
    check("average per household matches the cleaned data",
          abs(avg["avg_consumption"].iloc[0] - cleaned["KWH_hh"].mean()) < 1e-9)
    profile = pd.read_csv(out_dir / "hourly_profile.csv")
    check("hourly profile has 24 hours", profile["hour_of_day"].nunique() == 24)

    check("stage 3 passes on good output", pipeline.check_curated(out_dir) == [])

    (out_dir / "avg_consumption.csv").unlink()
    check("stage 3 notices a missing file", any("avg_consumption.csv is missing" in p
                                                for p in pipeline.check_curated(out_dir)))
    pipeline.transform(raw_dir, out_dir)
    d = pd.read_csv(out_dir / "daily_consumption.csv")
    d.loc[0, "kwh"] = None
    d.to_csv(out_dir / "daily_consumption.csv", index=False)
    check("stage 3 notices empty values", any("empty values" in p for p in pipeline.check_curated(out_dir)))

print(f"\n{passed} passed, {failed} failed")
print("RESULT:", "PASS" if failed == 0 else "FAIL")
