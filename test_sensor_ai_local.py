"""Local test for sensor_ai.py with sample data (no internet, no hardware). Run: py test_sensor_ai_local.py"""

import sys

import pandas as pd

import sensor_ai as ai

sys.stdout.reconfigure(encoding="utf-8")
fails = 0


def check(name, ok):
    global fails
    print(("PASS " if ok else "FAIL ") + name)
    fails += not ok


END = pd.Timestamp("2026-11-16 10:00", tz="UTC")
r = ai.make_demo_readings(days=14, end=END)
check("sample data has readings", len(r) > 10000 and {"created_at", "current_a", "peak_a"} <= set(r.columns))

st = ai.steps(r)
check("finds switch-on and switch-off jumps", any(s["delta_a"] > 0 for s in st) and any(s["delta_a"] < 0 for s in st))
check("lamp jump is about 1.75 A", any(abs(s["delta_a"] - 1.75) < 0.1 for s in st))
check("fan start has a motor spike", any(s["inrush"] and abs(s["delta_a"] - 0.25) < 0.1 for s in st))

name, _ = ai.match(1.72, False, ai.DEFAULT_DEVICES)
check("1.72 A -> lamp", name.startswith("Lamp"))
name, _ = ai.match(0.26, True, ai.DEFAULT_DEVICES)
check("0.26 A with spike -> fan", name.startswith("Fan"))
check("3.0 A -> unknown", ai.match(3.0, False, ai.DEFAULT_DEVICES)[0] is None)

rec = ai.recognize(r)
ev = rec["events"]
known = ev[ev["device"] != "Unknown device"]
check("most jumps are recognised", len(known) / max(len(ev), 1) >= 0.85)
print(f"    recognised {len(known)} of {len(ev)} jumps ({len(known) / max(len(ev), 1):.0%})")
check("usage table per device", set(rec["usage"]["device"]) == {d["name"] for d in ai.DEFAULT_DEVICES})
lamp_hours = rec["usage"].set_index("device").loc["Lamp (12 V 21 W)", "hours_on"]
check("lamp on about 5.5 h a day x 14 days", 60 < lamp_hours < 95)

an = ai.anomalies(r, rec=rec)
check("finds the lamp left on at night", any(a["kind"] == "on at night" for a in an))
for a in an[:3]:
    print("   ", a["kind"], "-", a["message"])

mt = {m["device"]: m for m in ai.maintenance(r)}
fan = mt.get("Fan (12 V)")
check("fan flagged: rising current (tired motor)", fan is not None and fan["status"] == "check" and fan["change_per_month"] > 0.1)
check("lamp is steady", mt["Lamp (12 V 21 W)"]["status"] == "ok")
check("LED strip noise is not a false alarm", mt["LED strip"]["status"] == "ok")
check("no false alarm for any end time", all(m["status"] == "ok" for h in (0, 7, 15)
                                              for m in ai.maintenance(ai.make_demo_readings(end=END - pd.Timedelta(hours=h)))
                                              if not m["device"].startswith("Fan")))
print("    fan:", fan["message"] if fan else None)
check("few days -> still learning", all(m["status"] == "learning" for m in ai.maintenance(r[r["created_at"] > END - pd.Timedelta(days=2)])))

occ = ai.occupancy(r)
check("occupancy gives a state", occ["state"] in {"home", "away", "forgotten", "unknown"})
quiet = r[r["created_at"] < END - pd.Timedelta(hours=10)].copy()
quiet.loc[quiet.index[-300:], "current_a"] = 1.75  # lamp on with no change for 5 hours
check("lamp on for hours with no change -> forgotten", ai.occupancy(quiet)["state"] == "forgotten")

new = ai.learn_from_last_step(r, "My lamp")
check("learn a device from the last switch-on", new is not None and new["name"] == "My lamp" and new["delta_a"] > 0)
check("empty readings do not crash", ai.recognize(r.iloc[:0])["on_now"] == [] and ai.occupancy(r.iloc[:0])["state"] == "unknown")
print("RESULT:", "FAIL" if fails else "PASS", f"({fails} failed)")
