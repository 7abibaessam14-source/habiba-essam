"""
billing.py - electricity bill, month-end projection and energy-saving tips (no Streamlit here).

Egyptian household tariff from the August 2026 bills (piasters per kWh, here in EGP):
    0-50: 0.68 | 51-100: 0.98 | 101-200: 1.15 | 201-350: 1.73 | 351-650: 2.14 | 651-1000: 2.41 | >1000: 2.74
The bill is an ESTIMATE: every kWh is priced at the price of its tier (a progressive tariff). Real bills
can add fees and have extra subsidy rules, and the government changes the prices, so the app lets the
user edit them or use one fixed price.
"""

import math

import numpy as np
import pandas as pd

from i18n import AR, day_month

EGYPT_TIERS = [  # (upper kWh of the tier, EGP per kWh); None = no upper limit
    (50, 0.68),
    (100, 0.98),
    (200, 1.15),
    (350, 1.73),
    (650, 2.14),
    (1000, 2.41),
    (None, 2.74),
]

# Carbon footprint: Egypt's grid is mostly natural-gas power, about 0.45 kg CO2 per kWh (an estimate; the exact
# factor changes every year). One tree absorbs roughly 21 kg CO2 a year.
CO2_KG_PER_KWH = 0.45
CO2_KG_PER_TREE_YEAR = 21


def co2_kg(kwh):
    return max(float(kwh), 0.0) * CO2_KG_PER_KWH


def bill_egp(kwh, tiers=None, flat_price=None):
    """Cost in EGP of `kwh` in one month: tiered (each kWh at its tier's price) or one flat price."""
    kwh = max(float(kwh), 0.0)
    if flat_price is not None:
        return kwh * flat_price
    cost, lower = 0.0, 0.0
    for upper, price in tiers or EGYPT_TIERS:
        top = kwh if upper is None else min(kwh, upper)
        if top > lower:
            cost += (top - lower) * price
        if upper is None or kwh <= upper:
            break
        lower = upper
    return cost


def price_of_next_kwh(kwh, tiers=None, flat_price=None):
    """What one more kWh costs at this monthly consumption (used for 'how much would you save')."""
    if flat_price is not None:
        return flat_price
    for upper, price in tiers or EGYPT_TIERS:
        if upper is None or kwh < upper:
            return price
    return (tiers or EGYPT_TIERS)[-1][1]


def next_tier(kwh, tiers=None):
    """(upper kWh of the current tier, price of the next tier) or None when in the last tier."""
    t = tiers or EGYPT_TIERS
    for i, (upper, _) in enumerate(t):
        if upper is not None and kwh < upper and i + 1 < len(t):
            return upper, t[i + 1][1]
    return None


def daily_kwh(hourly):
    """kWh per day from an hourly kWh series; a day needs at least 12 hours of data."""
    return hourly.resample("D").sum(min_count=12)


def month_projection(hourly, as_of, next_24h_kwh=None, tiers=None, flat_price=None):
    """
    The month of `as_of`: kWh and EGP used so far (up to the end of that day), the expected month total
    and a low-high range, plus last month. The remaining days use the model's next-24-hour forecast for the
    first day (if given) and the average of the last 28 days for the others. The range grows with the
    number of remaining days and with how much daily use varied in the last 28 days.
    """
    day = pd.Timestamp(as_of).normalize()
    m_start = day.replace(day=1)
    m_end = (m_start + pd.offsets.MonthEnd(0)).normalize()
    d = daily_kwh(hourly.loc[: day + pd.Timedelta(hours=23)])

    used = float(d.loc[m_start:day].sum())
    recent = d.loc[:day].dropna().tail(28)
    typical = float(recent.mean()) if len(recent) else 0.0
    spread = float(recent.std()) if len(recent) > 1 else 0.0
    remaining = int((m_end - day).days)

    if remaining > 0:
        first = next_24h_kwh if next_24h_kwh is not None and not math.isnan(next_24h_kwh) else typical
        projected = used + first + typical * (remaining - 1)
    else:
        projected = used
    margin = spread * math.sqrt(remaining)
    low, high = max(projected - margin, used), projected + margin

    p_start = (m_start - pd.Timedelta(days=1)).replace(day=1)
    prev = d.loc[p_start: m_start - pd.Timedelta(days=1)]
    prev_kwh = float(prev.sum()) if prev.notna().sum() >= 20 else None  # only a (nearly) full month counts

    def cost(k):
        return bill_egp(k, tiers, flat_price)

    return {
        "month": m_start,
        "as_of": day,
        "days_in_month": int(m_end.day),
        "remaining_days": remaining,
        "used_kwh": used,
        "used_egp": cost(used),
        "projected_kwh": projected,
        "projected_egp": cost(projected),
        "low_egp": cost(low),
        "high_egp": cost(high),
        "typical_day_kwh": typical,
        "history_days": int(len(recent)),
        "prev_kwh": prev_kwh,
        "prev_egp": cost(prev_kwh) if prev_kwh is not None else None,
        "daily": d.loc[m_start:day],
    }


