"""
reports.py - the daily / weekly / monthly energy report, as SMS, WhatsApp and email text (no Streamlit here).

The model is trained on the whole history (UCI: Dec 2006 - Nov 2010). The report is about TODAY's real date:
align_to_today() lays the history onto the current calendar, moved by whole weeks so a Monday stays a Monday
and November stays November, and treats it as a simulated meter whose readings have arrived up to the current
hour. The trained model then forecasts the rest of today and all of tomorrow, and the report says if tomorrow
will be higher or lower than today. With a real smart meter, align_to_today() is replaced by its readings.
"""

import html
import math

import pandas as pd

from billing import bill_egp, co2_kg, daily_kwh, month_projection, saving_tips
from ml_models import forecast_next
from uci_loader import APPLIANCE_COLUMNS

TIMEZONE = "Africa/Cairo"
FREQUENCIES = ("Daily", "Weekly", "Monthly")
CHANNELS = ("Gmail", "WhatsApp", "SMS")
SAME = 0.05  # tomorrow within +-5 % of today counts as "about the same"
HOUR = pd.Timedelta(hours=1)
DAY = pd.Timedelta(days=1)


def now_local():
    """The current time in Egypt, without the time zone (the data has none)."""
    return pd.Timestamp.now(tz=TIMEZONE).tz_localize(None)


def is_due(frequency, now):
    """Daily: every day. Weekly: Saturdays. Monthly: the 1st of the month."""
    return frequency == "Daily" or (frequency == "Weekly" and now.dayofweek == 5) or (frequency == "Monthly" and now.day == 1)


def history_offset(index, now):
    """How far to move the history so that `now` falls on the same season and weekday inside it."""
    first, last = index.min(), index.max()
    for years in range(1, 60):
        offset = pd.Timedelta(weeks=round(years * 365.2425 / 7))  # whole weeks: the weekday stays the same
        if first + pd.Timedelta(days=35) <= now - offset <= last:
            return offset
    return pd.Timedelta(weeks=math.ceil((now - last) / pd.Timedelta(weeks=1)))  # less than a year of data


def align_to_today(hourly, now):
    """(the history on today's calendar with readings up to the hour before `now`, the offset used)."""
    offset = history_offset(hourly.dropna().index, now)
    moved = hourly.copy()
    moved.index = moved.index + offset
    return moved.loc[: now - HOUR], offset


def appliance_kwh(fact, offset, start, end):
    """{appliance: kWh} between start and end (today's calendar) when the data has sub-meters (UCI), else None."""
    cols = list(APPLIANCE_COLUMNS)
    if fact is None or not set(cols) <= set(fact.columns):
        return None
    t = fact["DateTime"] + offset
    s = fact.loc[(t >= start) & (t < end), cols].sum()
    return {APPLIANCE_COLUMNS[c]: float(s[c]) for c in cols} if s.sum() > 0 else None


