"""
store.py - where the accounts made on the login page, the saved report choices and the meter readings are kept.

  Supabase (a free cloud PostgreSQL database) when the keys are set:
      [supabase]  url = "https://xxxx.supabase.co", key = "<secret key>"    in .streamlit/secrets.toml
      or the environment variables SUPABASE_URL and SUPABASE_KEY            (GitHub Actions)
  otherwise JSON files in .streamlit/ like before: fine on your own PC, lost when Streamlit Cloud restarts.

Create the tables once with supabase_setup.sql. This file talks to Supabase's REST API with `requests`,
so no extra package is needed. The key is the SECRET key: it stays on the server and is never shown.
Passing `path` always uses that JSON file (the tests do this), whatever the settings.
"""

import json
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

FOLDER = Path(__file__).parent / ".streamlit"
USERS_FILE = FOLDER / "app_users.json"
SUBSCRIPTIONS_FILE = FOLDER / "report_subscriptions.json"
METER_READINGS_FILE = FOLDER / "meter_readings.json"
TIMEOUT = 15
_lock = threading.Lock()  # two people saving at the same moment must not overwrite each other's file changes


def supabase_config():
    """(url, key) when Supabase is set up, else None."""
    url, key = os.environ.get("SUPABASE_URL"), os.environ.get("SUPABASE_KEY")
    if not (url and key):
        try:
            import streamlit as st

            cfg = st.secrets.get("supabase", {})
            url, key = cfg.get("url"), cfg.get("key")
        except Exception:  # no secrets.toml, or not running inside Streamlit
            return None
    return (url.rstrip("/"), key) if url and key else None


def backend_name():
    return "Supabase database" if supabase_config() else "local files"


def _remote(path):
    return supabase_config() if path is None else None


def _headers(key, **extra):
    h = {"apikey": key, "Content-Type": "application/json", **extra}
    if key.startswith("eyJ"):  # the older JWT-style keys also go in the Authorization header
        h["Authorization"] = f"Bearer {key}"
    return h


def _read_json(path):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_json(path, data):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
    tmp.replace(p)  # write-then-rename: the file is never left half written


# ---- accounts made on the login page ------------------------------------------------
def load_users(path=None):
    """{username: {"password_hash", "role", "created"}}; {} when the database cannot be reached."""
    cfg = _remote(path)
    if not cfg:
        return _read_json(path or USERS_FILE)
    url, key = cfg
    try:
        r = requests.get(f"{url}/rest/v1/app_users", params={"select": "username,password_hash,role,created"},
                         headers=_headers(key), timeout=TIMEOUT)
        r.raise_for_status()
        return {u["username"]: {"password_hash": u["password_hash"], "role": u["role"], "created": u["created"]}
                for u in r.json()}
    except (requests.RequestException, ValueError, KeyError):
        return {}


def add_user(name, record, path=None):
    """Saves a new account unless the name is taken (any upper/lower case). Returns (ok, message)."""
    cfg = _remote(path)
    if not cfg:
        with _lock:
            users = _read_json(path or USERS_FILE)
            if name.lower() in {u.lower() for u in users}:
                return False, "This username is taken. Please choose another one."
            users[name] = record
            _write_json(path or USERS_FILE, users)
        return True, "Account created."
    url, key = cfg
    row = {"username": name, "username_lower": name.lower(), "password_hash": record["password_hash"], "role": "viewer"}
    try:
        r = requests.post(f"{url}/rest/v1/app_users", json=row, headers=_headers(key, Prefer="return=minimal"),
                          timeout=TIMEOUT)
    except requests.RequestException:
        return False, "Could not reach the database. Please try again."
    if r.status_code == 409:  # the database refuses a second account with the same name
        return False, "This username is taken. Please choose another one."
    if r.status_code in (200, 201, 204):
        return True, "Account created."
    return False, "The database refused the new account. Please try again later."


# ---- saved report choices --------------------------------------------------------------
def load_subscriptions(path=None):
    """{username: {"frequency", "channels", "email", "phone", "whatsapp_apikey", "budget", "saved"}}."""
    cfg = _remote(path)
    if not cfg:
        return _read_json(path or SUBSCRIPTIONS_FILE)
    url, key = cfg
    try:
        r = requests.get(f"{url}/rest/v1/report_subscriptions", params={"select": "username,data,saved"},
                         headers=_headers(key), timeout=TIMEOUT)
        r.raise_for_status()
        return {s["username"]: {**s["data"], "saved": s["saved"]} for s in r.json()}
    except (requests.RequestException, ValueError, KeyError, TypeError):
        return {}


