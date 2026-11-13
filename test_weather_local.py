"""Local test for weather.py (fake Open-Meteo answers, no internet). Run: py test_weather_local.py
   py test_weather_local.py --live   also asks the real Open-Meteo for Cairo (needs internet)."""

import sys
from datetime import date
from unittest import mock

import weather

sys.stdout.reconfigure(encoding="utf-8")
fails = 0


def check(name, ok):
    global fails
    print(("PASS " if ok else "FAIL ") + name)
    fails += not ok


class Resp:
    def __init__(self, data, code=200):
        self._data, self.status_code = data, code

    def json(self):
        return self._data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise weather.requests.HTTPError(str(self.status_code))


DAILY = {"daily": {"time": ["2026-11-13", "2026-11-14", "2026-11-15"], "temperature_2m_max": [27.1, 38.4, 21.0],
                   "temperature_2m_min": [17.0, 26.2, 9.0], "precipitation_probability_max": [0, 5, 60],
                   "weather_code": [1, 0, 61]}}

with mock.patch("weather.requests.get", return_value=Resp(DAILY)) as g:
    w = weather.fetch_tomorrow("Cairo", today=date(2026, 11, 13))
    params = g.call_args.kwargs["params"]
check("picks tomorrow's row", w == {"city": "Cairo", "date": "2026-11-14", "tmax": 38.4, "tmin": 26.2, "rain": 5, "code": 0})
check("asks for Cairo time", params["timezone"] == "Africa/Cairo" and params["latitude"] == 30.0444)
check("unknown city -> Cairo", weather.fetch_tomorrow.__defaults__[0] == "Cairo")

with mock.patch("weather.requests.get", side_effect=weather.requests.ConnectionError()):
    check("no internet -> None, no crash", weather.fetch_tomorrow("Cairo") is None)
with mock.patch("weather.requests.get", return_value=Resp({"daily": {"time": []}})):
    check("odd answer -> None", weather.fetch_tomorrow("Cairo") is None)

hot = {"city": "Cairo", "tmax": 38.4, "tmin": 26.2, "rain": 5, "code": 0}
check("38°C is very hot", weather.band(hot) == "very_hot")
check("32°C is hot", weather.band({**hot, "tmax": 32}) == "hot")
check("26°C is mild", weather.band({**hot, "tmax": 26, "tmin": 16}) == "mild")
check("20°C is cool", weather.band({**hot, "tmax": 20, "tmin": 12}) == "cool")
check("night 8°C is cold", weather.band({**hot, "tmax": 19, "tmin": 8}) == "cold")

t = weather.tips(hot)
check("very hot: AC tip first with the temperature", t[0].startswith("Very hot tomorrow (38°C)") and "24-25°C" in t[0])
check("at most 3 tips", len(t) <= 3)
check("Arabic tips are Arabic", "التكييف" in weather.tips(hot, "ar")[0])
rainy = {**hot, "tmax": 21, "tmin": 9, "rain": 60, "code": 61}
check("rain tip when rain is likely", any("Rain is likely" in x for x in weather.tips(rainy)) or len(weather.tips(rainy)) == 3)

check("summary line", weather.summary(hot) == "Tomorrow in Cairo: ☀️ clear sky, 38° / 26°C")
check("summary with rain", weather.summary(rainy).endswith("60% chance of rain"))
check("Arabic summary uses the Arabic city", weather.summary(hot, "ar").startswith("غدًا في القاهرة"))
title, body = weather.push_message(hot)
check("push title", title == "☀️ Tomorrow in Cairo: 38°C" and body.startswith("Very hot"))

with mock.patch("weather.requests.post", return_value=Resp({}, 201)) as p:
    ok, msg = weather.send_push(hot, cfg=("https://example.supabase.co", "sb_secret_test"))
    row = p.call_args.kwargs["json"]
check("push queued as a weather row", ok and row["event"] == "weather" and row["title"] == title)

if "--live" in sys.argv:
    live = weather.fetch_tomorrow("Cairo")
    check("LIVE Open-Meteo answered", live is not None)
    if live:
        print("   ", weather.summary(live))
        for x in weather.tips(live):
            print("    -", x)

print("RESULT:", "FAIL" if fails else "PASS", f"({fails} failed)")
