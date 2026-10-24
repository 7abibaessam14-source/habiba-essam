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

No users and no Google -> login is off (handy on your own PC) and the app shows a "Login is off" note.

Passwords are checked with PBKDF2-SHA256 (Python's hashlib, no extra package). The login lasts for the
browser session: reloading the page logs you out.
"""

import base64
import hashlib
import hmac
import os
import time

import streamlit as st

ITERATIONS = 200_000
ROLES = ("admin", "viewer")
MAX_TRIES = 5  # wrong passwords in a row before a short lock
LOCK_SECONDS = 60


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
        if not configured_users():
            return
        if google_enabled():
            st.markdown("**or** use a username and password")
        with st.form("login"):
            name = st.text_input("Username")
            password = st.text_input("Password", type="password")
            submitted = st.form_submit_button(
                "Log in", type="secondary" if google_enabled() else "primary", width="stretch"
            )

        if not submitted:
            return
        if time.time() < st.session_state.get("login_locked_until", 0):
            st.error("Too many wrong tries. Wait a minute, then try again.")
            return

        cfg = configured_users().get(name.strip())
        if cfg and verify_password(password, cfg.get("password_hash", "")):
            role = cfg.get("role", "viewer")
            st.session_state.auth_user = {"name": name.strip(), "role": role if role in ROLES else "viewer"}
            st.session_state.login_fails = 0
            st.rerun()

        fails = st.session_state.get("login_fails", 0) + 1
        st.session_state.login_fails = fails
        if fails >= MAX_TRIES:
            st.session_state.login_locked_until = time.time() + LOCK_SECONDS
            st.session_state.login_fails = 0
        st.error("Wrong username or password.")
