"""
powersave.py - Power save mode: the settings the website saves and the ESP32 reads, and the log of what the ESP32 did.

Like a phone's power save mode: the user switches it on. When the current stays above `limit_a` for `after_min`
minutes (for example two 12 V lamps left on), the ESP32 warns (red LED blinks, the website shows a countdown),
waits `warn_s` seconds so the user can press Cancel, then switches lamp 2 off with a relay.
Without a relay the ESP32 only logs "would_turn_off" (test mode).

Tables: hardware/power_save_setup.sql. The website uses the SECRET key from [supabase] (store.supabase_config).
"""

from datetime import datetime, timedelta, timezone

import pandas as pd
import requests

import store

DEFAULT_DEVICE = "esp32-lab-1"
DEFAULTS = {"enabled": False, "limit_a": 3.0, "after_min": 10.0, "warn_s": 60, "lamp_w": 21.0}
EVENT_COLUMNS = ["created_at", "device_id", "event", "current_a"]
EVENT_TEXT = {
    "warning": "Warning: lamp 2 will switch off soon",
    "cancelled": "Cancelled (Cancel pressed, or the current went down)",
    "turned_off": "Lamp 2 switched off",
    "would_turn_off": "Would switch lamp 2 off (no relay connected yet)",
    "restored": "Lamp 2 switched back on",
}
TIMEOUT = 15


def _cfg(cfg):
    return cfg or store.supabase_config()


def load_settings(device_id, cfg=None):
    """(settings dict, error text or None). Missing row = the defaults (power save off)."""
    cfg = _cfg(cfg)
    if not cfg:
        return dict(DEFAULTS), "Supabase is not set up ([supabase] url and key in the secrets)."
    url, key = cfg
    try:
        r = requests.get(f"{url}/rest/v1/power_save_settings",
                         params={"select": "enabled,limit_a,after_min,warn_s,lamp_w", "device_id": f"eq.{device_id}"},
                         headers=store._headers(key), timeout=TIMEOUT)
    except requests.RequestException:
        return dict(DEFAULTS), "Could not reach the database."
    if r.status_code == 404:
        return dict(DEFAULTS), "The power save tables do not exist yet: run hardware/power_save_setup.sql in Supabase."
    if r.status_code != 200:
        return dict(DEFAULTS), f"The database answered HTTP {r.status_code}."
    rows = r.json()
    return ({**DEFAULTS, **rows[0]} if rows else dict(DEFAULTS)), None


def _upsert(device_id, fields, cfg=None):
    cfg = _cfg(cfg)
    if not cfg:
        return False, "Supabase is not set up."
    url, key = cfg
    row = {"device_id": device_id, **fields, "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    try:
        r = requests.post(f"{url}/rest/v1/power_save_settings", json=row,
                          headers=store._headers(key, Prefer="resolution=merge-duplicates,return=minimal"), timeout=TIMEOUT)
    except requests.RequestException:
        return False, "Could not reach the database. Please try again."
    if r.status_code in (200, 201, 204):
        return True, "Saved."
    if r.status_code == 404:
        return False, "Run hardware/power_save_setup.sql in Supabase first."
    return False, f"The database refused it (HTTP {r.status_code})."


def save_settings(device_id, enabled, limit_a, after_min, warn_s, lamp_w, cfg=None):
    return _upsert(device_id, {"enabled": bool(enabled), "limit_a": float(limit_a), "after_min": float(after_min),
                               "warn_s": int(warn_s), "lamp_w": float(lamp_w)}, cfg)


def cancel(device_id, minutes, cfg=None, now=None):
    """'Cancel' during a warning: the ESP32 waits `minutes` before it may warn again."""
    until = (now or datetime.now(timezone.utc)) + timedelta(minutes=minutes)
    return _upsert(device_id, {"snooze_until": until.isoformat(timespec="seconds")}, cfg)


def restore(device_id, cfg=None, now=None):
    """'Turn back on': the ESP32 switches lamp 2 on again at its next check."""
    return _upsert(device_id, {"restore_at": (now or datetime.now(timezone.utc)).isoformat(timespec="seconds")}, cfg)


def load_events(device_id, hours=24, cfg=None, now=None):
    """(DataFrame of this device's events in the last `hours`, oldest first, error text or None)."""
    empty = pd.DataFrame(columns=EVENT_COLUMNS)
    cfg = _cfg(cfg)
    if not cfg:
        return empty, "Supabase is not set up."
    url, key = cfg
    since = (now or datetime.now(timezone.utc)) - timedelta(hours=hours)
    try:
        r = requests.get(f"{url}/rest/v1/power_save_events",
                         params={"select": ",".join(EVENT_COLUMNS), "device_id": f"eq.{device_id}",
                                 "created_at": f"gte.{since.isoformat(timespec='seconds')}",
                                 "order": "created_at.asc", "limit": "1000"},
                         headers=store._headers(key), timeout=TIMEOUT)
    except requests.RequestException:
        return empty, "Could not reach the database."
    if r.status_code != 200:
        return empty, ("Run hardware/power_save_setup.sql in Supabase first." if r.status_code == 404
                       else f"The database answered HTTP {r.status_code}.")
    return events_frame(r.json()), None


def events_frame(rows):
    df = pd.DataFrame(rows, columns=EVENT_COLUMNS)
    df["created_at"] = pd.to_datetime(df["created_at"], utc=True, format="ISO8601")
    return df.sort_values("created_at").reset_index(drop=True)


def status(events, settings, now=None):
    """What power save is doing now: state in off / watching / warning / turned_off, plus details."""
    now = pd.Timestamp(now or datetime.now(timezone.utc))
    last = events.iloc[-1] if len(events) else None
    if last is not None and last["event"] == "turned_off":  # the lamp stays off until it is turned back on
        return {"state": "turned_off", "since": last["created_at"]}
    if not settings["enabled"]:
        return {"state": "off"}
    if last is not None and last["event"] == "warning":
        left = settings["warn_s"] - (now - last["created_at"]).total_seconds()
        if left > -15:  # a little extra time for the ESP32's next report
            return {"state": "warning", "seconds_left": max(int(left), 0), "since": last["created_at"]}
    return {"state": "watching"}


def saved_wh(events, lamp_w, now=None):
    """Energy saved: how long lamp 2 was really switched off (turned_off -> restored, or until now) x its power."""
    now = pd.Timestamp(now or datetime.now(timezone.utc))
    total_s, off_since = 0.0, None
    for _, e in events.iterrows():
        if e["event"] == "turned_off" and off_since is None:
            off_since = e["created_at"]
        elif e["event"] == "restored" and off_since is not None:
            total_s += (e["created_at"] - off_since).total_seconds()
            off_since = None
    if off_since is not None:
        total_s += (now - off_since).total_seconds()
    return lamp_w * total_s / 3600
