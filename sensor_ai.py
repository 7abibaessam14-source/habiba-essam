"""
sensor_ai.py - AI on the ESP32 + ACS712 readings (no Streamlit here).

1. Device recognition (NILM, Non-Intrusive Load Monitoring): one sensor on the main wire, many devices. Every time
   the current jumps up or down, the size of the jump (and the start-up spike of motors) is compared with the
   devices it has learned, and the closest one is picked (nearest-neighbour matching).
2. Anomalies: a device drawing more or less than its usual current, a device on in the middle of the night,
   a jump that matches no known device, and unusual readings found by an Isolation Forest.
3. Predictive maintenance: the daily usual current of each device; a steady rise over the days (linear trend)
   can mean a tired motor or a failing part.
4. Home or away: switching things on and off means someone is probably home; a long time with no change while
   something stays on means it may have been forgotten.

Readings: a DataFrame with created_at (time, UTC), current_a (RMS over 5 s) and peak_a (highest sample in the 5 s),
as in the Supabase table sensor_readings. make_demo_readings() makes realistic sample data for tests and demos.
"""

import numpy as np
import pandas as pd

MIN_STEP_A = 0.12        # smaller changes are sensor noise
MATCH_TOLERANCE = 0.30   # a jump within 30% of a learned device counts as that device
INRUSH_RATIO = 1.8       # peak / steady current above this at switch-on = a motor (fan, pump)
DEFAULT_DEVICES = [      # until devices are learned on the website
    {"name": "Lamp (12 V 21 W)", "delta_a": 1.75, "inrush": False},
    {"name": "Fan (12 V)", "delta_a": 0.25, "inrush": True},
    {"name": "LED strip", "delta_a": 0.45, "inrush": False},
]


# ---------------------------------------------------------------------------------------------------------------
# sample data
# ---------------------------------------------------------------------------------------------------------------
def make_demo_readings(days=14, end=None, seed=7, every_s=60):
    """Sample readings: a lamp, a fan (with a motor start-up spike, slowly drawing more each day) and an LED strip.
    every_s=60 keeps the demo small (the real ESP32 sends every 5 s)."""
    rng = np.random.default_rng(seed)
    end = pd.Timestamp(end or pd.Timestamp.now(tz="UTC")).floor("min")
    idx = pd.date_range(end - pd.Timedelta(days=days), end, freq=f"{every_s}s")
    local = idx.tz_convert("Africa/Cairo")
    hours = np.asarray(local.hour + local.minute / 60, dtype=float)
    day_no = np.asarray((idx - idx[0]).days)
    lamp = ((hours >= 18) & (hours < 23.5)).astype(float) * 1.75
    fan_on = ((hours >= 13) & (hours < 17)) | ((hours >= 21) & (hours < 23))
    fan_level = 0.25 * (1 + 0.012 * day_no)  # +1.2% a day: the "tired motor" for the maintenance demo
    fan = fan_on.astype(float) * fan_level
    led = ((hours >= 19) & (hours < 22)).astype(float) * 0.45
    current = lamp + fan + led + rng.normal(0, 0.015, len(idx))
    current[(hours >= 3) & (hours < 4) & (day_no == day_no.max() - 1)] += 1.75  # lamp left on at 3 AM, last night
    current = np.clip(current, 0, None)
    peak = current + np.abs(rng.normal(0, 0.03, len(idx)))
    starts = np.r_[False, np.diff(fan_on.astype(int)) == 1]
    peak[starts] += 0.6  # the motor's start-up spike
    return pd.DataFrame({"created_at": idx, "current_a": current.round(3), "peak_a": peak.round(3)})


# ---------------------------------------------------------------------------------------------------------------
# 1. device recognition (NILM)
# ---------------------------------------------------------------------------------------------------------------
def steps(readings, min_step=MIN_STEP_A):
    """Every jump of the current: [{time, delta_a (signed), inrush (bool), before_a, after_a}]."""
    r = readings.sort_values("created_at").reset_index(drop=True)
    cur = r["current_a"].astype(float).to_numpy()
    if len(cur) < 3:
        return []
    out, i = [], 1
    while i < len(cur):
        d = cur[i] - cur[i - 1]
        if abs(d) >= min_step:
            after = np.median(cur[i:i + 3]) if i + 1 < len(cur) else cur[i]  # settle over the next readings
            before = np.median(cur[max(i - 3, 0):i])                         # and over the readings just before
            delta = after - before
            if abs(delta) >= min_step:
                pk = float(r["peak_a"].iloc[i]) if "peak_a" in r else cur[i]
                inrush = delta > 0 and (pk - before) / max(delta, 1e-6) >= INRUSH_RATIO
                out.append({"time": r["created_at"].iloc[i], "delta_a": float(delta), "inrush": bool(inrush),
                            "before_a": float(before), "after_a": float(after)})
                i += 2
                continue
        i += 1
    return out


