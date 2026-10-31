"""
test_reports_local.py - checks the reports and the senders without sending anything. Run: py test_reports_local.py
"""

import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

import notify
from reports import align_to_today, as_email, as_sms, as_whatsapp, build_report, is_due
from send_reports import mask

ok = True


def check(name, cond):
    global ok
    ok &= bool(cond)
    print(f"{'ok  ' if cond else 'FAIL'} {name}")


class SameHourYesterday:  # stands in for the trained model: fast and predictable
    def predict(self, X):
        return X["lag_24"].to_numpy(dtype=float)


# three years of fake hourly data: busier evenings, a bit higher in winter
idx = pd.date_range("2007-01-01", "2009-12-31 23:00", freq="h")
rng = np.random.default_rng(0)
hourly = pd.Series(0.4 + 0.8 * np.isin(idx.hour, [19, 20, 21]) + 0.2 * np.isin(idx.month, [12, 1, 2])
                   + rng.normal(0, 0.05, len(idx)), index=idx, name="kwh")

now = pd.Timestamp("2026-11-01 20:00")
seen, offset = align_to_today(hourly, now)
check("the replayed readings stop at the hour before now", seen.index[-1] == now - pd.Timedelta(hours=1))
check("the weekday stays the same (whole weeks)", offset % pd.Timedelta(weeks=1) == pd.Timedelta(0))
check("the season stays the same (within a week)", abs(((now - offset).dayofyear - now.dayofyear + 183) % 366 - 183) <= 7)

check("Daily is due every day", is_due("Daily", now))
check("Weekly only on Saturdays", is_due("Weekly", pd.Timestamp("2026-10-31")) and not is_due("Weekly", now))
check("Monthly only on the 1st", is_due("Monthly", now) and not is_due("Monthly", pd.Timestamp("2026-11-02")))

for freq in ("Daily", "Weekly", "Monthly"):
    r = build_report(hourly, SameHourYesterday(), freq, now=now, budget_egp=300)
    fc = r["forecast"]
    check(f"{freq}: the forecast covers the rest of today and all of tomorrow",
          fc.index[0] == now and fc.index[-1] == r["tomorrow"] + pd.Timedelta(hours=23))
    expected = "higher" if r["change"] > 0.05 else "lower" if r["change"] < -0.05 else "about the same"
    check(f"{freq}: the higher / lower words match the numbers", r["trend"] == expected)
    check(f"{freq}: today and tomorrow are sensible amounts", 5 < r["today_kwh"] < 40 and 5 < r["tomorrow_kwh"] < 40)
    sms, wa, (subject, text, html) = as_sms(r), as_whatsapp(r), as_email(r)
    check(f"{freq}: every format says what tomorrow looks like", all("omorrow" in t for t in (sms, wa, subject, text, html)))
check("the SMS uses plain characters only", all(ord(ch) < 128 for ch in as_sms(r)))
check("the budget appears in the report", "300 EGP budget" in as_whatsapp(r))

check("a wrong email is refused before sending", not notify.send_gmail({"user": "x", "password": "y"}, "not-an-email", "s", "t")[0])
check("a phone without +country code is refused", not notify.send_whatsapp("01001234567", "key", "hi")[0])
check("WhatsApp needs the CallMeBot key", not notify.send_whatsapp("+201001234567", "", "hi")[0])
res = notify.send_report({"channels": ["Gmail", "SMS"], "email": "a@b.com", "phone": "+201001234567"},
                         {"sms": "x", "whatsapp": "x", "email": ("s", "t", None)}, None, None)
check("missing Gmail / Twilio settings give a clear message", [m.split(":")[0] for _, m in res] == ["Gmail", "SMS"]
      and not any(o for o, _ in res))

with tempfile.TemporaryDirectory() as tmp:
    f = Path(tmp) / "subs.json"
    notify.save_subscription("sara_1", {"frequency": "Weekly", "channels": ["Gmail"], "email": "s@x.com"}, f)
    notify.save_subscription("sara_1", {"frequency": "Daily", "channels": ["SMS"], "phone": "+201001234567"}, f)
    subs = notify.load_subscriptions(f)
    check("saving again replaces the old choice", list(subs) == ["sara_1"] and subs["sara_1"]["frequency"] == "Daily")

check("contacts are masked in the GitHub log", mask("habiba@gmail.com") == "ha***@gmail.com" and "100123" not in mask("+201001234567"))

print("\nRESULT:", "PASS" if ok else "FAIL")
