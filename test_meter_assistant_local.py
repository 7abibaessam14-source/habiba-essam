"""
test_meter_assistant_local.py - checks meter_assistant.py with a FAKE model (no internet, no API key, no cost):
the numbers, the reading checks, and what is sent to and taken from the API. Run: py test_meter_assistant_local.py
"""

import inspect
import io
import json
import tempfile
from datetime import date
from pathlib import Path
from types import SimpleNamespace as NS

import groq
import httpx
from PIL import Image

import meter_assistant as ma
import store

ok = True


def check(name, cond):
    global ok
    ok &= bool(cond)
    print(f"{'ok  ' if cond else 'FAIL'} {name}")


class FakeClient:
    """Answers like client.chat.completions.create and keeps the request it received."""

    def __init__(self, content=None, tool_calls=None, finish_reason="stop", error=None, then=None):
        message = NS(content=content, tool_calls=tool_calls)
        self.answer, self.error, self.sent = NS(choices=[NS(message=message, finish_reason=finish_reason)]), error, None
        self.then = then  # the text of a second answer, if the code asks again
        self.calls = 0
        self.chat = NS(completions=NS(create=self.create))

    def create(self, **kw):
        self.sent = kw
        self.calls += 1
        if self.error:
            raise self.error
        if self.calls > 1 and self.then is not None:
            return NS(choices=[NS(message=NS(content=self.then, tool_calls=None), finish_reason="stop")])
        return self.answer


def tool_call(arguments):
    return NS(function=NS(name="propose_meter_reading", arguments=arguments))


def photo_bytes(size=(3000, 1000)):
    out = io.BytesIO()
    Image.new("RGB", size, "white").save(out, "PNG")
    return out.getvalue()


def reading(day, kwh):
    return {"read_on": day, "reading_kwh": kwh, "source": "manual"}


# ---- numbers: all easy to check by hand ----
check("no readings: nothing to work out", ma.consumption_summary([]) == {"readings": 0})
one = ma.consumption_summary([reading("2026-10-01", 1000.0)])
check("one reading gives no consumption", one["readings"] == 1 and "kwh_per_day" not in one)

two = [reading("2026-10-01", 1000.0), reading("2026-10-11", 1100.0)]  # 100 kWh in 10 days
s = ma.consumption_summary(two, budget_egp=500)
check("10 kWh a day", s["kwh_per_day"] == 10)
check("300 kWh in a 30-day month", s["typical_month_kwh"] == 300)
bill = 50 * 0.68 + 50 * 0.98 + 100 * 1.15 + 100 * 1.73
check("the bill comes from billing.py", s["typical_month_bill_egp"] == round(bill, 1))
check("the next tier starts after 350 kWh", s["next_tier_starts_after_kwh"] == 350 and s["next_tier_price_egp"] == 2.14)
check("under budget is worked out", s["under_budget_by_egp"] == round(500 - bill, 1))
check("10 days is not a short period", not s["short_period_less_certain"])
flat = ma.consumption_summary(two, flat_price=2.0)
check("fixed price: 300 x 2 EGP and no tiers", flat["typical_month_bill_egp"] == 600 and "next_tier_starts_after_kwh" not in flat)

three = two + [reading("2026-10-21", 1250.0)]  # then 150 kWh in 10 days
s3 = ma.consumption_summary(three)
check("the change from the period before is +50%", s3["change_from_previous_period_percent"] == 50)
check("a zero period before does not crash",
      "change_from_previous_period_percent" not in ma.consumption_summary(
          [reading("2026-10-01", 100.0), reading("2026-10-02", 100.0), reading("2026-10-03", 110.0)]))

# ---- what may be saved ----
today = date(2026, 11, 5)
check("a good reading passes", ma.check_reading(two, 1200.0, date(2026, 11, 1), today) is None)
check("the first reading passes", ma.check_reading([], 5.0, today, today) is None)
check("lower than the last is refused", "only counts up" in ma.check_reading(two, 1099.0, date(2026, 11, 1), today))
check("a date in the future is refused", "future" in ma.check_reading(two, 1200.0, date(2026, 11, 6), today))
check("not after the last date is refused", "after your last" in ma.check_reading(two, 1200.0, date(2026, 10, 11), today))
check("a negative number is refused", ma.check_reading([], -1.0, today, today) is not None)
check("a mistyped digit is noticed", ma.looks_too_high(two, 11000.0, date(2026, 10, 21)))
check("a normal reading is not flagged", not ma.looks_too_high(two, 1200.0, date(2026, 10, 21)))