def build_report(hourly, model, frequency="Daily", now=None, tiers=None, flat_price=None, budget_egp=None, fact=None):
    """All the numbers of one report. `fact` (optional) adds the appliance tip; budget_egp the budget line."""
    now = (now if now is not None else now_local()).floor("h")
    seen, offset = align_to_today(hourly, now)
    today, tomorrow = now.normalize(), now.normalize() + DAY

    # the model forecasts the hours left today and all of tomorrow (gaps in the readings are filled first)
    filled = seen.reindex(pd.date_range(seen.index[0], now - HOUR, freq="h")).interpolate(limit_direction="both")
    fc = forecast_next(model, filled, steps=48 - now.hour)
    today_kwh = float(seen.loc[today:].sum()) + float(fc.loc[: tomorrow - HOUR].sum())
    tomorrow_kwh = float(fc.loc[tomorrow:].sum())
    change = (tomorrow_kwh - today_kwh) / today_kwh if today_kwh > 0 else 0.0
    trend = "higher" if change > SAME else "lower" if change < -SAME else "about the same"

    full = pd.concat([seen, fc.loc[: tomorrow - HOUR]])  # today complete: readings + the model for the hours left
    proj = month_projection(full, today, tomorrow_kwh, tiers, flat_price)
    price = proj["projected_egp"] / proj["projected_kwh"] if proj["projected_kwh"] > 0 else bill_egp(1, tiers, flat_price)

    days = daily_kwh(full)
    if frequency == "Weekly":
        cur, prev = days.loc[today - 6 * DAY: today], days.loc[today - 13 * DAY: today - 7 * DAY]
        period, compare = f"Last 7 days ({today - 6 * DAY:%d %b}–{today:%d %b})", "the week before"
        prev_kwh = float(prev.sum(min_count=1)) if prev.notna().any() else None
    elif frequency == "Monthly":
        cur, prev = days.loc[today - 29 * DAY: today], days.loc[today - 59 * DAY: today - 30 * DAY]
        period, compare = f"Last 30 days ({today - 29 * DAY:%d %b}–{today:%d %b})", "the 30 days before"
        prev_kwh = float(prev.sum(min_count=1)) if prev.notna().any() else None
    else:
        cur, prev = days.loc[today:today], days.loc[today - 28 * DAY: today - DAY].dropna()
        period, compare = f"Today ({today:%a %d %b})", "your usual day"
        prev_kwh = float(prev.mean()) if len(prev) else None
    period_kwh = float(cur.sum())
    period_change = (period_kwh - prev_kwh) / prev_kwh if prev_kwh else None

    appl = appliance_kwh(fact, offset, proj["month"], now)
    tips = saving_tips(full, proj, None, appl, tiers, flat_price)  # no budget tip: the report has its own budget line
    tips = sorted(tips, key=lambda t: {"warn": 0, "info": 1, "good": 2}[t[0]])[:3]  # warnings first

    data = hourly.dropna().index
    return {
        "frequency": frequency,
        "now": now,
        "today": today,
        "tomorrow": tomorrow,
        "period": period,
        "period_kwh": period_kwh,
        "period_egp": period_kwh * price,
        "compare": compare,
        "period_change": period_change,
        "today_kwh": today_kwh,
        "today_egp": today_kwh * price,
        "tomorrow_kwh": tomorrow_kwh,
        "tomorrow_egp": tomorrow_kwh * price,
        "change": change,
        "trend": trend,
        "proj": proj,
        "co2_month_kg": co2_kg(proj["projected_kwh"]),
        "budget": budget_egp,
        "tips": tips,
        "forecast": fc,
        "note": f"Simulated meter: the model learned from real readings of {data.min():%Y}–{data.max():%Y} "
                "and replays them on today's date.",
    }


def _tomorrow_line(r):
    if r["trend"] == "about the same":
        return f"about the same as today ({r['tomorrow_kwh']:.1f} kWh)"
    return f"{r['tomorrow_kwh']:.1f} kWh, {r['trend'].upper()} than today by {abs(r['change']):.0%}"


def _budget_line(r):
    p = r["proj"]
    if not r["budget"]:
        return f"expected {p['projected_egp']:,.0f} EGP"
    over = p["projected_egp"] - r["budget"]
    return (f"expected {p['projected_egp']:,.0f} EGP, {over:,.0f} EGP OVER your {r['budget']:,.0f} EGP budget" if over > 0
            else f"expected {p['projected_egp']:,.0f} EGP, within your {r['budget']:,.0f} EGP budget")


def _change(r):
    return "" if r["period_change"] is None else f" ({r['period_change']:+.0%} vs {r['compare']})"


def as_sms(r):
    """Short text (about 2 SMS)."""
    tip = f" Tip: {r['tips'][0][1]}" if r["tips"] else ""
    text = (f"Smart Energy {r['frequency'].lower()} report. {r['period']}: {r['period_kwh']:.0f} kWh ~{r['period_egp']:.0f} EGP"
            f"{_change(r)}. Tomorrow: {_tomorrow_line(r)}. Month: {_budget_line(r)}.{tip}")
    return text.replace("–", "-")  # plain characters: one non-English character makes every SMS part shorter


