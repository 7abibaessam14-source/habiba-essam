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
from i18n import day_month, month_name, weekday
from ml_models import forecast_next
from uci_loader import APPLIANCE_COLUMNS
import weather as wx

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


def today_series(hourly, model, now):
    """
    Today's simulated meter, used by the reports and the Bill page:
    (readings up to `now` + the model's forecast for the rest of today, the forecast from `now` to the end of
    tomorrow, the offset used by align_to_today). Gaps in the readings are filled before forecasting.
    """
    seen, offset = align_to_today(hourly, now)
    tomorrow = now.normalize() + DAY
    filled = seen.reindex(pd.date_range(seen.index[0], now - HOUR, freq="h")).interpolate(limit_direction="both")
    fc = forecast_next(model, filled, steps=48 - now.hour)
    return pd.concat([seen, fc.loc[: tomorrow - HOUR]]), fc, offset


def appliance_kwh(fact, offset, start, end):
    """{appliance: kWh} between start and end (today's calendar) when the data has sub-meters (UCI), else None."""
    cols = list(APPLIANCE_COLUMNS)
    if fact is None or not set(cols) <= set(fact.columns):
        return None
    t = fact["DateTime"] + offset
    s = fact.loc[(t >= start) & (t < end), cols].sum()
    return {APPLIANCE_COLUMNS[c]: float(s[c]) for c in cols} if s.sum() > 0 else None


def build_report(hourly, model, frequency="Daily", now=None, tiers=None, flat_price=None, budget_egp=None, fact=None,
                 lang="en", weather=None):
    """All the numbers of one report. `fact` (optional) adds the appliance tip; budget_egp the budget line;
    lang ("en" / "ar") is the language of the tips and of every text made from the result;
    weather (optional, from weather.fetch_tomorrow) adds tomorrow's weather and the tips that fit it."""
    now = (now if now is not None else now_local()).floor("h")
    today, tomorrow = now.normalize(), now.normalize() + DAY

    # today complete (readings + the model for the hours left) and the forecast for tomorrow
    full, fc, offset = today_series(hourly, model, now)
    today_kwh = float(full.loc[today:].sum())
    tomorrow_kwh = float(fc.loc[tomorrow:].sum())
    change = (tomorrow_kwh - today_kwh) / today_kwh if today_kwh > 0 else 0.0
    trend = "higher" if change > SAME else "lower" if change < -SAME else "about the same"

    proj = month_projection(full, today, tomorrow_kwh, tiers, flat_price)
    price = proj["projected_egp"] / proj["projected_kwh"] if proj["projected_kwh"] > 0 else bill_egp(1, tiers, flat_price)

    days = daily_kwh(full)
    if frequency == "Weekly":
        key, start = "week", today - 6 * DAY
        cur, prev = days.loc[start: today], days.loc[today - 13 * DAY: today - 7 * DAY]
        prev_kwh = float(prev.sum(min_count=1)) if prev.notna().any() else None
    elif frequency == "Monthly":
        key, start = "month", today - 29 * DAY
        cur, prev = days.loc[start: today], days.loc[today - 59 * DAY: today - 30 * DAY]
        prev_kwh = float(prev.sum(min_count=1)) if prev.notna().any() else None
    else:
        key, start = "day", today
        cur, prev = days.loc[today:today], days.loc[today - 28 * DAY: today - DAY].dropna()
        prev_kwh = float(prev.mean()) if len(prev) else None
    period_kwh = float(cur.sum())
    period_change = (period_kwh - prev_kwh) / prev_kwh if prev_kwh else None

    appl = appliance_kwh(fact, offset, proj["month"], now)
    tips = saving_tips(full, proj, None, appl, tiers, flat_price, lang)  # no budget tip: the report has its own budget line
    tips = sorted(tips, key=lambda t: {"warn": 0, "info": 1, "good": 2}[t[0]])[:3]  # warnings first

    data = hourly.dropna().index
    return {
        "lang": lang,
        "frequency": frequency,
        "now": now,
        "today": today,
        "tomorrow": tomorrow,
        "period_key": key,
        "period_start": start,
        "period_kwh": period_kwh,
        "period_egp": period_kwh * price,
        "period_change": period_change,
        "today_kwh": today_kwh,
        "today_egp": today_kwh * price,
        "tomorrow_kwh": tomorrow_kwh,
        "tomorrow_egp": tomorrow_kwh * price,
        "change": change,
        "trend": trend,  # always the English word; the texts below translate it
        "proj": proj,
        "co2_month_kg": co2_kg(proj["projected_kwh"]),
        "budget": budget_egp,
        "tips": tips,
        "weather": weather,
        "weather_line": wx.summary(weather, lang) if weather else None,
        "weather_tips": wx.tips(weather, lang) if weather else [],
        "forecast": fc,
        "years": (data.min().year, data.max().year),
    }