# ---- numbers as people write them ----
check("plain digits", ma.parse_number("15230") == 15230)
check("Arabic digits", ma.parse_number("١٥٢٣٠") == 15230)
check("a comma and spaces", ma.parse_number(" 15,230 ") == 15230)
check("a decimal", ma.parse_number("15230.5") == 15230.5)
check("empty is not a number", ma.parse_number("") is None)
check("words are not a number", ma.parse_number("about 15230") is None)

# ---- photos ----
data, media = ma.prepare_image(photo_bytes())
img = Image.open(io.BytesIO(__import__("base64").b64decode(data)))
check("a big photo is made smaller, as JPEG", max(img.size) == ma.MAX_IMAGE_SIDE and media == "image/jpeg")
try:
    ma.prepare_image(b"not a photo")
    check("a file that is not a photo is refused", False)
except ValueError:
    check("a file that is not a photo is refused", True)

c = FakeClient(json.dumps({"kind": "kwh", "reading": "١٥٢٣٠", "note": "Clear."}))
r = ma.read_meter_photo(photo_bytes(), "ar", client=c)
check("a photo reading is parsed", r == {"kind": "kwh", "reading": 15230, "note": "Clear."})
image = c.sent["messages"][1]["content"][0]
check("the photo is sent as an image", image["type"] == "image_url" and image["image_url"]["url"].startswith("data:image/jpeg;base64,"))
check("the answer must be JSON", c.sent["response_format"] == {"type": "json_object"} and "JSON" in c.sent["messages"][0]["content"])
check("photos use the photo model and its reasoning stays hidden", c.sent["model"] == ma.PHOTO_MODEL and c.sent["reasoning_format"] == "hidden")
check("the note's language is asked for", "Egyptian Arabic" in c.sent["messages"][0]["content"])

c = FakeClient(json.dumps({"kind": "kwh", "reading": "15?30", "note": "Blurry."}))
check("an unreadable digit is never saved as a number", ma.read_meter_photo(photo_bytes(), client=c)["kind"] == "unclear")
c = FakeClient(json.dumps({"kind": "other_value", "reading": "85", "note": "Balance."}))
r = ma.read_meter_photo(photo_bytes(), client=c)
check("a card meter's balance is not a reading", r["kind"] == "other_value" and r["reading"] is None)
c = FakeClient(json.dumps({"kind": "something", "reading": "85", "note": "?"}))
check("an unknown kind is treated as unclear", ma.read_meter_photo(photo_bytes(), client=c)["kind"] == "unclear")
for bad in ("not json", "[1]", json.dumps({"kind": "kwh"}), None):
    try:
        ma.read_meter_photo(photo_bytes(), client=FakeClient(bad))
        check(f"a broken answer gives a clear message: {bad}", False)
    except ma.AssistantError:
        check(f"a broken answer gives a clear message: {bad}", True)

# ---- chat ----
facts = ma.consumption_summary(two)
c = FakeClient("You use 10 kWh a day.")
r = ma.chat([{"role": "user", "content": "How am I doing?"}], facts, client=c)
check("a chat reply comes back", r == {"text": "You use 10 kWh a day.", "proposed": None})
check("the code's numbers reach the model as facts", '"kwh_per_day": 10.0' in c.sent["messages"][0]["content"])
check("the chat uses the chat model and its reasoning stays hidden", c.sent["model"] == ma.CHAT_MODEL and c.sent["reasoning_format"] == "hidden")

def invented(text, said="hi"):
    return ma._invented_numbers(text, facts, [{"role": "user", "content": said}])


check("a number from the facts is allowed", invented("You use 10 kWh a day, about 300 kWh a month.") == [])
check("a fact may be rounded", invented(f"The bill is about {facts['typical_month_bill_egp']:.0f} EGP.") == [])
check("a number the user wrote is allowed", invented("Your 300 EGP bill is high.", "my bill was 300") == [])
check("an invented kWh is caught", invented("Each degree adds 0.5 kWh a day.") == ["0.5"])
check("an invented percentage is caught", invented("LEDs save 80% of the power.") == ["80"])
check("an invented range is caught", invented("That costs 20-40 EGP.") == ["20", "40"])
check("money written before the number is caught", invented("That costs EGP 77.") == ["77"])
check("Arabic digits and units are caught", invented("هتوفر ٧٧ جنيه و٣٣٪ و٠٫٥ ك.و.س") == ["77", "33", "0.5"])
check("a setting or a count is not a claim", invented("Set it to 24 °C, clean it every 3 months.") == [])

