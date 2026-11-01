"""Check a Supabase URL + key without printing the key.

Run:  py check_supabase.py
The key is typed hidden (nothing shows on screen) and is never saved or printed.
"""
import base64
import getpass
import json
import sys

import requests

TABLES = ("app_users", "report_subscriptions")


def key_kind(key):
    # new keys: sb_secret_... / sb_publishable_...; legacy keys: JWT whose payload has a "role"
    if key.startswith("sb_secret_"):
        return "secret"
    if key.startswith("sb_publishable_"):
        return "publishable"
    if key.startswith("eyJ") and key.count(".") == 2:
        try:
            part = key.split(".")[1]
            payload = json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
            return payload.get("role", "unknown")
        except ValueError:
            return "broken"
    return "unknown"


def main():
    url = input("Paste the Project URL: ").strip().strip('"').rstrip("/")
    key = getpass.getpass("Paste the key (hidden, nothing will show): ").strip().strip('"')
    ok = True

    if url.endswith("/rest/v1"):
        print("FAIL  URL ends with /rest/v1 -> remove that part")
        url, ok = url[: -len("/rest/v1")], False
    if url.startswith("https://") and url.endswith(".supabase.co"):
        print("OK    URL format")
    else:
        print("FAIL  URL should look like https://xxxx.supabase.co")
        ok = False

    if not key.isascii():
        print("FAIL  the key contains hidden dots (•••) - you copied the masked text.")
        print("      In Supabase click the copy button next to the key instead of selecting the text.")
        print("RESULT: FAIL")
        return 1

    kind = key_kind(key)
    print(f"INFO  key length {len(key)}, type: {kind}")
    if kind in ("secret", "service_role"):
        print("OK    this key type can save accounts")
    elif kind in ("publishable", "anon"):
        print("FAIL  this is the PUBLIC key - it cannot save (RLS blocks it). Use the secret / service_role key")
        ok = False
    else:
        print("FAIL  this does not look like a Supabase key (copied only part of it?)")
        ok = False

    headers = {"apikey": key}
    if key.startswith("eyJ"):
        headers["Authorization"] = f"Bearer {key}"
    for table in TABLES:
        try:
            r = requests.get(f"{url}/rest/v1/{table}", headers=headers,
                             params={"select": "*", "limit": "1"}, timeout=15)
        except requests.RequestException as e:
            print(f"FAIL  cannot reach Supabase: {type(e).__name__}")
            ok = False
            break
        if r.status_code == 200:
            print(f"OK    table {table} reachable")
        elif r.status_code in (401, 403):
            print(f"FAIL  table {table}: key rejected ({r.status_code}) - wrong key or wrong project")
            ok = False
        elif r.status_code == 404:
            print(f"FAIL  table {table} not found - run supabase_setup.sql in this project")
            ok = False
        else:
            print(f"FAIL  table {table}: HTTP {r.status_code}")
            ok = False

    print("RESULT:", "PASS - paste exactly this URL and key into Streamlit + GitHub" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