def as_whatsapp(r):
    """Medium text with emojis; *text* is bold on WhatsApp."""
    p = r["proj"]
    arrow = {"higher": "📈", "lower": "📉"}.get(r["trend"], "➡️")
    budget_icon = " ⚠️" if r["budget"] and p["projected_egp"] > r["budget"] else ""
    lines = [
        f"⚡ *Smart Energy – {r['frequency']} report* ({r['today']:%d %b %Y})",
        f"{r['period']}: *{r['period_kwh']:.1f} kWh* ≈ {r['period_egp']:,.0f} EGP{_change(r)}",
        f"{arrow} Tomorrow: {_tomorrow_line(r)}",
        f"🧾 {p['month']:%B}: {p['used_egp']:,.0f} EGP so far, {_budget_line(r)}{budget_icon}",
        f"🌍 Carbon: about {r['co2_month_kg']:,.0f} kg CO₂ this month",
    ]
    if r["tips"]:
        lines.append("💡 *Tips:*")
        lines += [f"• {text}" for _, text in r["tips"]]
    lines.append(f"_{r['note']}_")
    return "\n".join(lines)


def as_email(r):
    """(subject, plain text, HTML)."""
    subject = f"Smart Energy {r['frequency'].lower()} report – {r['today']:%d %b %Y}: tomorrow {r['trend']} than today"
    if r["trend"] == "about the same":
        subject = f"Smart Energy {r['frequency'].lower()} report – {r['today']:%d %b %Y}: tomorrow about the same as today"
    p = r["proj"]
    rows = [
        (r["period"], f"{r['period_kwh']:.1f} kWh ≈ {r['period_egp']:,.0f} EGP{_change(r)}"),
        ("Today (expected total)", f"{r['today_kwh']:.1f} kWh ≈ {r['today_egp']:,.0f} EGP"),
        ("Tomorrow (forecast)", f"{r['tomorrow_kwh']:.1f} kWh ≈ {r['tomorrow_egp']:,.0f} EGP – {r['trend']} than today"
                                if r["trend"] != "about the same" else f"{r['tomorrow_kwh']:.1f} kWh – about the same as today"),
        (f"{p['month']:%B} so far", f"{p['used_kwh']:,.0f} kWh ≈ {p['used_egp']:,.0f} EGP"),
        (f"{p['month']:%B} bill", _budget_line(r)),
        ("Carbon footprint", f"about {r['co2_month_kg']:,.0f} kg CO₂ this month"),
    ]
    text = "\n".join([f"Smart Energy – {r['frequency']} report ({r['today']:%d %b %Y})", ""]
                     + [f"{k}: {v}" for k, v in rows]
                     + ([""] + ["Tips:"] + [f"- {t}" for _, t in r["tips"]] if r["tips"] else [])
                     + ["", r["note"]])
    e = html.escape
    table = "".join(f"<tr><td style='padding:4px 12px 4px 0;color:#555'>{e(k)}</td><td style='padding:4px 0'><b>{e(v)}</b></td></tr>"
                    for k, v in rows)
    tips = "".join(f"<li>{e(t)}</li>" for _, t in r["tips"])
    body = (f"<div style='font-family:Arial,sans-serif;font-size:14px;color:#1b2430'>"
            f"<h2 style='color:#0e7c86;margin:0 0 8px'>⚡ Smart Energy – {e(r['frequency'])} report</h2>"
            f"<p style='margin:0 0 12px;color:#555'>{r['today']:%A %d %B %Y}</p><table>{table}</table>"
            + (f"<h3 style='margin:16px 0 4px'>💡 Tips</h3><ul>{tips}</ul>" if tips else "")
            + f"<p style='color:#888;font-size:12px'>{e(r['note'])}</p></div>")
    return subject, text, body