c = FakeClient("Each degree adds 0.5 kWh a day.", then="Raise the AC setting a little.")
r = ma.chat([{"role": "user", "content": "tips?"}], facts, client=c)
check("an invented number is asked again", r["text"] == "Raise the AC setting a little." and c.calls == 2
      and "not in the facts" in c.sent["messages"][-1]["content"])
c = FakeClient("LEDs save 80%.", then="LEDs save 70%.")
check("a second invented number gives the safe reply",
      ma.chat([{"role": "user", "content": "tips?"}], facts, client=c)["text"] == ma.SAFE_REPLY["en"])
c = FakeClient("LEDs save 80%.", then="LEDs save 70%.")
check("the safe reply follows the language",
      ma.chat([{"role": "user", "content": "نصايح؟"}], facts, "ar", client=c)["text"] == ma.SAFE_REPLY["ar"])
c = FakeClient("You use 10 kWh a day.")
ma.chat([{"role": "user", "content": "tips?"}], facts, client=c)
check("a good answer is not asked again", c.calls == 1)
c = FakeClient("Saves 80%.", tool_calls=[tool_call('{"reading": "15230"}')], finish_reason="tool_calls", then="Please confirm it.")
r = ma.chat([{"role": "user", "content": "the meter says 15230"}], facts, client=c)
check("asking again keeps the proposed reading", r == {"text": "Please confirm it.", "proposed": 15230})

check("English letters mean English", ma._text_language("how can I save?", "ar") == "en")
check("Arabic letters mean Arabic", ma._text_language("ازاي اوفر؟", "en") == "ar")
check("mostly Arabic with LED is Arabic", ma._text_language("لو غيرت اللمبات LED هوفر كام؟", "en") == "ar")
check("only digits keep the page language", ma._text_language("15420", "ar") == "ar")
c = FakeClient("تمام")
ma.chat([{"role": "user", "content": "how am I doing?"}], facts, "ar", client=c)
check("an English question on the Arabic page asks for English", "only in English" in c.sent["messages"][0]["content"])
c = FakeClient("ده رد بالعربي", then="An English answer.")
r = ma.chat([{"role": "user", "content": "how am I doing?"}], facts, client=c)
check("a reply in the wrong language is asked again", r["text"] == "An English answer." and c.calls == 2
      and "wrong language" in c.sent["messages"][-1]["content"])
c = FakeClient("ده رد بالعربي", then="لسه بالعربي")
check("a second wrong language gives the safe reply",
      ma.chat([{"role": "user", "content": "how am I doing?"}], facts, client=c)["text"] == ma.SAFE_REPLY["en"])
c = FakeClient("You use 10 kWh a day.")
ma.chat([{"role": "user", "content": "ازاي؟"}], facts, "en", client=c)
check("an Arabic question on the English page asks for Arabic", "only in Egyptian Arabic" in c.sent["messages"][0]["content"])
c = FakeClient("رد بالعربي")
ma.chat([{"role": "user", "hidden": True, "content": "I saved a new meter reading."}], facts, "ar", client=c)
check("the page's hidden English message keeps the Arabic page language",
      "only in Egyptian Arabic" in c.sent["messages"][0]["content"] and c.calls == 1)
c = FakeClient("Use an old لمبة less.", then="Use old bulbs less.")
r = ma.chat([{"role": "user", "content": "tips?"}], facts, client=c)
check("Arabic words in an English reply are asked again", r["text"] == "Use old bulbs less." and c.calls == 2)
c = FakeClient("Use old bulbs less.")
ma.chat([{"role": "user", "content": "tips?"}], facts, client=c)
check("the Arabic spelling rule is not sent for English", "لمبة" not in c.sent["messages"][0]["content"])
c = FakeClient("رد بالعربي")
ma.chat([{"role": "user", "content": "نصايح؟"}], facts, client=c)
check("the Arabic spelling rule is sent for Arabic", "لمبة" in c.sent["messages"][0]["content"])
check("the code works out the kWh left before the next tier",
      facts["kwh_left_before_next_tier"] == round(facts["next_tier_starts_after_kwh"] - facts["kwh_per_day"] * 30, 1))
check("the AC rule is in the prompt", "never advise a lower setting" in ma.CHAT_SYSTEM)
check("the tariff rule is in the prompt", "Never say some hours are cheaper" in ma.CHAT_SYSTEM)

