"""
auth.py - simple username / password login for the dashboard.

Users live in .streamlit/secrets.toml (never in the code), with a HASHED password:

    [auth.users.admin]
    password_hash = "pbkdf2_sha256$200000$<salt>$<hash>"   # make it with:  py make_password_hash.py
    role = "admin"      # admin = every page; viewer = no Pipeline monitor and no email button

Optional "Sign in with Google" (Streamlit's st.login, needs the authlib package): anyone with a Google
account can sign in and gets the viewer role; emails listed in [roles] admin_emails get the admin role.

    [auth]
    redirect_uri = "https://YOUR-APP.streamlit.app/oauth2callback"
    cookie_secret = "<long random text>"
    [auth.google]
    client_id = "...apps.googleusercontent.com"
    client_secret = "..."
    server_metadata_url = "https://accounts.google.com/.well-known/openid-configuration"
    [roles]
    admin_emails = ["you@gmail.com"]

Create account: when login is on, anyone can also create a username + password on the login page. These
accounts are always "viewer" and are saved (hashed) in .streamlit/app_users.json, which is never uploaded.
On Streamlit Cloud that file is lost when the app restarts, so keeping them online needs a database.
Turn it off with:   [signup]  enabled = false

No users and no Google -> login is off (handy on your own PC) and the app shows a "Login is off" note.

Passwords are checked with PBKDF2-SHA256 (Python's hashlib, no extra package). The login lasts for the
browser session: reloading the page logs you out.
"""

import base64
import hashlib
import hmac
import json
import os
import re
import threading
import time
from pathlib import Path

import streamlit as st

ITERATIONS = 200_000
ROLES = ("admin", "viewer")
MAX_TRIES = 5  # wrong passwords in a row before a short lock
LOCK_SECONDS = 60
USERS_FILE = Path(__file__).parent / ".streamlit" / "app_users.json"  # accounts made on the login page
USERNAME_RE = re.compile(r"[A-Za-z0-9_-]{3,20}")
MIN_PASSWORD = 8
MAX_SIGNUPS_PER_SESSION = 3
_users_file_lock = threading.Lock()  # two people signing up at the same moment must not overwrite each other


def hash_password(password, iterations=ITERATIONS):
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"pbkdf2_sha256${iterations}${base64.b64encode(salt).decode()}${base64.b64encode(digest).decode()}"


def verify_password(password, stored_hash):
    try:
        algo, iterations, salt, digest = stored_hash.split("$")
        if algo != "pbkdf2_sha256":
            return False
        test = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), base64.b64decode(salt), int(iterations))
        return hmac.compare_digest(test, base64.b64decode(digest))
    except (ValueError, TypeError, AttributeError):  # malformed hash in secrets.toml
        return False


def configured_users():
    """{username: {"password_hash": ..., "role": ...}} from secrets.toml, or {} when login is off."""
    try:
        users = st.secrets.get("auth", {}).get("users", {})
    except FileNotFoundError:  # no secrets.toml at all
        return {}
    return {name: dict(cfg) for name, cfg in users.items()}


def _secrets_section(name):
    try:
        return st.secrets.get(name, {})
    except FileNotFoundError:  # no secrets.toml at all
        return {}


