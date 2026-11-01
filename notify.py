"""
notify.py - sends a report by Gmail, WhatsApp or SMS (the saved report choices are kept by store.py).

Every sender returns (ok, message) and never raises. The keys come from secrets.toml / GitHub Secrets, never
from the code:
  Gmail     [smtp]   host = "smtp.gmail.com", port = 587, user, password  (a Gmail "App Password", not your
                     normal password: Google Account -> Security -> 2-Step Verification -> App passwords)
  WhatsApp  CallMeBot (free): each person sends "I allow callmebot to send me messages" to the bot's WhatsApp
            number once and gets their own apikey (https://www.callmebot.com/blog/free-api-whatsapp-messages/)
  SMS       [twilio] account_sid, auth_token, from_number  (the free trial only sends to verified numbers)
Error messages never include the URL or the keys.
"""

import re
import smtplib
from email.message import EmailMessage

import requests

EMAIL_RE = re.compile(r"[^@\s]+@[^@\s]+\.[A-Za-z]{2,}")
PHONE_RE = re.compile(r"\+\d{8,15}")  # international format, e.g. +201001234567


def send_gmail(smtp_cfg, to, subject, text, html=None):
    if not EMAIL_RE.fullmatch(to or ""):
        return False, "Gmail: please enter a valid email address."
    try:
        msg = EmailMessage()
        msg["Subject"], msg["From"], msg["To"] = subject, smtp_cfg["user"], to
        msg.set_content(text)
        if html:
            msg.add_alternative(html, subtype="html")
        with smtplib.SMTP(smtp_cfg.get("host", "smtp.gmail.com"), int(smtp_cfg.get("port", 587)), timeout=20) as server:
            server.starttls()
            server.login(smtp_cfg["user"], smtp_cfg["password"])
            server.send_message(msg)
        return True, f"Gmail: sent to {to}."
    except smtplib.SMTPAuthenticationError:
        return False, "Gmail: login refused. Use a Gmail App Password in [smtp] password."
    except Exception as e:
        return False, f"Gmail: could not send ({type(e).__name__})."


def send_whatsapp(phone, apikey, text):
    if not PHONE_RE.fullmatch(phone or ""):
        return False, "WhatsApp: enter the phone with the country code, e.g. +201001234567."
    if not apikey:
        return False, "WhatsApp: enter your CallMeBot key (see the help next to the box)."
    try:
        r = requests.get("https://api.callmebot.com/whatsapp.php",
                         params={"phone": phone, "text": text, "apikey": apikey}, timeout=30)
    except requests.RequestException as e:
        return False, f"WhatsApp: could not reach CallMeBot ({type(e).__name__})."
    body = r.text.lower()
    if r.ok and ("queued" in body or "sent" in body) and "error" not in body:
        return True, f"WhatsApp: sent to {phone}."
    return False, "WhatsApp: CallMeBot refused it. Check the phone number and the key."


def send_sms(twilio_cfg, phone, text):
    if not PHONE_RE.fullmatch(phone or ""):
        return False, "SMS: enter the phone with the country code, e.g. +201001234567."
    sid = twilio_cfg["account_sid"]
    try:
        r = requests.post(f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json",
                          data={"To": phone, "From": twilio_cfg["from_number"], "Body": text},
                          auth=(sid, twilio_cfg["auth_token"]), timeout=30)
    except requests.RequestException as e:
        return False, f"SMS: could not reach Twilio ({type(e).__name__})."
    if r.status_code == 201:
        return True, f"SMS: sent to {phone}."
    try:
        reason = r.json().get("message", "")
    except ValueError:
        reason = ""
    return False, f"SMS: Twilio refused it. {reason}".strip()


def send_report(sub, texts, smtp_cfg=None, twilio_cfg=None):
    """
    sub: {"channels": [...], "email", "phone", "whatsapp_apikey"}; texts: {"sms", "whatsapp", "email": (subject, text, html)}.
    Returns [(ok, message), ...], one per channel.
    """
    out = []
    for ch in sub.get("channels", []):
        if ch == "Gmail":
            out.append(send_gmail(smtp_cfg, sub.get("email", ""), *texts["email"]) if smtp_cfg
                       else (False, "Gmail: the app has no [smtp] settings yet."))
        elif ch == "WhatsApp":
            out.append(send_whatsapp(sub.get("phone", ""), sub.get("whatsapp_apikey", ""), texts["whatsapp"]))
        elif ch == "SMS":
            out.append(send_sms(twilio_cfg, sub.get("phone", ""), texts["sms"]) if twilio_cfg
                       else (False, "SMS: the app has no [twilio] settings yet."))
    return out

