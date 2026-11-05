"""
test_store_local.py - checks store.py: the local-file mode for real, and the Supabase mode with a FAKE server
(no internet, no account needed). Run: py test_store_local.py
"""

import os
import tempfile
from pathlib import Path

import store

ok = True


def check(name, cond):
    global ok
    ok &= bool(cond)
    print(f"{'ok  ' if cond else 'FAIL'} {name}")


# ---- local files ----
with tempfile.TemporaryDirectory() as tmp:
    f = Path(tmp) / "users.json"
    check("file: a new account is saved", store.add_user("Sara_1", {"password_hash": "h", "role": "viewer"}, f)[0])
    check("file: the same name in other letters is refused", not store.add_user("sara_1", {"password_hash": "h"}, f)[0])
    check("file: the account can be read back", store.load_users(f)["Sara_1"]["password_hash"] == "h")
    s = Path(tmp) / "subs.json"
    store.save_subscription("sara_1", {"frequency": "Weekly"}, s)
    check("file: a report choice is saved", store.load_subscriptions(s)["sara_1"]["frequency"] == "Weekly")


# ---- Supabase, with a fake server that records what it receives ----
class Fake:
    def __init__(self, status=201, body=None):
        self.status_code, self._body, self.text = status, body if body is not None else [], ""
        self.ok = status < 400

    def json(self):
        return self._body

    def raise_for_status(self):
        if not self.ok:
            raise store.requests.HTTPError(str(self.status_code))


calls = []
replies = {}


def fake_get(url, params=None, headers=None, timeout=None):
    calls.append(("GET", url, params, headers, None))
    return replies.get(("GET", url.rsplit("/", 1)[1]), Fake(200, []))


def fake_post(url, json=None, headers=None, timeout=None):
    calls.append(("POST", url, None, headers, json))
    return replies.get(("POST", url.rsplit("/", 1)[1]), Fake(201))


real_get, real_post = store.requests.get, store.requests.post
store.requests.get, store.requests.post = fake_get, fake_post
os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"] = "https://demo.supabase.co/", "sb_secret_TEST"
try:
    check("supabase: settings are found in the environment", store.supabase_config() == ("https://demo.supabase.co", "sb_secret_TEST"))
    check("supabase: a new account is created", store.add_user("Sara_1", {"password_hash": "h"})[0])
    method, url, _, headers, body = calls[-1]
    check("supabase: it goes to the app_users table", method == "POST" and url == "https://demo.supabase.co/rest/v1/app_users")
    check("supabase: only the hash and the viewer role are sent",
          body == {"username": "Sara_1", "username_lower": "sara_1", "password_hash": "h", "role": "viewer"})
    check("supabase: the new-style key is sent as apikey only", headers["apikey"] == "sb_secret_TEST" and "Authorization" not in headers)

    replies[("POST", "app_users")] = Fake(409)
    res = store.add_user("sara_1", {"password_hash": "h"})
    check("supabase: a taken name (409) gives a clear message", not res[0] and "taken" in res[1])

    replies[("GET", "app_users")] = Fake(200, [{"username": "Sara_1", "password_hash": "h", "role": "viewer", "created": "2026-11-01"}])
    check("supabase: accounts are read back", store.load_users()["Sara_1"]["role"] == "viewer")

    store.save_subscription("Sara_1", {"frequency": "Daily", "channels": ["Gmail"]})
    _, url, _, headers, body = calls[-1]
    check("supabase: a choice replaces the old one (merge)", "merge-duplicates" in headers["Prefer"] and body["data"]["frequency"] == "Daily")

    replies[("GET", "report_subscriptions")] = Fake(200, [{"username": "Sara_1", "data": {"frequency": "Daily"}, "saved": "2026-11-01"}])
    check("supabase: choices are read back", store.load_subscriptions()["Sara_1"]["frequency"] == "Daily")

    replies[("GET", "app_users")] = Fake(500)
    check("supabase: a server error does not crash the login", store.load_users() == {})

    check("supabase: a meter reading is saved", store.add_meter_reading("Sara_1", "2026-10-01", 1000.0, "photo")[0])
    _, url, _, _, body = calls[-1]
    check("supabase: it goes to the meter_readings table with its owner",
          url.endswith("/meter_readings") and body == {"username": "Sara_1", "read_on": "2026-10-01", "reading_kwh": 1000.0, "source": "photo"})
    replies[("POST", "meter_readings")] = Fake(409)
    check("supabase: a second reading on a day is refused", "already" in store.add_meter_reading("Sara_1", "2026-10-01", 1.0, "chat")[1])
    replies[("GET", "meter_readings")] = Fake(200, [{"read_on": "2026-10-01", "reading_kwh": "1000.5", "source": "photo"}])
    got = store.load_meter_readings("Sara_1")
    check("supabase: only this user's readings are asked for", calls[-1][2]["username"] == "eq.Sara_1")
    check("supabase: readings come back as numbers", got[0]["reading_kwh"] == 1000.5)
    replies[("GET", "meter_readings")] = Fake(500)
    check("supabase: a server error is not shown as 'no readings'", store.load_meter_readings("Sara_1") is None)
    deleted = []
    real_delete = store.requests.delete
    store.requests.delete = lambda url, params=None, headers=None, timeout=None: deleted.append(params) or Fake(204)
    check("supabase: a reading is deleted", store.delete_meter_reading("Sara_1", "2026-10-01")[0])
    store.requests.delete = real_delete
    check("supabase: only this user's reading is deleted", deleted[0] == {"username": "eq.Sara_1", "read_on": "eq.2026-10-01"})

    with tempfile.TemporaryDirectory() as tmp:
        f = Path(tmp) / "users.json"
        before = len(calls)
        store.add_user("Local_1", {"password_hash": "h"}, f)
        check("a file path always uses the file, even with Supabase set", len(calls) == before and f.exists())
finally:
    store.requests.get, store.requests.post = real_get, real_post
    del os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"]

print("\nRESULT:", "PASS" if ok else "FAIL")
