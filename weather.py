"""
weather.py - tomorrow's weather for an Egyptian city and saving tips that fit it (English / Arabic).

The forecast comes from Open-Meteo (https://open-meteo.com): free, no account and no key. Used by the evening
report (send_reports.py, reports.py), the Home page and the Bill page of the website, and the evening phone
notification. The AmperEC phone app has the same rules in app/app.js. No secrets here.
Tips are advice only: they name no saving percentages, because those depend on each home and device.
"""

from datetime import date, timedelta

import requests

API = "https://api.open-meteo.com/v1/forecast"
TIMEOUT = 10
DEFAULT_CITY = "Cairo"
# name: (latitude, longitude, Arabic name)
CITIES = {
    "Cairo": (30.0444, 31.2357, "القاهرة"),
    "Giza": (30.0131, 31.2089, "الجيزة"),
    "Alexandria": (31.2001, 29.9187, "الإسكندرية"),
    "Port Said": (31.2653, 32.3019, "بورسعيد"),
    "Ismailia": (30.5965, 32.2715, "الإسماعيلية"),
    "Suez": (29.9668, 32.5498, "السويس"),
    "Mansoura": (31.0409, 31.3785, "المنصورة"),
    "Tanta": (30.7865, 31.0004, "طنطا"),
    "Zagazig": (30.5877, 31.5020, "الزقازيق"),
    "Asyut": (27.1809, 31.1837, "أسيوط"),
    "Luxor": (25.6872, 32.6396, "الأقصر"),
    "Aswan": (24.0889, 32.8998, "أسوان"),
    "Hurghada": (27.2579, 33.8116, "الغردقة"),
    "Sharm El Sheikh": (27.9158, 34.3299, "شرم الشيخ"),
}

# WMO weather codes (what Open-Meteo returns) -> (emoji, English, Arabic)
_SKY = [
    ((0,), "☀️", "clear sky", "صافٍ"),
    ((1, 2), "🌤️", "partly cloudy", "غائم جزئيًا"),
    ((3,), "☁️", "cloudy", "غائم"),
    ((45, 48), "☁️", "fog", "شبورة"),  # the fog emoji is missing on some phones and PCs
    ((51, 53, 55, 56, 57), "🌦️", "drizzle", "رذاذ"),
    ((61, 63, 65, 66, 67, 80, 81, 82), "🌧️", "rain", "أمطار"),
    ((71, 73, 75, 77, 85, 86), "❄️", "snow", "ثلوج"),
    ((95, 96, 99), "⛈️", "thunderstorm", "عواصف رعدية"),
]


def city_name(city, lang="en"):
    return CITIES[city][2] if lang == "ar" and city in CITIES else city


def sky(code, lang="en"):
    """(emoji, words) for a WMO weather code."""
    for codes, emoji, en, ar in _SKY:
        if code in codes:
            return emoji, (ar if lang == "ar" else en)
    return "🌡️", ("" if lang == "ar" else "")


def fetch_tomorrow(city=DEFAULT_CITY, today=None, timeout=TIMEOUT):
    """{"city", "date", "tmax", "tmin", "rain", "code"} for tomorrow (Cairo time), or None when it cannot be fetched."""
    if city not in CITIES:
        city = DEFAULT_CITY
    lat, lon, _ = CITIES[city]
    tomorrow = (today or date.today()) + timedelta(days=1)
    try:
        r = requests.get(API, params={
            "latitude": lat, "longitude": lon, "timezone": "Africa/Cairo", "forecast_days": 3,
            "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max,weather_code",
        }, timeout=timeout)
        r.raise_for_status()
        d = r.json()["daily"]
        i = d["time"].index(tomorrow.isoformat())
        rain = d.get("precipitation_probability_max", [None] * len(d["time"]))[i]
        return {"city": city, "date": tomorrow.isoformat(), "tmax": float(d["temperature_2m_max"][i]),
                "tmin": float(d["temperature_2m_min"][i]), "rain": None if rain is None else int(rain),
                "code": int(d["weather_code"][i])}
    except (requests.RequestException, ValueError, KeyError, TypeError, IndexError):
        return None


def band(w):
    """very_hot / hot / mild / cool / cold, from tomorrow's highest and lowest temperature."""
    if w["tmax"] >= 35:
        return "very_hot"
    if w["tmax"] >= 30:
        return "hot"
    if w["tmin"] < 10:
        return "cold"
    if w["tmax"] < 22:
        return "cool"
    return "mild"