# ---- the texts, in English or Arabic (r["lang"]) ------------------------------------
AR_FREQ = {"Daily": "اليومي", "Weekly": "الأسبوعي", "Monthly": "الشهري"}
AR_TREND = {"higher": "أعلى", "lower": "أقل"}


def period_label(r):
    t, s, ar = r["today"], r["period_start"], r["lang"] == "ar"
    if r["period_key"] == "day":
        return f"اليوم ({weekday(t, 'ar')} {day_month(t, 'ar')})" if ar else f"Today ({t:%a %d %b})"
    n = 7 if r["period_key"] == "week" else 30
    if ar:
        return f"آخر {n} {'أيام' if n == 7 else 'يومًا'} ({day_month(s, 'ar')} – {day_month(t, 'ar')})"
    return f"Last {n} days ({s:%d %b}–{t:%d %b})"


def compare_label(r):
    en = {"day": "your usual day", "week": "the week before", "month": "the 30 days before"}
    ar = {"day": "يومك المعتاد", "week": "الأسبوع السابق", "month": "الـ30 يومًا السابقة"}
    return (ar if r["lang"] == "ar" else en)[r["period_key"]]


def note(r):
    y1, y2 = r["years"]
    if r["lang"] == "ar":
        return f"عداد محاكى: تعلّم النموذج من قراءات حقيقية من {y1} إلى {y2} ويعيد تشغيلها على تاريخ اليوم."
    return f"Simulated meter: the model learned from real readings of {y1}–{y2} and replays them on today's date."


def _tomorrow_line(r):
    ar = r["lang"] == "ar"
    if r["trend"] == "about the same":
        return (f"تقريبًا مثل اليوم ({r['tomorrow_kwh']:.1f} ك.و.س)" if ar
                else f"about the same as today ({r['tomorrow_kwh']:.1f} kWh)")
    if ar:
        return f"{r['tomorrow_kwh']:.1f} ك.و.س، {AR_TREND[r['trend']]} من اليوم بنسبة {abs(r['change']):.0%}"
    return f"{r['tomorrow_kwh']:.1f} kWh, {r['trend'].upper()} than today by {abs(r['change']):.0%}"


def _budget_line(r):
    p, b, ar = r["proj"], r["budget"], r["lang"] == "ar"
    if not b:
        return f"المتوقع {p['projected_egp']:,.0f} جنيه" if ar else f"expected {p['projected_egp']:,.0f} EGP"
    over = p["projected_egp"] - b
    if ar:
        return (f"المتوقع {p['projected_egp']:,.0f} جنيه، أي أعلى من ميزانيتك ({b:,.0f} جنيه) بـ{over:,.0f} جنيه" if over > 0
                else f"المتوقع {p['projected_egp']:,.0f} جنيه، ضمن ميزانيتك ({b:,.0f} جنيه)")
    return (f"expected {p['projected_egp']:,.0f} EGP, {over:,.0f} EGP OVER your {b:,.0f} EGP budget" if over > 0
            else f"expected {p['projected_egp']:,.0f} EGP, within your {b:,.0f} EGP budget")


def _change(r):
    if r["period_change"] is None:
        return ""
    if r["lang"] == "ar":
        return f" ({r['period_change']:+.0%} مقارنة مع {compare_label(r)})"
    return f" ({r['period_change']:+.0%} vs {compare_label(r)})"


