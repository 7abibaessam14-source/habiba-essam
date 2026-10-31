"""
test_billing_local.py - checks billing.py with numbers that are easy to verify by hand. Run: py test_billing_local.py
"""

import numpy as np
import pandas as pd

from billing import bill_egp, month_projection, next_tier, price_of_next_kwh, saving_tips

ok = True


def check(name, got, expected, tol=0.01):
    global ok
    good = abs(got - expected) <= tol
    ok &= good
    print(f"{'ok  ' if good else 'FAIL'} {name}: {got:.2f} (expected {expected:.2f})")


# --- tiered bill (each kWh at its tier price) ---
check("bill 40 kWh", bill_egp(40), 40 * 0.68)
check("bill 100 kWh", bill_egp(100), 50 * 0.68 + 50 * 0.98)
check("bill 250 kWh", bill_egp(250), 50 * 0.68 + 50 * 0.98 + 100 * 1.15 + 50 * 1.73)
check("bill 1200 kWh", bill_egp(1200),
      50 * 0.68 + 50 * 0.98 + 100 * 1.15 + 150 * 1.73 + 300 * 2.14 + 350 * 2.41 + 200 * 2.74)
check("flat price 300 kWh x 2 EGP", bill_egp(300, flat_price=2.0), 600)
check("next kWh price at 300 kWh", price_of_next_kwh(300), 1.73)
ok &= next_tier(330) == (350, 2.14)
print("ok   next tier at 330 kWh is (350, 2.14)" if next_tier(330) == (350, 2.14) else "FAIL next tier")

# --- projection: constant 1 kWh every hour = 24 kWh a day ---
idx = pd.date_range("2026-08-01", "2026-09-30 23:00", freq="h")
hourly = pd.Series(1.0, index=idx)
p = month_projection(hourly, "2026-09-10")
check("used by 10 Sep (10 days)", p["used_kwh"], 240)
check("projected September (30 days)", p["projected_kwh"], 720)
check("range has no width when every day is the same", p["high_egp"] - p["low_egp"], 0)
check("last month (August, 31 days)", p["prev_kwh"], 744)

# --- tips: an over-budget month and a jump on the last day must be reported ---
spiky = hourly.copy()
spiky.loc["2026-09-10"] = 2.0  # double use on 10 Sep
p2 = month_projection(spiky, "2026-09-10")
tips = saving_tips(spiky, p2, budget_egp=500, appliance_kwh={"Water heater & AC": 100, "Kitchen": 20})
for kind, text in tips:
    print(f"   tip [{kind}] {text}")
found_jump = any("higher than your usual day" in t for _, t in tips)
found_budget = any("over your budget" in t for _, t in tips)
found_appl = any("Water heater & AC uses" in t for _, t in tips)
ok &= found_jump and found_budget and found_appl
print(f"{'ok  ' if found_jump else 'FAIL'} tip about the jump on 10 Sep")
print(f"{'ok  ' if found_budget else 'FAIL'} tip about going over the budget")
print(f"{'ok  ' if found_appl else 'FAIL'} tip about the biggest appliance")

print("\nRESULT:", "PASS" if ok else "FAIL")
