"""
test_theme_local.py - checks theme.py without opening a browser: colors, Dark / Light and the page backgrounds.
Run: py test_theme_local.py
"""

import re
import xml.etree.ElementTree as ET

import theme

ok = True


def check(name, cond):
    global ok
    ok &= bool(cond)
    print(f"{'ok  ' if cond else 'FAIL'} {name}")


def contrast(a, b):
    def lum(h):
        c = [int(h[i:i + 2], 16) / 255 for i in (1, 3, 5)]
        c = [x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4 for x in c]
        return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]

    hi, lo = sorted((lum(a), lum(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


check("every color has all its shades", all(c in d for c in theme.COLORS for d in (theme.SWATCH, theme.LIGHT_ACCENT, theme.GLOW, theme.DEEP)))
check("dark and light define the same settings", set(theme.DARK) == set(theme.LIGHT))
for c in theme.COLORS:
    check(f"{c}: accent text is readable on dark", contrast(theme.SWATCH[c], theme.DARK["ground"]) >= 4.5)
    check(f"{c}: accent text is readable on light", contrast(theme.LIGHT_ACCENT[c], theme.LIGHT["ground"]) >= 4.5)
    check(f"{c}: button text is readable in dark", contrast(theme.DARK["on"], theme.SWATCH[c]) >= 4.5)
    check(f"{c}: button text is readable in light", contrast(theme.LIGHT["on"], theme.LIGHT_ACCENT[c]) >= 4.5)
check("main text is readable on dark", contrast(theme.DARK["ink"], theme.DARK["ground"]) >= 4.5)
check("main text is readable on light", contrast(theme.LIGHT["ink"], theme.LIGHT["ground"]) >= 4.5)

dark, light = theme.theme_vars("Dark", "Teal"), theme.theme_vars("Light", "Teal")
check("the picked color reaches the page", theme.SWATCH["Teal"] in dark and theme.GLOW["Teal"] in dark)
check("light mode uses the darker color", f"--accent: {theme.LIGHT_ACCENT['Teal']}" in light)
check("only light mode inverts tables", "invert" in light and "invert" not in dark)
for name in re.findall(r"var\(--(\w+)\)", theme.BASE_CSS):
    check(f"the CSS setting --{name} exists", f"--{name}:" in dark and f"--{name}:" in light)

check("Home has the house", 'id="energy-neon"' in theme.scene_for("home"))
check("Bill has the bills", "EGP" in theme.scene_for("bill"))
check("Assistant has the wires", "energy-wire2" in theme.scene_for("assistant") and "<rect" in theme.scene_for("assistant"))
check("Reports has the envelopes", "l170 -130" in theme.scene_for("reports"))
for page in ("forecast", "appliances", "analytics", "anomalies", "alerts", "live", "pipeline", "about"):
    check(f"{page} has the charts background", theme.scene_for(page) == theme.CHARTS)

from urllib.parse import unquote

art = unquote(theme.art_css("home", "Light", "Gold"))
check("the background image has the real color", theme.LIGHT_ACCENT["Gold"] in art)
check("the background image has no CSS variables left", "var(--" not in art)
for page in ("home", "bill", "assistant", "reports", "forecast"):
    check(f"{page}: the background image is valid XML", ET.fromstring(unquote(theme.art_css(page, "Dark", "Red")).split("svg+xml,", 1)[1].rsplit('")', 1)[0]) is not None)

check("Arabic moves the house to the left", "scaleX(-1)" in theme.art_css("home", "Dark", "Red", rtl=True))
check("English keeps the house on the right", "scaleX(-1)" not in theme.art_css("home", "Dark", "Red"))
check("Arabic does not mirror the EGP on the bills", "scaleX(-1)" not in theme.art_css("bill", "Dark", "Red", rtl=True))

for c in theme.COLORS:
    check(f"{c}: the logo is valid and uses the color", theme.SWATCH[c] in theme.logo_svg("Dark", c) and ET.fromstring(theme.logo_svg("Dark", c)) is not None)
    check(f"{c}: the icon is valid and uses the color", theme.SWATCH[c] in theme.icon_svg(c) and ET.fromstring(theme.icon_svg(c)) is not None)
check("the logo name is readable in dark", theme.DARK["ink"] in theme.logo_svg("Dark"))
check("the logo name is readable in light", theme.LIGHT["ink"] in theme.logo_svg("Light"))

house = theme.scene_for("home", "kitchen")
check("the chosen part of the house is bright", 'fill-opacity="1" d="M932' in house)
check("the other parts are dim", 'fill-opacity="0.22" d="M1009' in house and 'fill-opacity="0.22" d="M1189' in house)
check("nothing chosen lights all of it", 'fill-opacity="0.22"' not in theme.scene_for("home"))
check("each usage row has a window", set(theme.WINDOWS.values()) == {"kitchen", "laundry", "heater", "other"})

from uci_loader import APPLIANCE_COLUMNS

check("the windows match the app's usage names", set(theme.WINDOWS) == set(APPLIANCE_COLUMNS.values()))

print("\nRESULT:", "PASS" if ok else "FAIL")