def match(delta_a, inrush, devices):
    """(device name, distance) of the closest learned device for one jump, or (None, distance)."""
    best, best_d = None, 9.9
    for dev in devices:
        d = abs(abs(delta_a) - dev["delta_a"]) / dev["delta_a"]
        if dev.get("inrush") and delta_a > 0 and not inrush:
            d += 0.15  # a motor without its start-up spike is a weaker match
        if d < best_d:
            best, best_d = dev["name"], d
    return (best if best_d <= MATCH_TOLERANCE else None), best_d


def recognize(readings, devices=None, volts=12.0):
    """Which devices switched on/off, which are on now, and how long / how much energy each used.
    Returns {"events": DataFrame, "on_now": [names], "usage": DataFrame}."""
    devices = devices or DEFAULT_DEVICES
    rows, on_since = [], {}
    usage = {d["name"]: {"device": d["name"], "times_on": 0, "hours_on": 0.0} for d in devices}
    for s in steps(readings):
        name, dist = match(s["delta_a"], s["inrush"], devices)
        action = "on" if s["delta_a"] > 0 else "off"
        rows.append({"time": s["time"], "device": name or "Unknown device", "action": action,
                     "change_a": round(s["delta_a"], 2), "confidence": round(max(0.0, 1 - dist / MATCH_TOLERANCE), 2) if name else 0.0})
        if not name:
            continue
        if action == "on":
            on_since.setdefault(name, s["time"])
            usage[name]["times_on"] += 1
        elif name in on_since:
            usage[name]["hours_on"] += (s["time"] - on_since.pop(name)).total_seconds() / 3600
    last_t = readings["created_at"].max() if len(readings) else None
    for name, t0 in on_since.items():
        usage[name]["hours_on"] += (last_t - t0).total_seconds() / 3600
    by_name = {d["name"]: d for d in devices}
    u = pd.DataFrame(usage.values())
    if len(u):
        u["energy_wh"] = [round(by_name[n]["delta_a"] * volts * h, 1) for n, h in zip(u["device"], u["hours_on"])]
        u["hours_on"] = u["hours_on"].round(2)
    return {"events": pd.DataFrame(rows, columns=["time", "device", "action", "change_a", "confidence"]),
            "on_now": sorted(on_since), "usage": u}


def learn_from_last_step(readings, name):
    """A new device from the most recent switch-on: {"name", "delta_a", "inrush"}, or None if there is none."""
    ups = [s for s in steps(readings) if s["delta_a"] > 0]
    if not ups:
        return None
    s = ups[-1]
    return {"name": name.strip()[:40], "delta_a": round(s["delta_a"], 3), "inrush": s["inrush"]}


# ---------------------------------------------------------------------------------------------------------------
# 2. anomalies
# ---------------------------------------------------------------------------------------------------------------
def anomalies(readings, devices=None, rec=None, night=(0, 5), noise_a=0.15):
    """[{time, kind, message}] newest first: unknown jumps, devices on at night, and unusual readings (Isolation Forest)."""
    devices = devices or DEFAULT_DEVICES
    rec = rec or recognize(readings, devices)
    out = []
    for e in rec["events"].itertuples():
        if e.device == "Unknown device" and e.action == "on":
            out.append({"time": e.time, "kind": "unknown device",
                        "message": f"A jump of {e.change_a:+.2f} A matches no learned device. A new device, or a fault?"})
    r = readings.sort_values("created_at")
    local = r["created_at"].dt.tz_convert("Africa/Cairo")
    at_night = r[(local.dt.hour >= night[0]) & (local.dt.hour < night[1]) & (r["current_a"] > noise_a)]
    if len(at_night):
        groups = (at_night["created_at"].diff() > pd.Timedelta(minutes=15)).cumsum()
        for _, g in at_night.groupby(groups):
            t = g["created_at"].iloc[0].tz_convert("Africa/Cairo")
            out.append({"time": g["created_at"].iloc[0], "kind": "on at night",
                        "message": f"{g['current_a'].median():.2f} A was on at {t:%H:%M} at night "
                                   f"for {len(g) * _step_minutes(r):.0f} min. Left on by mistake?"})
    if len(r) >= 200:
        from sklearn.ensemble import IsolationForest

        x = pd.DataFrame({"a": r["current_a"].to_numpy(), "h": local.dt.hour.to_numpy()})
        flags = IsolationForest(n_estimators=150, contamination=0.002, random_state=42).fit_predict(x) == -1
        for t, a in r.loc[flags, ["created_at", "current_a"]].tail(5).itertuples(index=False):
            out.append({"time": t, "kind": "unusual reading",
                        "message": f"{a:.2f} A at {t.tz_convert('Africa/Cairo'):%H:%M} is unusual for that time of day."})
    return sorted(out, key=lambda x: x["time"], reverse=True)