def save_subscription(name, sub, path=None):
    """Adds or replaces this user's choice. Returns (ok, message)."""
    saved = time.strftime("%Y-%m-%d %H:%M")
    cfg = _remote(path)
    if not cfg:
        with _lock:
            subs = _read_json(path or SUBSCRIPTIONS_FILE)
            subs[name] = {**sub, "saved": saved}
            _write_json(path or SUBSCRIPTIONS_FILE, subs)
        return True, "Saved."
    url, key = cfg
    try:
        row = {"username": name, "data": sub, "saved": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        r = requests.post(f"{url}/rest/v1/report_subscriptions", json=row,
                          headers=_headers(key, Prefer="resolution=merge-duplicates,return=minimal"), timeout=TIMEOUT)
    except requests.RequestException:
        return False, "Could not reach the database. Please try again."
    if r.status_code in (200, 201, 204):
        return True, "Saved."
    return False, "The database refused to save your choice. Please try again later."


# ---- meter readings confirmed on the Meter assistant page ---------------------------------
def load_meter_readings(name, path=None):
    """This user's [{"read_on": "YYYY-MM-DD", "reading_kwh", "source"}], oldest first; None when the database cannot be reached."""
    cfg = _remote(path)
    if not cfg:
        rows = _read_json(path or METER_READINGS_FILE).get(name, [])
        return sorted(rows, key=lambda r: r["read_on"])
    url, key = cfg
    try:
        r = requests.get(f"{url}/rest/v1/meter_readings",
                         params={"select": "read_on,reading_kwh,source", "username": f"eq.{name}", "order": "read_on.asc"},
                         headers=_headers(key), timeout=TIMEOUT)
        r.raise_for_status()
        return [{"read_on": m["read_on"], "reading_kwh": float(m["reading_kwh"]), "source": m["source"]} for m in r.json()]
    except (requests.RequestException, ValueError, KeyError, TypeError):
        return None


def add_meter_reading(name, read_on, reading_kwh, source, path=None):
    """Saves one reading (one per day for each user). Returns (ok, message)."""
    row = {"read_on": read_on, "reading_kwh": reading_kwh, "source": source}
    cfg = _remote(path)
    if not cfg:
        with _lock:
            data = _read_json(path or METER_READINGS_FILE)
            if any(r["read_on"] == read_on for r in data.get(name, [])):
                return False, "You already have a reading on this date."
            data.setdefault(name, []).append(row)
            _write_json(path or METER_READINGS_FILE, data)
        return True, "Saved."
    url, key = cfg
    try:
        r = requests.post(f"{url}/rest/v1/meter_readings", json={"username": name, **row},
                          headers=_headers(key, Prefer="return=minimal"), timeout=TIMEOUT)
    except requests.RequestException:
        return False, "Could not reach the database. Please try again."
    if r.status_code == 409:
        return False, "You already have a reading on this date."
    if r.status_code in (200, 201, 204):
        return True, "Saved."
    return False, "The database refused to save your reading. Please try again later."


def delete_meter_reading(name, read_on, path=None):
    """Removes one reading, for correcting a mistake. Returns (ok, message)."""
    cfg = _remote(path)
    if not cfg:
        with _lock:
            data = _read_json(path or METER_READINGS_FILE)
            data[name] = [r for r in data.get(name, []) if r["read_on"] != read_on]
            _write_json(path or METER_READINGS_FILE, data)
        return True, "Deleted."
    url, key = cfg
    try:
        r = requests.delete(f"{url}/rest/v1/meter_readings",
                            params={"username": f"eq.{name}", "read_on": f"eq.{read_on}"},
                            headers=_headers(key, Prefer="return=minimal"), timeout=TIMEOUT)
    except requests.RequestException:
        return False, "Could not reach the database. Please try again."
    return (True, "Deleted.") if r.status_code in (200, 204) else (False, "The database refused to delete it. Please try again later.")