def file_users(path=USERS_FILE):
    """Accounts created on the login page: {username: {"password_hash", "role", "created"}}."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def signup_enabled():
    return login_enabled() and bool(_secrets_section("signup").get("enabled", True))


def create_user(name, password, password2, path=USERS_FILE):
    """Saves a new viewer account. Returns (ok, message)."""
    name = name.strip()
    if not USERNAME_RE.fullmatch(name):
        return False, "Username: 3 to 20 letters, digits, _ or -."
    if len(password) < MIN_PASSWORD:
        return False, f"Password: at least {MIN_PASSWORD} characters."
    if password != password2:
        return False, "The two passwords are different."
    if password.lower() == name.lower():
        return False, "The password must not be the same as the username."
    with _users_file_lock:
        users = file_users(path)
        if name.lower() in {u.lower() for u in list(users) + list(configured_users())}:
            return False, "This username is taken. Please choose another one."
        users[name] = {"password_hash": hash_password(password), "role": "viewer", "created": time.strftime("%Y-%m-%d %H:%M")}
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(users, indent=1), encoding="utf-8")
        tmp.replace(p)  # write-then-rename: the file is never left half written
    return True, "Account created."


def check_login(name, password, path=USERS_FILE):
    """The role ('admin' / 'viewer') when the username and password are right, else None."""
    cfg = configured_users().get(name)
    if cfg:  # accounts in secrets.toml come first and keep their role
        role = cfg.get("role", "viewer")
        return (role if role in ROLES else "viewer") if verify_password(password, cfg.get("password_hash", "")) else None
    cfg = file_users(path).get(name)
    if cfg and verify_password(password, cfg.get("password_hash", "")):
        return "viewer"  # accounts made on the login page are always viewers
    return None


def google_enabled():
    """True when the secrets hold everything st.login("google") needs."""
    auth, google = _secrets_section("auth"), _secrets_section("auth").get("google", {})
    return all(auth.get(k) for k in ("redirect_uri", "cookie_secret")) and all(
        google.get(k) for k in ("client_id", "client_secret", "server_metadata_url")
    )


def admin_emails():
    return {e.strip().lower() for e in _secrets_section("roles").get("admin_emails", [])}


def login_enabled():
    return bool(configured_users()) or google_enabled()


def current_user():
    """{"name", "role"} of the logged-in user, an admin stand-in when login is off, or None."""
    if not login_enabled():
        return {"name": "local", "role": "admin"}
    if google_enabled() and st.user.is_logged_in:
        email = str(st.user.get("email") or "").lower()
        role = "admin" if email in admin_emails() else "viewer"
        return {"name": st.user.get("name") or email, "email": email, "role": role, "google": True}
    return st.session_state.get("auth_user")


def is_admin():
    user = current_user()
    return bool(user) and user["role"] == "admin"


def logout():
    google = (current_user() or {}).get("google")
    st.session_state.clear()  # forget the user and everything they did (Live replay, settings, ...)
    if google:
        st.logout()  # also removes the Google login cookie


def login_page(title, subtitle):
    _, mid, _ = st.columns([1, 2, 1])
    with mid:
        st.header(title)
        st.caption(subtitle)
        if google_enabled():
            if st.button("Sign in with Google", icon=":material/login:", type="primary", width="stretch"):
                st.login("google")
            st.caption("Anyone with a Google account can sign in.")
        if not configured_users() and not signup_enabled():
            return
        if google_enabled():
            st.markdown("**or** use a username and password")
        if signup_enabled():
            tab_login, tab_new = st.tabs(["Log in", "Create account"])
            with tab_login:
                _login_form()
            with tab_new:
                _signup_form()
        else:
            _login_form()


def _login_form():
    with st.form("login"):
        name = st.text_input("Username")
        password = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Log in", type="secondary" if google_enabled() else "primary", width="stretch")
    if not submitted:
        return
    if time.time() < st.session_state.get("login_locked_until", 0):
        st.error("Too many wrong tries. Wait a minute, then try again.")
        return

    role = check_login(name.strip(), password)
    if role:
        st.session_state.auth_user = {"name": name.strip(), "role": role}
        st.session_state.login_fails = 0
        st.rerun()

    fails = st.session_state.get("login_fails", 0) + 1
    st.session_state.login_fails = fails
    if fails >= MAX_TRIES:
        st.session_state.login_locked_until = time.time() + LOCK_SECONDS
        st.session_state.login_fails = 0
    st.error("Wrong username or password.")


def _signup_form():
    with st.form("signup", clear_on_submit=False):
        name = st.text_input("Choose a username", help="3 to 20 letters, digits, _ or -")
        password = st.text_input("Choose a password", type="password", help=f"At least {MIN_PASSWORD} characters")
        password2 = st.text_input("Repeat the password", type="password")
        submitted = st.form_submit_button("Create account", width="stretch")
    st.caption("Your password is stored hashed, never as plain text. New accounts can view every page except the "
               "admin tools. On the free online version, new accounts may be reset when the server restarts.")
    if not submitted:
        return
    if st.session_state.get("signups", 0) >= MAX_SIGNUPS_PER_SESSION:
        st.error("Too many new accounts from this browser. Please use one of the accounts you created.")
        return
    ok, msg = create_user(name, password, password2)
    if not ok:
        st.error(msg)
        return
    st.session_state.signups = st.session_state.get("signups", 0) + 1
    st.session_state.auth_user = {"name": name.strip(), "role": "viewer"}  # log the new user straight in
    st.rerun()
