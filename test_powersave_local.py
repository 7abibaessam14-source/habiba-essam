"""Local test for powersave.py (no internet): fake Supabase answers. Run: py test_powersave_local.py"""

from datetime import datetime, timedelta, timezone
from unittest import mock

import powersave as ps

fails = 0


def check(name, ok):
    global fails
    print(("PASS " if ok else "FAIL ") + name)
    fails += not ok


class Resp:
    def __init__(self, code, data=None):
        self.status_code, self._data = code, data

    def json(self):
        return self._data


NOW = datetime(2026, 11, 13, 12, 0, 0, tzinfo=timezone.utc)
CFG = ("https://example.supabase.co", "sb_secret_test")


def ev(min_ago, event, a=3.5):
    return {"created_at": (NOW - timedelta(minutes=min_ago)).isoformat(), "device_id": "esp32-lab-1", "event": event, "current_a": a}


# settings: missing row -> defaults (off); saved row -> its values
with mock.patch("powersave.requests.get", return_value=Resp(200, [])):
    s, err = ps.load_settings("esp32-lab-1", cfg=CFG)
check("no settings row -> power save off", err is None and s["enabled"] is False and s["limit_a"] == 3.0)
with mock.patch("powersave.requests.get", return_value=Resp(200, [{"enabled": True, "limit_a": 2.5, "after_min": 5, "warn_s": 30, "lamp_w": 21}])):
    s, err = ps.load_settings("esp32-lab-1", cfg=CFG)
check("saved settings are read", s["enabled"] and s["limit_a"] == 2.5 and s["warn_s"] == 30)
with mock.patch("powersave.requests.get", return_value=Resp(404, {})):
    s, err = ps.load_settings("esp32-lab-1", cfg=CFG)
check("missing table -> clear message", "power_save_setup.sql" in err and s["enabled"] is False)

# saving sends one upsert row with the right values
with mock.patch("powersave.requests.post", return_value=Resp(201)) as p:
    ok, msg = ps.save_settings("esp32-lab-1", True, 3, 10, 60, 21, cfg=CFG)
    row, headers = p.call_args.kwargs["json"], p.call_args.kwargs["headers"]
check("save ok", ok and msg == "Saved.")
check("upsert row", row["device_id"] == "esp32-lab-1" and row["enabled"] is True and row["warn_s"] == 60
      and "merge-duplicates" in headers["Prefer"])
with mock.patch("powersave.requests.post", return_value=Resp(201)) as p:
    ps.cancel("esp32-lab-1", 10, cfg=CFG, now=NOW)
    snooze = p.call_args.kwargs["json"]["snooze_until"]
check("cancel snoozes 10 min", snooze == "2026-11-13T12:10:00+00:00")
with mock.patch("powersave.requests.post", side_effect=ps.requests.ConnectionError()):
    ok, msg = ps.restore("esp32-lab-1", cfg=CFG)
check("no internet -> message, no crash", not ok and "reach" in msg)

# status
on = {**ps.DEFAULTS, "enabled": True, "warn_s": 60}
empty = ps.events_frame([])
check("off when disabled", ps.status(empty, ps.DEFAULTS, NOW)["state"] == "off")
check("watching when enabled", ps.status(empty, on, NOW)["state"] == "watching")
w = ps.status(ps.events_frame([{**ev(0, "warning"), "created_at": (NOW - timedelta(seconds=20)).isoformat()}]), on, NOW)
check("warning with 40 s left", w["state"] == "warning" and w["seconds_left"] == 40)
check("old warning ignored", ps.status(ps.events_frame([ev(5, "warning")]), on, NOW)["state"] == "watching")
check("lamp off shown even if mode disabled", ps.status(ps.events_frame([ev(3, "turned_off")]), ps.DEFAULTS, NOW)["state"] == "turned_off")
check("restored -> watching", ps.status(ps.events_frame([ev(9, "turned_off"), ev(3, "restored")]), on, NOW)["state"] == "watching")

# savings: off 30 min then restored, off again for the last 15 min; 21 W lamp -> 21 * 45/60 = 15.75 Wh
evs = ps.events_frame([ev(90, "warning"), ev(89, "turned_off"), ev(59, "restored"), ev(15, "turned_off"), ev(40, "would_turn_off")])
check("saved Wh", abs(ps.saved_wh(evs, 21, NOW) - 15.75) < 1e-6)
check("would_turn_off saves nothing", ps.saved_wh(ps.events_frame([ev(30, "would_turn_off")]), 21, NOW) == 0)

with mock.patch("powersave.requests.get", return_value=Resp(200, [ev(1, "restored"), ev(5, "turned_off")])) as g:
    df, err = ps.load_events("esp32-lab-1", 24, cfg=CFG, now=NOW)
check("events sorted oldest first", err is None and list(df["event"]) == ["turned_off", "restored"])

print("RESULT:", "FAIL" if fails else "PASS", f"({fails} failed)")
