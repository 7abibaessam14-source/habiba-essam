"""
make_password_hash.py - make the lines for one dashboard user in .streamlit/secrets.toml.

Run:  py make_password_hash.py
It asks for a username, a role and the password (typed twice, not shown on screen) and prints
the lines to paste into .streamlit/secrets.toml. Only the hash is printed, never the password.
"""

import re
from getpass import getpass

from auth import ROLES, hash_password

if __name__ == "__main__":
    name = input("Username (letters, digits, _ or -): ").strip()
    role = input(f"Role {ROLES} [viewer]: ").strip() or "viewer"
    if not re.fullmatch(r"[A-Za-z0-9_-]+", name) or role not in ROLES:
        raise SystemExit(f"Need a username of letters, digits, _ or -, and a role from {ROLES}.")

    password = getpass("Password: ")
    if len(password) < 8:
        raise SystemExit("Use at least 8 characters.")
    if getpass("Password again: ") != password:
        raise SystemExit("The two passwords are different.")

    print("\nPaste these lines into .streamlit/secrets.toml:\n")
    print(f"[auth.users.{name}]")
    print(f'password_hash = "{hash_password(password)}"')
    print(f'role = "{role}"')