def saving_tips(hourly, proj, budget_egp=None, appliance_kwh=None, tiers=None, flat_price=None, lang="en"):
    """
    Short tips written from the user's own numbers. appliance_kwh: {name: kWh this month} when known.
    lang: "en" or "ar". Returns a list of (kind, text) with kind in {"warn", "good", "info"}.
    """
    ar = lang == "ar"
    tips = []
    day = proj["as_of"]
    d = daily_kwh(hourly.loc[: day + pd.Timedelta(hours=23)])
    today = d.get(day, np.nan)
    before = d.loc[: day - pd.Timedelta(days=1)].dropna().tail(28)
    if not math.isnan(today) and len(before) >= 7:
        avg = before.mean()
        change = (today - avg) / avg
        if change > 0.15:
            tips.append(("warn", f"استهلاكك يوم {day_month(day, 'ar')} كان أعلى من يومك المعتاد بنسبة {change:.0%} "
                                 f"({today:.1f} ك.و.س مقابل {avg:.1f} ك.و.س)." if ar else
                                 f"Your use on {day:%d %b} was {change:.0%} higher than your usual day "
                                 f"({today:.1f} kWh vs {avg:.1f} kWh)."))
        elif change < -0.15:
            tips.append(("good", f"أحسنت: استهلاكك يوم {day_month(day, 'ar')} كان أقل من يومك المعتاد بنسبة {-change:.0%} "
                                 f"({today:.1f} ك.و.س مقابل {avg:.1f} ك.و.س)." if ar else
                                 f"Well done: your use on {day:%d %b} was {-change:.0%} lower than your usual day "
                                 f"({today:.1f} kWh vs {avg:.1f} kWh)."))

    if budget_egp:
        over = proj["projected_egp"] - budget_egp
        if over > 0:
            tips.append(("warn", f"بهذا المعدل ستصل فاتورتك إلى حوالي {proj['projected_egp']:,.0f} جنيه، أي أعلى من "
                                 f"ميزانيتك ({budget_egp:,.0f} جنيه) بـ{over:,.0f} جنيه." if ar else
                                 f"At this rate your bill will reach about {proj['projected_egp']:,.0f} EGP, "
                                 f"{over:,.0f} EGP over your budget of {budget_egp:,.0f} EGP."))
        else:
            tips.append(("good", f"أنت على المسار الصحيح: المتوقع حوالي {proj['projected_egp']:,.0f} جنيه، أقل من "
                                 f"ميزانيتك بـ{-over:,.0f} جنيه." if ar else
                                 f"You are on track: about {proj['projected_egp']:,.0f} EGP expected, "
                                 f"{-over:,.0f} EGP under your budget."))

    if flat_price is None:
        nt = next_tier(proj["projected_kwh"], tiers)
        if nt and nt[0] - proj["projected_kwh"] < 0.1 * nt[0]:
            tips.append(("warn", f"اقتربت من الشريحة التالية: بعد {nt[0]:,} ك.و.س يصبح سعر كل ك.و.س {nt[1]:.2f} جنيه. "
                                 f"التوفير البسيط الآن يجعل الشهر كله أرخص." if ar else
                                 f"You are close to the next price tier: after {nt[0]:,} kWh each kWh costs "
                                 f"{nt[1]:.2f} EGP. Saving a little now keeps the whole month cheaper."))

    last14 = hourly.loc[day - pd.Timedelta(days=13): day + pd.Timedelta(hours=23)].dropna()
    if len(last14) > 24 * 7:
        night = last14[(last14.index.hour >= 1) & (last14.index.hour < 5)].mean()
        overall = last14.mean()
        if overall > 0 and night > 0.5 * overall:
            tips.append(("warn", f"بين الساعة 1 و5 فجرًا ما زلت تستهلك حوالي {night:.2f} ك.و.س في الساعة "
                                 f"({night / overall:.0%} من متوسط ساعتك). تأكد من الأجهزة التي تُترك تعمل ليلًا." if ar else
                                 f"Between 1 and 5 AM you still use about {night:.2f} kWh per hour "
                                 f"({night / overall:.0%} of your average hour). Check for devices left on at night."))
        peak = last14.groupby(last14.index.hour).mean().idxmax()
        tips.append(("info", f"أكثر ساعة استهلاكًا عندك حوالي الساعة {peak:02d}:00. تشغيل الغسالة أو السخان في وقت آخر "
                             f"يوزّع الحمل." if ar else
                             f"Your busiest hour is around {peak:02d}:00. Moving washing or water heating "
                             f"away from that hour spreads the load."))

    real = {k: v for k, v in (appliance_kwh or {}).items() if not k.startswith("Other")}  # "Other" is not a device
    if real:
        total = sum(appliance_kwh.values())
        name, kwh = max(real.items(), key=lambda kv: kv[1])
        if total > 0:
            price = price_of_next_kwh(proj["projected_kwh"], tiers, flat_price)
            saving = 0.10 * kwh * price
            tips.append(("info", f"{AR.get(name, name)} يستهلك {kwh / total:.0%} من طاقتك هذا الشهر. تقليل استخدامه 10% "
                                 f"يوفر حوالي {saving:,.0f} جنيه في الشهر." if ar else
                                 f"{name} uses {kwh / total:.0%} of your energy this month. Using it 10% less "
                                 f"would save about {saving:,.0f} EGP a month."))
    return tips