def as_sms(r):
    """Short text (about 2 SMS in English; Arabic SMS parts are shorter, 67 characters each)."""
    w = r.get("weather")
    # with the weather, its first tip replaces the bill tip (SMS is short)
    tip = r["weather_tips"][0] if r.get("weather_tips") else (r["tips"][0][1] if r["tips"] else "")
    if r["lang"] == "ar":
        wtxt = f" الطقس غدًا {w['tmax']:.0f} درجة." if w else ""
        return (f"تقرير الطاقة {AR_FREQ[r['frequency']]}. {period_label(r)}: {r['period_kwh']:.0f} ك.و.س ~{r['period_egp']:.0f} "
                f"جنيه{_change(r)}. الغد: {_tomorrow_line(r)}. الشهر: {_budget_line(r)}.{wtxt}" + (f" نصيحة: {tip}" if tip else ""))
    wtxt = f" Weather tomorrow: {w['tmax']:.0f}C." if w else ""
    text = (f"AmperEC {r['frequency'].lower()} report. {period_label(r)}: {r['period_kwh']:.0f} kWh "
            f"~{r['period_egp']:.0f} EGP{_change(r)}. Tomorrow: {_tomorrow_line(r)}. Month: {_budget_line(r)}.{wtxt}"
            + (f" Tip: {tip}" if tip else ""))
    text = text.replace("°C", "C").replace("°", "")
    return text.replace("–", "-")  # plain characters: one non-English character makes every SMS part shorter


def sms_parts(text):
    """How many SMS messages the text needs (153 plain characters per part, 67 when it has Arabic or emoji)."""
    per = 153 if all(ord(c) < 128 for c in text) else 67
    return max(1, math.ceil(len(text) / per))


def as_whatsapp(r):
    """Medium text with emojis; *text* is bold on WhatsApp."""
    p, ar = r["proj"], r["lang"] == "ar"
    arrow = {"higher": "📈", "lower": "📉"}.get(r["trend"], "➡️")
    budget_icon = " ⚠️" if r["budget"] and p["projected_egp"] > r["budget"] else ""
    if ar:
        lines = [
            f"⚡ *AmperEC – التقرير {AR_FREQ[r['frequency']]}* ({day_month(r['today'], 'ar')} {r['today'].year})",
            f"{period_label(r)}: *{r['period_kwh']:.1f} ك.و.س* ≈ {r['period_egp']:,.0f} جنيه{_change(r)}",
            f"{arrow} الغد: {_tomorrow_line(r)}",
            f"🧾 {month_name(p['month'], 'ar')}: {p['used_egp']:,.0f} جنيه حتى الآن، {_budget_line(r)}{budget_icon}",
            f"🌍 الكربون: حوالي {r['co2_month_kg']:,.0f} كجم CO₂ هذا الشهر",
        ]
        tips_title = "💡 *نصائح:*"
    else:
        lines = [
            f"⚡ *AmperEC – {r['frequency']} report* ({r['today']:%d %b %Y})",
            f"{period_label(r)}: *{r['period_kwh']:.1f} kWh* ≈ {r['period_egp']:,.0f} EGP{_change(r)}",
            f"{arrow} Tomorrow: {_tomorrow_line(r)}",
            f"🧾 {p['month']:%B}: {p['used_egp']:,.0f} EGP so far, {_budget_line(r)}{budget_icon}",
            f"🌍 Carbon: about {r['co2_month_kg']:,.0f} kg CO₂ this month",
        ]
        tips_title = "💡 *Tips:*"
    if r.get("weather_line"):
        lines.append(f"{r['weather_line']}")
        lines.append("🌤️ *نصائح حسب طقس الغد:*" if ar else "🌤️ *Tips for tomorrow's weather:*")
        lines += [f"• {text}" for text in r["weather_tips"]]
    if r["tips"]:
        lines.append(tips_title)
        lines += [f"• {text}" for _, text in r["tips"]]
    lines.append(f"_{note(r)}_")
    return "\n".join(lines)