def _step_minutes(r):
    d = r["created_at"].diff().median()
    return d.total_seconds() / 60 if pd.notna(d) else 1


# ---------------------------------------------------------------------------------------------------------------
# 3. predictive maintenance
# ---------------------------------------------------------------------------------------------------------------
def device_daily_levels(readings, devices=None):
    """For each device and day, the median size of its switch-on jumps (its usual current that day)."""
    devices = devices or DEFAULT_DEVICES
    rows = []
    for s in steps(readings):
        if s["delta_a"] <= 0:
            continue
        name, _ = match(s["delta_a"], s["inrush"], devices)
        if name:
            rows.append({"day": s["time"].tz_convert("Africa/Cairo").normalize(), "device": name, "delta_a": s["delta_a"]})
    if not rows:
        return pd.DataFrame(columns=["day", "device", "level_a"])
    d = pd.DataFrame(rows).groupby(["device", "day"])["delta_a"].median().rename("level_a").reset_index()
    return d


def maintenance(readings, devices=None, min_days=5, warn_per_month=0.10, min_r2=0.5):
    """[{device, days, level_a, change_per_month, status, message}] from the daily levels' linear trend.
    A warning needs a big enough change AND a clear trend (R² >= min_r2), so normal day-to-day noise is ignored."""
    levels = device_daily_levels(readings, devices)
    out = []
    for name, g in levels.groupby("device"):
        if len(g) < min_days:
            out.append({"device": name, "days": len(g), "level_a": round(float(g["level_a"].mean()), 3),
                        "change_per_month": None, "status": "learning",
                        "message": f"Needs at least {min_days} days of use to judge ({len(g)} so far)."})
            continue
        x = (g["day"] - g["day"].min()).dt.days.to_numpy(dtype=float)
        y = g["level_a"].to_numpy()
        slope, intercept = np.polyfit(x, y, 1)
        mean = float(y.mean())
        per_month = slope * 30 / mean if mean else 0.0
        ss_tot = float(((y - mean) ** 2).sum())
        r2 = 1 - float(((y - (slope * x + intercept)) ** 2).sum()) / ss_tot if ss_tot > 0 else 0.0
        if r2 < min_r2:  # day-to-day wobble, not a steady trend: no warning
            out.append({"device": name, "days": len(g), "level_a": round(mean, 3), "change_per_month": round(per_month, 3),
                        "status": "ok", "message": "Its current wobbles a little from day to day but shows no steady trend."})
            continue
        if per_month >= warn_per_month:
            status, msg = "check", (f"Draws {per_month:.0%} more current per month than when it started. "
                                    "A tired motor or a failing part? Check or clean it.")
        elif per_month <= -warn_per_month:
            status, msg = "check", f"Draws {abs(per_month):.0%} less current per month. It may be weakening."
        else:
            status, msg = "ok", "Its current is steady."
        out.append({"device": name, "days": len(g), "level_a": round(mean, 3), "change_per_month": round(per_month, 3),
                    "status": status, "message": msg})
    return out


# ---------------------------------------------------------------------------------------------------------------
# 4. home or away
# ---------------------------------------------------------------------------------------------------------------
def occupancy(readings, now=None, active_minutes=60, forgotten_hours=3, noise_a=0.15):
    """{"state": home / away / forgotten / unknown, "message", "last_change"} from the recent switching."""
    if not len(readings):
        return {"state": "unknown", "message": "No readings yet.", "last_change": None}
    now = pd.Timestamp(now or readings["created_at"].max())
    st = steps(readings)
    last = st[-1]["time"] if st else None
    current = float(readings.sort_values("created_at")["current_a"].iloc[-1])
    if last is not None and now - last <= pd.Timedelta(minutes=active_minutes):
        return {"state": "home", "last_change": last,
                "message": f"Someone is probably home: a device was switched {int((now - last).total_seconds() // 60)} min ago."}
    quiet = (now - last) if last is not None else (now - readings["created_at"].min())
    if current > noise_a and quiet >= pd.Timedelta(hours=forgotten_hours):
        return {"state": "forgotten", "last_change": last,
                "message": f"{current:.2f} A has been on with no change for {quiet.total_seconds() / 3600:.1f} h. "
                           "Forgotten? Power save could switch it off."}
    if current <= noise_a:
        return {"state": "away", "last_change": last, "message": "Nothing is on and nothing changed lately: probably nobody home."}
    return {"state": "home", "last_change": last, "message": "Something is on; probably someone is home."}
