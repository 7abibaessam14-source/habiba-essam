"""
test_auth_local.py - checks "Create account" and login with a temporary users file. Run: py test_auth_local.py
"""

import json
import tempfile
from pathlib import Path

from auth import check_login, create_user

ok = True


def check(name, cond):
    global ok
    ok &= bool(cond)
    print(f"{'ok  ' if cond else 'FAIL'} {name}")


with tempfile.TemporaryDirectory() as tmp:
    f = Path(tmp) / "users.json"
    check("too short username is refused", not create_user("ab", "longpassword", "longpassword", f)[0])
    check("username with spaces is refused", not create_user("my name", "longpassword", "longpassword", f)[0])
    check("short password is refused", not create_user("sara_1", "short", "short", f)[0])
    check("different passwords are refused", not create_user("sara_1", "longpassword", "longpasswordX", f)[0])
    check("password equal to username is refused", not create_user("sara_12345", "sara_12345", "sara_12345", f)[0])
    check("a good account is created", create_user("sara_1", "Green-Tree-42", "Green-Tree-42", f)[0])
    check("the same name again is refused", not create_user("SARA_1", "Other-Pass-99", "Other-Pass-99", f)[0])
    check("right password logs in as viewer", check_login("sara_1", "Green-Tree-42", f) == "viewer")
    check("wrong password does not log in", check_login("sara_1", "wrong-password", f) is None)
    check("unknown user does not log in", check_login("nobody", "Green-Tree-42", f) is None)
    saved = json.loads(f.read_text(encoding="utf-8"))
    check("the password is not stored as plain text", "Green-Tree-42" not in f.read_text(encoding="utf-8"))
    check("the stored role is viewer", saved["sara_1"]["role"] == "viewer")
    saved["sara_1"]["role"] = "admin"  # someone edits the file by hand...
    f.write_text(json.dumps(saved), encoding="utf-8")
    check("...but a sign-up account still only gets viewer", check_login("sara_1", "Green-Tree-42", f) == "viewer")

print("\nRESULT:", "PASS" if ok else "FAIL")