def as_email(r):
    """(subject, plain text, HTML)."""
    p, ar = r["proj"], r["lang"] == "ar"
    if ar:
        tomorrow_word = {"higher": "أعلى من اليوم", "lower": "أقل من اليوم"}.get(r["trend"], "تقريبًا مثل اليوم")
        title = f"AmperEC – التقرير {AR_FREQ[r['frequency']]}"
        date = f"{weekday(r['today'], 'ar')} {day_month(r['today'], 'ar')} {r['today'].year}"
        subject = f"تقرير AmperEC {AR_FREQ[r['frequency']]} – {day_month(r['today'], 'ar')} {r['today'].year}: الغد {tomorrow_word}"
        if r.get("weather"):
            subject += f"، {r['weather']['tmax']:.0f} درجة"
        month = month_name(p["month"], "ar")
        rows = [
            (period_label(r), f"{r['period_kwh']:.1f} ك.و.س ≈ {r['period_egp']:,.0f} جنيه{_change(r)}"),
            ("اليوم (الإجمالي المتوقع)", f"{r['today_kwh']:.1f} ك.و.س ≈ {r['today_egp']:,.0f} جنيه"),
            ("الغد (تنبؤ)", f"{r['tomorrow_kwh']:.1f} ك.و.س ≈ {r['tomorrow_egp']:,.0f} جنيه – {tomorrow_word}"),
            (f"{month} حتى الآن", f"{p['used_kwh']:,.0f} ك.و.س ≈ {p['used_egp']:,.0f} جنيه"),
            (f"فاتورة {month}", _budget_line(r)),
            ("البصمة الكربونية", f"حوالي {r['co2_month_kg']:,.0f} كجم CO₂ هذا الشهر"),
        ]
        tips_title, direction, align = "💡 نصائح", "rtl", "right"
        wtitle = "🌤️ نصائح حسب طقس الغد"
    else:
        tomorrow_word = {"higher": "higher than today", "lower": "lower than today"}.get(r["trend"], "about the same as today")
        title = f"AmperEC – {r['frequency']} report"
        date = f"{r['today']:%A %d %B %Y}"
        subject = f"AmperEC {r['frequency'].lower()} report – {r['today']:%d %b %Y}: tomorrow {tomorrow_word}"
        if r.get("weather"):
            subject += f", {r['weather']['tmax']:.0f}°C"
        rows = [
            (period_label(r), f"{r['period_kwh']:.1f} kWh ≈ {r['period_egp']:,.0f} EGP{_change(r)}"),
            ("Today (expected total)", f"{r['today_kwh']:.1f} kWh ≈ {r['today_egp']:,.0f} EGP"),
            ("Tomorrow (forecast)", f"{r['tomorrow_kwh']:.1f} kWh ≈ {r['tomorrow_egp']:,.0f} EGP – {tomorrow_word}"
                                    if r["trend"] != "about the same" else f"{r['tomorrow_kwh']:.1f} kWh – {tomorrow_word}"),
            (f"{p['month']:%B} so far", f"{p['used_kwh']:,.0f} kWh ≈ {p['used_egp']:,.0f} EGP"),
            (f"{p['month']:%B} bill", _budget_line(r)),
            ("Carbon footprint", f"about {r['co2_month_kg']:,.0f} kg CO₂ this month"),
        ]
        tips_title, direction, align = "💡 Tips", "ltr", "left"
        wtitle = "🌤️ Tips for tomorrow's weather"
    wtips = r.get("weather_tips") or []
    text = "\n".join([f"{title} ({date})", ""]
                     + [f"{k}: {v}" for k, v in rows]
                     + ([""] + [r["weather_line"], wtitle] + [f"- {t}" for t in wtips] if wtips else [])
                     + ([""] + [tips_title] + [f"- {t}" for _, t in r["tips"]] if r["tips"] else [])
                     + ["", note(r)])
    e = html.escape
    cell = "padding:4px 0 4px 12px" if direction == "rtl" else "padding:4px 12px 4px 0"
    table = "".join(f"<tr><td style='{cell};color:#555'>{e(k)}</td><td style='padding:4px 0'><b>{e(v)}</b></td></tr>"
                    for k, v in rows)
    tips = "".join(f"<li>{e(t)}</li>" for _, t in r["tips"])
    weather_html = ""
    if wtips:
        items = "".join(f"<li>{e(t)}</li>" for t in wtips)
        weather_html = (f"<div style='margin:16px 0 0;padding:10px 14px;background:#eef8f7;border-radius:10px'>"
                        f"<p style='margin:0 0 6px'><b>{e(r['weather_line'])}</b></p>"
                        f"<h3 style='margin:8px 0 4px'>{wtitle}</h3><ul style='margin:0'>{items}</ul></div>")
    body = (f"<div dir='{direction}' style='font-family:Arial,sans-serif;font-size:14px;color:#1b2430;text-align:{align}'>"
            f"<h2 style='color:#0e7c86;margin:0 0 8px'>⚡ {e(title)}</h2>"
            f"<p style='margin:0 0 12px;color:#555'>{e(date)}</p><table>{table}</table>"
            + weather_html
            + (f"<h3 style='margin:16px 0 4px'>{tips_title}</h3><ul>{tips}</ul>" if tips else "")
            + f"<p style='color:#888;font-size:12px'>{e(note(r))}</p></div>")
    return subject, text, body
