"""Local test for explain.py with made-up data (no internet). Run: py test_explain_local.py"""

import sys

import numpy as np
import pandas as pd

from explain import explain_forecast

sys.stdout.reconfigure(encoding="utf-8")
fails = 0


def check(name, ok):
    global fails
    print(("PASS " if ok else "FAIL ") + name)
    fails += not ok


idx = pd.date_range("2026-10-01", periods=24 * 42, freq="h")  # 6 weeks
base = 0.5 + 0.5 * np.clip(np.sin((idx.hour.values - 6) / 24 * 2 * np.pi), 0, None)
weekend = np.where(idx.dayofweek >= 5, 1.3, 1.0)  # weekends use 30% more
hourly = pd.Series(base * weekend, index=idx)
last = idx[-1]
fidx = pd.date_range(last + pd.Timedelta(hours=1), periods=24, freq="h")
forecast = pd.Series((0.5 + 0.5 * np.clip(np.sin((fidx.hour.values - 6) / 24 * 2 * np.pi), 0, None)) * np.where(fidx.dayofweek >= 5, 1.3, 1.0), index=fidx)
imp = pd.Series([0.6, 0.25, 0.15], index=["lag_24", "hour", "lag_1"])

out = explain_forecast(hourly, forecast, imp)
for r in [out["headline"]] + out["reasons"]:
    print("   ", r)
check("has a headline with kWh", "kWh" in out["headline"])
check("mentions weekend / weekday difference", any("weekend" in r or "weekday" in r for r in out["reasons"]))
check("weekend difference is about 30%", any("30%" in r for r in out["reasons"]))
check("same day last week", any("last week" in r for r in out["reasons"]))
check("busiest hour", any("busiest hour" in r for r in out["reasons"]))
check("model inputs in words", any("the same hour yesterday" in r for r in out["reasons"]))
check("works without importance", "reasons" in explain_forecast(hourly, forecast))
print("RESULT:", "FAIL" if fails else "PASS", f"({fails} failed)")