r = ma.chat([{"role": "user", "content": "the meter says 15,230"}], facts,
            client=FakeClient(tool_calls=[tool_call('{"reading": "15,230"}')], finish_reason="tool_calls"))
check("a reading in the chat is only proposed", r["proposed"] == 15230 and r["text"] == "")
r = ma.chat([{"role": "user", "content": "العداد ١٥٢٣٠"}], facts,
            client=FakeClient(tool_calls=[tool_call('{"reading": "15230"}')], finish_reason="tool_calls"))
check("a reading in Arabic digits is proposed", r["proposed"] == 15230)

c = FakeClient("Tips.")
ma.chat([{"role": "user", "content": "ليه فاتورتي عالية؟"}], facts, client=c)
check("a message with no number cannot propose a reading", "tools" not in c.sent)
c = FakeClient(tool_calls=[tool_call('{"reading": "1100"}')], finish_reason="tool_calls", then="A plain answer.")
r = ma.chat([{"role": "user", "content": "my bill was 300"}], facts, client=c)
check("an invented reading is dropped and a plain answer is asked for",
      r == {"text": "A plain answer.", "proposed": None} and c.calls == 2 and "tools" not in c.sent)
c = FakeClient(tool_calls=[tool_call('{"reading": "0"}')], finish_reason="tool_calls", then="A plain answer.")
check("part of a number is not accepted as a reading", ma.chat([{"role": "user", "content": "I used 1000"}], facts, client=c)["proposed"] is None)
try:
    ma.chat([{"role": "user", "content": "x"}], facts, client=FakeClient(tool_calls=[tool_call("{broken")], finish_reason="tool_calls"))
    check("a broken tool call gives a clear message", False)
except ma.AssistantError:
    check("a broken tool call gives a clear message", True)

long_chat = [{"role": "assistant" if i % 2 else "user", "content": str(i), "hidden": True} for i in range(ma.MAX_HISTORY + 1)]
c = FakeClient("ok")
ma.chat(long_chat, facts, client=c)
sent = c.sent["messages"]
check("the system message comes first", sent[0]["role"] == "system")
check("a long chat is cut and starts with the user", sent[1]["role"] == "user" and len(sent) - 1 <= ma.MAX_HISTORY)
check("only role and content are sent", set(sent[1]) == {"role", "content"})

for reason in ("length", "content_filter"):
    try:
        ma.chat([{"role": "user", "content": "x"}], facts, client=FakeClient("half an ans", finish_reason=reason))
        check(f"finish reason {reason} gives a clear message", False)
    except ma.AssistantError as e:
        check(f"finish reason {reason} gives a clear message", "could not answer" in str(e))
try:
    ma.chat([{"role": "user", "content": "x"}], facts,
            client=FakeClient(error=groq.APIConnectionError(request=httpx.Request("POST", "https://x"))))
    check("no internet gives a clear message", False)
except ma.AssistantError as e:
    check("no internet gives a clear message", "internet" in str(e))

# ---- the installed SDK really has what this file uses ----
params = inspect.signature(groq.Groq(api_key="test").chat.completions.create).parameters
check("the SDK accepts every setting used here",
      {"reasoning_format", "reasoning_effort", "response_format", "max_completion_tokens", "tools"} <= set(params))

# ---- saved readings, in a file ----
with tempfile.TemporaryDirectory() as tmp:
    f = Path(tmp) / "readings.json"
    check("a reading is saved", store.add_meter_reading("sara", "2026-10-11", 1100.0, "photo", f)[0])
    store.add_meter_reading("sara", "2026-10-01", 1000.0, "chat", f)
    store.add_meter_reading("omar", "2026-10-05", 7.0, "manual", f)
    got = store.load_meter_readings("sara", f)
    check("readings come back oldest first", [g["reading_kwh"] for g in got] == [1000.0, 1100.0])
    check("each user sees only their own", len(store.load_meter_readings("omar", f)) == 1)
    check("one reading a day", not store.add_meter_reading("sara", "2026-10-01", 1.0, "manual", f)[0])
    store.delete_meter_reading("sara", "2026-10-11", f)
    check("a reading can be deleted", [g["read_on"] for g in store.load_meter_readings("sara", f)] == ["2026-10-01"])
    check("a user with no readings gets an empty list", store.load_meter_readings("nobody", f) == [])

print("\nRESULT:", "PASS" if ok else "FAIL")
