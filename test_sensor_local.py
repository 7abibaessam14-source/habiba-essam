"""Local test for sensor.py (no internet): fake Supabase answers. Run: py test_sensor_local.py"""

from datetime import datetime, timedelta, timezone
from unittest import mock

import sensor

fails = 0


def check(name, ok):
    global fails
    print(("PASS " if ok else "FAIL ") + name)
    fails += not ok


NOW = datetime(2026, 11, 9, 16, 0, 0, tzinfo=timezone.utc)


def row(sec_ago, a, peak, over):
    t = (NOW - timedelta(seconds=sec_ago)).isoformat()
    return {"created_at": t, "device_id": "esp32-lab-1", "current_a": a, "peak_a": peak, "threshold_a": 1.0,
            "over_threshold": over, "samples": 2500, "rssi_dbm": -55}


class Resp:
    def __init__(self, code, data):
        self.status_code, self._data = code, data

    def json(self):
        return self._data


CFG = ("https://example.supabase.co", "sb_secret_test")
rows = [row(20, 1.75, 1.9, True), row(15, 0.08, 0.4, False), row(10, 1.72, 1.8, True), row(5, 0.07, 0.3, False)]

with mock.patch("sensor.requests.get", return_value=Resp(200, list(reversed(rows)))) as g:
    df, err = sensor.fetch_readings(1, cfg=CFG, now=NOW)
    params = g.call_args.kwargs["params"]
check("no error", err is None)
check("4 rows, oldest first", len(df) == 4 and df["created_at"].is_monotonic_increasing)
check("asks only for the last hour", params["created_at"] == "gte.2026-11-09T15:00:00+00:00")

s = sensor.summary(df, now=NOW, volts=12)
check("online (last row 5 s old)", s["online"] and round(s["age_s"]) == 5)
check("latest current 0.07 A", abs(s["current_a"] - 0.07) < 1e-6)
check("watts estimate 0.84 W at 12 V", abs(s["watts"] - 0.84) < 1e-6)
check("2 alerts, max 1.9 A", s["alerts"] == 2 and abs(s["max_a"] - 1.9) < 1e-6)

s_old = sensor.summary(df, now=NOW + timedelta(minutes=5))
check("offline after 5 min", not s_old["online"])
check("empty table -> no data", sensor.summary(sensor.to_frame([]), now=NOW) == {"has_data": False, "online": False})

with mock.patch("sensor.requests.get", return_value=Resp(404, {})):
    df, err = sensor.fetch_readings(1, cfg=CFG, now=NOW)
check("missing table -> clear message", df.empty and "sensor_setup.sql" in err)

with mock.patch("sensor.requests.get", side_effect=sensor.requests.ConnectionError()):
    df, err = sensor.fetch_readings(1, cfg=CFG, now=NOW)
check("no internet -> message, no crash", df.empty and "reach" in err)

with mock.patch("sensor.store.supabase_config", return_value=None):
    df, err = sensor.fetch_readings(1, now=NOW)
check("no Supabase settings -> message", df.empty and "not set up" in err)

check("age text", sensor.age_text(5) == "5 s ago" and sensor.age_text(125) == "2 min ago")

print("RESULT:", "FAIL" if fails else "PASS", f"({fails} failed)")