def summary(w, lang="en"):
    """One line: 'Tomorrow in Cairo: ☀️ clear sky, 34° / 22°C'."""
    emoji, words = sky(w["code"], lang)
    temps = f"{w['tmax']:.0f}° / {w['tmin']:.0f}°C"
    rain = w.get("rain")
    if lang == "ar":
        line = f"غدًا في {city_name(w['city'], 'ar')}: {emoji} {words}، {temps}"
        return line + (f"، احتمال المطر {rain}%" if rain and rain >= 30 else "")
    line = f"Tomorrow in {w['city']}: {emoji} {words}, {temps}"
    return line + (f", {rain}% chance of rain" if rain and rain >= 30 else "")


def tips(w, lang="en"):
    """2-3 short saving tips that fit tomorrow's weather."""
    hi, lo = f"{w['tmax']:.0f}°C", f"{w['tmin']:.0f}°C"
    b = band(w)
    if lang == "ar":
        t = {
            "very_hot": [f"غدًا حار جدًا ({hi}): اضبط التكييف على 24-25 درجة بدل 18؛ كل درجة أعلى تستهلك كهرباء أقل.",
                         "اقفل الستائر على الشبابيك اللي عليها شمس بعد الظهر عشان الحر ما يدخلش.",
                         "شغّل الغسالة والمكواة والسخان الصبح بدري أو بالليل، مش وقت الظهر."],
            "hot": [f"غدًا دافئ ({hi}): ابدأ بالمروحة، ولو احتجت التكييف خليه على 25 درجة واقفل الأبواب والشبابيك.",
                    "اطفي التكييف في الأوض الفاضية."],
            "mild": [f"جو لطيف غدًا ({hi}): افتح الشبابيك وريّح التكييف يوم.",
                     "انشر الغسيل في الشمس واستفد من ضوء النهار بدل اللمبات."],
            "cool": [f"غدًا جو بارد شوية ({lo} إلى {hi}): مفيش داعي للتكييف.",
                     "شغّل السخان قبل ما تحتاج المية السخنة بساعة بس، مش طول اليوم."],
            "cold": [f"ليلة باردة غدًا ({lo}): استخدم بطانية بدل الدفاية طول الليل.",
                     "لو شغّلت دفاية، دفّي أوضة واحدة واقفل بابها.",
                     "شغّل السخان قبل ما تحتاج المية السخنة بساعة بس."],
        }[b]
    else:
        t = {
            "very_hot": [f"Very hot tomorrow ({hi}): set the AC to 24-25°C, not 18. Every degree warmer uses less electricity.",
                         "Close the curtains on sunny windows in the afternoon to keep the heat out.",
                         "Run the washing machine, iron and water heater early in the morning or at night, not at midday."],
            "hot": [f"Warm tomorrow ({hi}): try the fan first; if you need the AC, keep it at 25°C with doors and windows closed.",
                    "Switch the AC off in empty rooms."],
            "mild": [f"Pleasant weather tomorrow ({hi}): open the windows and give the AC a day off.",
                     "Dry clothes in the sun and use daylight instead of lamps."],
            "cool": [f"Cool tomorrow ({lo} to {hi}): no AC needed.",
                     "Turn the water heater on only about an hour before you need hot water, not all day."],
            "cold": [f"Cold night tomorrow ({lo}): use a blanket instead of running a heater all night.",
                     "If you use a heater, heat one room and keep its door closed.",
                     "Turn the water heater on only about an hour before you need hot water."],
        }[b]
    if w.get("rain") and w["rain"] >= 50:
        t.append("احتمال مطر كبير: افصل الأجهزة اللي برا البيت." if lang == "ar"
                 else "Rain is likely: unplug any devices that are outdoors.")
    return t[:3]


def push_message(w):
    """(title, body) of the evening phone notification (English, like the app)."""
    emoji, words = sky(w["code"])
    title = f"{emoji} Tomorrow in {w['city']}: {w['tmax']:.0f}°C"
    return title[:80], tips(w)[0][:180]


def send_push(w, cfg=None, timeout=TIMEOUT):
    """Adds a 'weather' row to alert_events; the Database Webhook then calls the send-push Edge Function.
    Needs the SECRET key (send_reports.py on GitHub Actions). Returns (ok, message)."""
    import store

    cfg = cfg or store.supabase_config()
    if not cfg:
        return False, "Supabase is not set up."
    url, key = cfg
    title, body = push_message(w)
    try:
        r = requests.post(f"{url}/rest/v1/alert_events",
                          json={"device_id": "weather", "event": "weather", "title": title, "body": body},
                          headers=store._headers(key, Prefer="return=minimal"), timeout=timeout)
    except requests.RequestException:
        return False, "Could not reach the database."
    return (True, "weather notification queued") if r.status_code in (200, 201, 204) else (False, f"HTTP {r.status_code}")
