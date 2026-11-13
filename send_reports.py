"""
send_reports.py - sends the scheduled energy reports. GitHub Actions runs it every evening
(.github/workflows/reports.yml), because Streamlit Cloud cannot run anything on a timer.

    py send_reports.py --dry-run --force      on your PC: builds every report and prints it, sends nothing

It trains the model on the data in ./data (the zipped UCI file online), then for every subscriber whose
report is due (Daily: every day, Weekly: Saturdays, Monthly: the 1st) builds the report for today's date
and sends it. Settings come from environment variables (GitHub -> Settings -> Secrets and variables -> Actions):
  SUPABASE_URL, SUPABASE_KEY   the database: everyone who clicked "Save my choice" gets their report
  REPORT_SUBSCRIBERS   (without the database) the JSON from the app's Reports page (admin box), or a list:
                       {"habiba": {"frequency": "Daily", "channels": ["Gmail", "WhatsApp"], "email": "...",
                                   "phone": "+20...", "whatsapp_apikey": "...", "budget": 600}}
  SMTP_USER, SMTP_PASSWORD (Gmail App Password), optional SMTP_HOST, SMTP_PORT
  TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_FROM            (only for SMS)
Contacts are printed masked and keys never; the run fails (red in GitHub) when a message could not be sent.
"""

import argparse
import json
import os
import sys
from pathlib import Path

from ml_models import add_features, to_hourly, train_and_compare
import store
from notify import send_report
from reports import as_email, as_sms, as_whatsapp, build_report, is_due, now_local
from uci_loader import load_uci, uci_path
import weather

DATA_DIR = Path(__file__).parent / "data"


def mask(value):
    value = str(value or "")
    if "@" in value:
        name, domain = value.split("@", 1)
        return f"{name[:2]}***@{domain}"
    return value[:4] + "*" * max(len(value) - 6, 0) + value[-2:] if len(value) > 6 else "***"


def subscribers_from_env():
    raw = os.environ.get("REPORT_SUBSCRIBERS", "").strip()
    if not raw:
        return []
    data = json.loads(raw)
    if isinstance(data, dict):  # the app's format: {username: {...}}
        return [{"name": k, **v} for k, v in data.items()]
    return list(data)


def subscribers():
    """REPORT_SUBSCRIBERS plus the choices saved in the Supabase database (the database wins for the same name)."""
    subs = {s.get("name", f"#{i}"): s for i, s in enumerate(subscribers_from_env())}
    if store.supabase_config():
        subs.update({name: {"name": name, **sub} for name, sub in store.load_subscriptions().items()})
    return list(subs.values())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="send to everyone now, whatever their frequency")
    ap.add_argument("--dry-run", action="store_true", help="print the reports, send nothing")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    subs = subscribers()
    if not subs and args.dry_run:
        subs = [{"name": "example", "frequency": f, "channels": ["Gmail", "WhatsApp", "SMS"], "budget": 600}
                for f in ("Daily", "Weekly", "Monthly")]
    now = now_local()
    due = [s for s in subs if args.force or is_due(s.get("frequency", "Daily"), now)]
    print(f"{now:%Y-%m-%d %H:%M} Cairo: {len(subs)} subscriber(s), {len(due)} report(s) due.")

    # tomorrow's weather: fetched once per city; a failure only leaves the weather out of the report
    forecasts = {}

    def weather_for(city):
        city = city if city in weather.CITIES else weather.DEFAULT_CITY
        if city not in forecasts:
            forecasts[city] = weather.fetch_tomorrow(city, today=now.date())
        return forecasts[city]

    w = weather_for(weather.DEFAULT_CITY)
    print(weather.summary(w) if w else "Weather: could not be fetched (reports go out without it).")
    if w and not args.dry_run and store.supabase_config():  # the evening phone notification, for everyone subscribed
        ok, msg = weather.send_push(w)
        print(("ok   " if ok else "FAIL ") + "phone notification: " + msg)
    if not due:
        return 0

    path = uci_path(DATA_DIR)
    if path is None:
        print("No UCI data file in ./data")
        return 1
    fact, _, _ = load_uci(path)
    hourly = to_hourly(fact)
    res = train_and_compare(add_features(hourly))
    print(f"Model trained on {hourly.dropna().index.min():%Y}–{hourly.dropna().index.max():%Y}: best = {res['best_name']}")

    smtp = None
    if os.environ.get("SMTP_USER") and os.environ.get("SMTP_PASSWORD"):
        smtp = {"host": os.environ.get("SMTP_HOST") or "smtp.gmail.com", "port": int(os.environ.get("SMTP_PORT") or 587),
                "user": os.environ["SMTP_USER"], "password": os.environ["SMTP_PASSWORD"]}
    twilio = None
    if all(os.environ.get(k) for k in ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_FROM")):
        twilio = {"account_sid": os.environ["TWILIO_ACCOUNT_SID"], "auth_token": os.environ["TWILIO_AUTH_TOKEN"],
                  "from_number": os.environ["TWILIO_FROM"]}

    failed = 0
    for s in due:
        r = build_report(hourly, res["best_model"], s.get("frequency", "Daily"), now=now,
                         budget_egp=float(s.get("budget") or 0) or None, fact=fact, lang=s.get("language", "en"),
                         weather=weather_for(s.get("city", weather.DEFAULT_CITY)))
        texts = {"sms": as_sms(r), "whatsapp": as_whatsapp(r), "email": as_email(r)}
        print(f"\n--- {s.get('name', '?')}: {r['frequency']} by {', '.join(s.get('channels', []))} "
              f"({mask(s.get('email'))} {mask(s.get('phone'))})")
        if args.dry_run:
            print(texts["whatsapp"])
            continue
        for ok, msg in send_report(s, texts, smtp, twilio):
            print(("ok   " if ok else "FAIL ") + msg.replace(s.get("email") or "\0", mask(s.get("email")))
                  .replace(s.get("phone") or "\0", mask(s.get("phone"))))
            failed += not ok
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
