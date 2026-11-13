"""
theme.py - the website's look: Dark / Light, the color the user picks and a moving background for each page.

Streamlit cannot switch its own theme from code, so .streamlit/config.toml keeps it on dark and Light mode is done
here with CSS. Tables are drawn on a canvas that CSS cannot recolor, so Light mode inverts them.
apply() must run once per page, before the page's content. No secrets and no data here.
"""

from urllib.parse import quote

import streamlit as st

MODES = ["Dark", "Light"]
COLORS = ["Red", "Teal", "Blue", "Lime", "Gold", "Violet"]
SWATCH = {"Red": "#FF5362", "Teal": "#3FE0D0", "Blue": "#4DA3FF", "Lime": "#D4FF3A", "Gold": "#FFC845", "Violet": "#BDA4FF"}
# Light mode needs darker accents so that text in that color stays readable on the light background
LIGHT_ACCENT = {"Red": "#C81E33", "Teal": "#0A706A", "Blue": "#1F5FBF", "Lime": "#4A7300", "Gold": "#935C00", "Violet": "#5B37D6"}
GLOW = {"Red": "#FF2D3F", "Teal": "#12BFB0", "Blue": "#1E7BFF", "Lime": "#7EDB1F", "Gold": "#FF9A1F", "Violet": "#7A3BFF"}
DEEP = {"Red": "#7A0F2A", "Teal": "#0B4F6C", "Blue": "#0B2E6E", "Lime": "#0F5E36", "Gold": "#6E3A08", "Violet": "#2A1B7A"}
# second color of the AmperEC logo (the logo is a gradient from SWATCH to PARTNER); same pairs as the phone app
PARTNER = {"Red": "#FF9AA4", "Teal": "#7CF29B", "Blue": "#7CE7FF", "Lime": "#7EDB1F", "Gold": "#FF9A1F", "Violet": "#7AC8FF"}

DARK = {"ground": "#060405", "ink": "#F7F5F3", "ink2": "#DDD6D2", "on": "#0A0506", "panel": "#120D0FB8", "well": "#FFFFFF12",
        "chip": "#FFFFFF14", "line": "#FFFFFF2E", "wall": "#0D090A", "orb": "0.42", "orb2": "0.75", "chart": "none"}
LIGHT = {"ground": "#F2EEE8", "ink": "#17110F", "ink2": "#4A403C", "on": "#FFFFFF", "panel": "#FFFFFFC7", "well": "#FFFFFFB3",
         "chip": "#17110F12", "line": "#17110F26", "wall": "#FFFFFF", "orb": "0.26", "orb2": "0.2",
         "chart": "invert(1) hue-rotate(180deg)"}

# Which window of the Home house lights up for each part of "Where it goes" (uci_loader.APPLIANCE_COLUMNS)
WINDOWS = {"Kitchen": "kitchen", "Laundry": "laundry", "Water heater & AC": "heater", "Other (not sub-metered)": "other"}

BASE_CSS = """
@import url('https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;500;600&family=Playfair+Display:ital,wght@1,500&display=swap');
.stApp { background: var(--ground); color: var(--ink); }
.stApp h1, .stApp h2, .stApp h3, .stApp p, .stApp label, .stApp input, .stApp button { font-family: 'Outfit', 'Cairo', 'Segoe UI', sans-serif; }
.energy-hero { margin: 8px 0 14px; font-weight: 400; font-size: clamp(40px, 5vw, 72px); line-height: 1.05; letter-spacing: -0.02em; color: var(--ink); }
.energy-hero em { font-family: 'Playfair Display', 'Cairo', serif; font-weight: 500; color: var(--accent); letter-spacing: 0; }
.energy-tiers { display: flex; gap: 4px; }
.energy-tiers div { flex: 1 1 0; min-width: 0; padding: 9px 2px; border-radius: 8px; text-align: center; font-size: 13px; background: var(--chip); color: var(--ink2); }
.energy-tiers div.on { background: var(--accent); color: var(--on); font-weight: 600; }
.stApp [data-testid="stHeader"] { background: var(--panel); backdrop-filter: blur(20px); -webkit-backdrop-filter: blur(20px); }
.stApp [data-testid="stBottom"], .stApp [data-testid="stBottom"] > div, .stApp [data-testid="stBottomBlockContainer"] { background: transparent; }
.stApp [data-testid="stChatInput"], .stApp [data-testid="stChatInput"] > div { background: var(--panel); border-color: var(--line); }
.stApp [data-testid="stMetricValue"] { font-size: clamp(1.35rem, 2.1vw, 2.1rem); }
.stApp [data-testid="stTopNavSection"] svg, .stApp [data-testid="stHeader"] [data-testid="stIconMaterial"] { color: var(--ink); fill: currentColor; }
.stApp [data-testid="stCheckbox"] label[data-selected="true"] > div:first-of-type { background: var(--accent); }
.stApp [data-testid="stSlider"] [role="group"] > div > div:not(:has(input)) { background-image: linear-gradient(var(--line), var(--line)); }
.stApp [data-testid="stSlider"] div:has(> div > input[type="range"]) { background: var(--accent); }
.stApp [data-testid="stVegaLiteChart"] { background: var(--panel); border-radius: 12px; }
.stApp [data-testid="stVegaLiteChart"] svg { background: transparent !important; }
.stApp [data-testid="stMultiSelect"] [role="group"] { background: var(--well); border-color: var(--line); }
.stApp [data-testid="stMultiSelect"] [role="group"] span[class] { background: var(--accent); color: var(--on); }
.stApp [data-testid="stTopNavPopoverBody"] { background: var(--panel); backdrop-filter: blur(20px); -webkit-backdrop-filter: blur(20px); }
.stApp [data-testid="stTopNavDropdownLink"][aria-current="page"] { background: var(--accent); }
.stApp [data-testid="stTopNavDropdownLink"][aria-current="page"] span { color: var(--on); }
.stApp [data-testid="stVegaLiteChart"] svg text { fill: var(--ink2); }
.stApp [data-testid="stVegaLiteChart"] svg .role-axis-grid line, .stApp [data-testid="stVegaLiteChart"] svg .role-axis-domain line, .stApp [data-testid="stVegaLiteChart"] svg .role-axis-tick line { stroke: var(--line); }
.stApp [data-testid="stMain"] { background: transparent; position: relative; z-index: 1; }
.stApp [data-testid="stSidebar"] { background: var(--panel); backdrop-filter: blur(20px); -webkit-backdrop-filter: blur(20px); }
.stApp [data-testid="stMainMenu"] { display: none; }
.stApp h1, .stApp h2, .stApp h3, .stApp p, .stApp li, .stApp label, .stApp summary, .stApp span { color: var(--ink); }
.stApp [data-testid="stCaptionContainer"] p, .stApp [data-testid="stMetricLabel"] p { color: var(--ink2); }
.stApp [data-testid="stMetricValue"] div { color: var(--ink); }
.stApp [data-testid="stMetric"], .stApp [data-testid="stExpander"] details, .stApp [data-testid="stAlert"] {
  background: var(--panel); border: 1px solid var(--line); border-radius: 16px; backdrop-filter: blur(18px); -webkit-backdrop-filter: blur(18px);
}
.stApp [data-testid="stMetric"] { padding: 14px 16px; }
.stApp [data-testid="stLayoutWrapper"] > [data-testid="stVerticalBlock"][class*="st-key-card"],
.stApp div[class*="st-key-card"] {
  background: var(--panel); border: 1px solid var(--line); border-radius: 20px; padding: 18px;
  backdrop-filter: blur(18px); -webkit-backdrop-filter: blur(18px);
}
.stApp [data-baseweb="input"], .stApp [data-baseweb="base-input"], .stApp [data-baseweb="textarea"], .stApp [data-baseweb="select"] > div, .stApp [data-testid="stNumberInputContainer"],
.stApp [data-testid="stTextInputRootElement"], .stApp [data-testid="stDateInput"] > div > div {
  background: var(--well); border-color: var(--line); color: var(--ink);
}
.stApp input, .stApp textarea { background: transparent; color: var(--ink); caret-color: var(--ink); -webkit-text-fill-color: var(--ink); }
.stApp [data-testid="stNumberInputContainer"] button { background: var(--chip); color: var(--ink); }
.stApp button[data-testid^="stBaseButton-secondary"], .stApp [data-testid="stPageLink"] a, .stApp [data-testid="stFileUploaderDropzone"] {
  background: var(--chip); border: 1px solid var(--line); color: var(--ink);
}
.stApp button[data-testid^="stBaseButton-primary"] { background: var(--accent); border-color: var(--accent); color: var(--on); }
.stApp button[data-testid^="stBaseButton-primary"] p { color: var(--on); }
.stApp button[data-testid^="stBaseButton"]:hover { border-color: var(--accent); }
.stApp [data-testid="stSlider"] [role="slider"] { background: var(--accent); }
.stApp [data-testid="stSliderThumbValue"], .stApp [data-testid="stSliderTickBarMin"], .stApp [data-testid="stSliderTickBarMax"] { color: var(--ink); }
.stApp [data-testid="stTab"][aria-selected="true"] p { color: var(--accent); }
.stApp [data-testid="stTab"] .react-aria-SelectionIndicator { background: var(--accent); }
.stApp [data-testid="stProgressBarTrack"] { background: var(--line); }
.stApp [data-testid="stProgressBarTrack"] > div { background: var(--accent); }
.stApp [data-testid="stRadioOption"] > div > div:first-child { background: var(--chip); border: 1px solid var(--line); }
.stApp [data-testid="stRadioOption"]:has(input:checked) > div > div:first-child { background: var(--accent); border-color: var(--accent); }
.stApp [data-testid="stRadioOption"] > div > div:first-child > div { background: var(--on); }
.stApp [data-testid="stDataFrame"] canvas { filter: var(--chart); }
/* the tabs at the top */
.stApp [data-testid="stTopNavLink"], .stApp [data-testid="stTopNavSection"] { border-radius: 12px; }
.stApp [data-testid="stTopNavLink"] span, .stApp [data-testid="stTopNavSection"] span { font-size: 1rem; }
.stApp [data-testid="stTopNavLink"][aria-current="page"] { background: var(--accent); }
.stApp [data-testid="stTopNavLink"][aria-current="page"] span { color: var(--on); }
.stApp [data-testid="stAppDeployButton"], .stApp [data-testid="stToolbarActions"] { display: none; }
/* Dark / Light, the color circles and the language, in one row */
.st-key-energy_controls { gap: 6px 22px; align-items: center; }
.st-key-theme_color [data-testid="stRadioGroup"], .st-key-theme_color [role="radiogroup"] { gap: 2px 8px; }
.st-key-theme_color [data-testid="stRadioOption"] > div > div:first-child { width: 22px; height: 22px; border: 2px solid var(--line); }
.st-key-theme_color [data-testid="stRadioOption"]:has(input:checked) > div > div:first-child { outline: 2px solid var(--ink); outline-offset: 2px; }
.st-key-theme_color [data-testid="stRadioOption"] > div > div:first-child > div { display: none; }
.st-key-theme_color [data-testid="stRadioOption"] [data-testid="stMarkdownContainer"] { position: absolute; width: 1px; height: 1px; overflow: hidden; clip-path: inset(50%); }
/* the page background: fixed behind everything */
.st-key-energy_bg { position: fixed; inset: 0; z-index: 0; pointer-events: none; overflow: hidden; }
.st-key-energy_bg > * { position: absolute; inset: 0; }
.energy-scene { position: absolute; inset: 0; overflow: hidden; }
.energy-art { position: absolute; inset: 0; background-repeat: no-repeat; background-size: 100% 100%; }
.energy-orb { position: absolute; border-radius: 50%; filter: blur(110px); }
.energy-orb.a { right: 0; top: -140px; width: 620px; height: 620px; background: var(--glow); opacity: var(--orb); animation: energy-a 17s ease-in-out infinite; }
.energy-orb.b { left: -8%; bottom: -160px; width: 600px; height: 600px; background: var(--deep); opacity: var(--orb2); animation: energy-b 21s ease-in-out infinite; }
@keyframes energy-a { 0%,100% { transform: translate(0,0) scale(1); } 50% { transform: translate(-240px,200px) scale(1.2); } }
@keyframes energy-b { 0%,100% { transform: translate(0,0); } 50% { transform: translate(320px,-180px); } }
@media (max-width: 768px) {
  .energy-orb { filter: blur(70px); }
  .energy-art { opacity: .35; }
}
@media (prefers-reduced-motion: reduce) {
  .energy-orb { animation: none; }
}
"""

# st.html removes inline <svg>, so each scene goes on the page as a CSS background image. An image cannot see the
# page's CSS, so it carries its own styles, with the colors written in (see art_css).
SVG_CSS = """
.energy-soft { filter: blur(5px); opacity: .55; }
.energy-line { fill: none; stroke: var(--accent); stroke-linecap: round; stroke-linejoin: round; }
.energy-solid { fill: var(--accent); }
.energy-wall { fill: var(--wall); stroke: var(--accent); stroke-linejoin: round; }
.energy-plate { fill: none; stroke: var(--accent); stroke-opacity: .45; }
.energy-wire { stroke-dasharray: 14 26; animation: energy-flow 2.4s linear infinite; }
.energy-wire2 { stroke-dasharray: 6 22; animation: energy-flow 1.5s linear infinite; }
.energy-pa { animation: energy-pa 12s ease-in-out infinite; }
.energy-pb { animation: energy-pb 15s ease-in-out infinite; }
.energy-pc { animation: energy-pc 18s ease-in-out infinite; }
.energy-rise { transform-box: fill-box; transform-origin: bottom; animation: energy-rise 5s ease-in-out infinite; }
.energy-breathe { animation: energy-breathe 3s ease-in-out infinite; }
text { font-family: 'Segoe UI', Arial, sans-serif; }
@keyframes energy-flow { to { stroke-dashoffset: -80; } }
@keyframes energy-pa { 0%,100% { transform: translate(0,0) rotate(-6deg); } 50% { transform: translate(50px,-46px) rotate(3deg); } }
@keyframes energy-pb { 0%,100% { transform: translate(0,0) rotate(5deg); } 50% { transform: translate(-60px,50px) rotate(-4deg); } }
@keyframes energy-pc { 0%,100% { transform: translate(0,0) rotate(2deg); } 50% { transform: translate(40px,60px) rotate(8deg); } }
@keyframes energy-rise { 0%,100% { transform: scaleY(.72); } 50% { transform: scaleY(1); } }
@keyframes energy-breathe { 0%,100% { opacity: .65; } 50% { opacity: 1; } }
@media (prefers-reduced-motion: reduce) { * { animation: none !important; } }
"""


def _svg(body, align="xMidYMid slice", cls=""):
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1440 1000" preserveAspectRatio="{align}" class="{cls}">'
            f"<style>{SVG_CSS}</style>{body}</svg>")


def _house(lit):
    """The Home scene. lit: the window that is bright (see WINDOWS), or None for all of them."""
    def op(name):
        return 1 if lit in (None, name) else 0.22

    return _svg(f"""
<defs><filter id="energy-neon" x="-30%" y="-30%" width="160%" height="160%"><feGaussianBlur stdDeviation="9" result="b"></feGaussianBlur>
<feMerge><feMergeNode in="b"></feMergeNode><feMergeNode in="SourceGraphic"></feMergeNode></feMerge></filter></defs>
<g class="energy-plate" stroke-width="1.5" transform="translate(0 130)"><path d="M1090 200 L1400 355 L1090 510 L780 355 Z"></path>
<path d="M935 277 L1245 432 M1012 239 L1322 394 M858 316 L1168 471 M1245 277 L935 432 M1168 239 L858 394 M1322 316 L1012 471"></path></g>
<g filter="url(#energy-neon)"><path class="energy-line energy-wire" stroke-width="2.5" d="M1270 470 L1440 385"></path>
<path class="energy-line energy-wire" stroke-width="2.5" d="M1090 560 L1090 640 L990 690"></path>
<path class="energy-line energy-wire" stroke-width="2.5" d="M910 470 L780 405 H700"></path></g>
<path class="energy-wall" stroke-width="2.5" d="M910 470 L1090 560 V440 L910 350 Z"></path>
<path class="energy-wall" stroke-width="2.5" d="M1090 560 L1270 470 V350 L1180 315 L1090 440 Z"></path>
<path class="energy-wall" stroke-width="3" d="M910 350 L1090 440 L1180 315 L1000 225 Z"></path>
<g class="energy-plate" stroke-width="1.5"><path d="M955 345 L1090 412 L1140 345 L1005 278 Z"></path>
<path d="M1000 311 L1135 379 M980 323 L1044 390 M1030 288 L1094 355"></path></g>
<g filter="url(#energy-neon)"><path class="energy-solid" fill-opacity="{op('kitchen')}" d="M932 441 L978 464 V414 L932 391 Z"></path>
<path class="energy-solid" fill-opacity="{op('laundry')}" d="M1009 480 L1063 507 V457 L1009 430 Z"></path>
<path class="energy-solid" fill-opacity="{op('other')}" d="M1117 546 L1158 526 V441 L1117 461 Z"></path>
<path class="energy-solid" fill-opacity="{op('heater')}" d="M1189 465 L1243 438 V388 L1189 415 Z"></path></g>""", "xMaxYMin meet")


def _bill(path, label=""):
    text = f'<text class="energy-solid" x="{label[0]}" y="{label[1]}" font-size="54" font-weight="600">EGP</text>' if label else ""
    return f'<path class="energy-line" stroke-width="5" d="{path}"></path>{text}'


BILLS = _svg(
    f'<g class="energy-pa">{_bill("M1040 90 h260 v340 l-44 -26 l-44 26 l-44 -26 l-44 26 l-44 -26 l-40 26 z M1080 160 h180 M1080 215 h180 M1080 270 h110", (1080, 370))}</g>'
    f'<g class="energy-pb">{_bill("M80 560 h240 v320 l-40 -24 l-40 24 l-40 -24 l-40 24 l-40 -24 l-40 24 z M118 625 h165 M118 680 h165 M118 735 h100", (118, 825))}</g>'
    f'<g class="energy-pc">{_bill("M640 660 h210 v280 l-35 -22 l-35 22 l-35 -22 l-35 22 l-35 -22 l-35 22 z M674 720 h145 M674 770 h145 M674 820 h90")}</g>'
    f'<g class="energy-pb">{_bill("M1150 640 h190 v250 l-32 -20 l-31 20 l-32 -20 l-32 20 l-32 -20 l-31 20 z M1182 695 h125 M1182 745 h125")}</g>',
    cls="energy-soft",
)

WIRES = _svg("""
<path class="energy-line energy-wire" stroke-width="5" d="M-20 720 C260 520 340 960 620 760 S1000 520 1460 700"></path>
<path class="energy-line energy-wire2" stroke-width="4" d="M-20 860 C200 980 420 620 700 860 S1120 980 1460 820"></path>
<path class="energy-line energy-wire" stroke-width="4" d="M-20 560 C320 700 480 420 760 620 S1180 800 1460 520"></path>
<path class="energy-line energy-wire2" stroke-width="3" d="M-20 960 C360 800 620 1000 900 900 S1240 700 1460 940"></path>
<path class="energy-line energy-wire" stroke-width="4" d="M1460 330 C1280 300 1180 450 1040 420 S860 270 780 330"></path>
<rect class="energy-solid energy-breathe" x="700" y="300" width="84" height="60" rx="12"></rect>
<path class="energy-line" stroke-width="10" d="M660 316 H700 M660 344 H700"></path>""", cls="energy-soft")

ENVELOPES = _svg("""
<g class="energy-pa"><path class="energy-line" stroke-width="6" d="M980 120 h340 v230 h-340 z M980 130 l170 130 l170 -130"></path></g>
<g class="energy-pb"><path class="energy-line" stroke-width="6" d="M90 620 h290 v200 h-290 z M90 630 l145 112 l145 -112"></path></g>
<g class="energy-pc"><path class="energy-line" stroke-width="6" d="M1040 640 h250 v170 h-250 z M1040 650 l125 96 l125 -96"></path></g>
<path class="energy-line energy-wire2" stroke-width="3" d="M380 720 C620 640 760 900 1040 720"></path>""", cls="energy-soft")

_BARS = [(70, 720, -1), (210, 620, -3), (350, 680, 0), (490, 540, -2), (630, 600, -4), (770, 480, -1.5), (910, 560, -3.5), (1050, 430, -2.5), (1190, 500, -0.5)]
CHARTS = _svg(
    "".join(f'<rect class="energy-solid energy-rise" x="{x}" y="{y}" width="96" height="{1000 - y}" rx="10" opacity=".35" style="animation-delay:{d}s"></rect>'
            for x, y, d in _BARS)
    + '<path class="energy-line energy-wire" stroke-width="5" d="M0 660 L120 660 L170 560 L230 760 L290 620 L420 620 L470 500 L530 700 '
      'L590 560 L760 560 L810 420 L870 640 L930 500 L1110 500 L1160 370 L1220 590 L1280 450 L1440 450"></path>',
    "xMidYMax slice", "energy-soft",
)

SCENES = {"bill": BILLS, "assistant": WIRES, "reports": ENVELOPES}


def _aec_mark(color):
    """The AmperEC logo on a dark tile: an A with a lightning bolt, a circuit E and a C with a leaf (same drawing as the
    phone app), in a gradient from the picked color to its partner color. The tile stays dark in both modes."""
    a, b, gid = SWATCH[color], PARTNER[color], f"aec{color}"
    return (f'<defs><linearGradient id="{gid}" x1="0" x2="1" y1="0" y2="0"><stop offset="0" stop-color="{a}"/>'
            f'<stop offset="1" stop-color="{b}"/></linearGradient></defs>'
            f'<rect width="512" height="512" rx="112" fill="#0A0E1A"/>'
            f'<g transform="translate(46 150) scale(1.5)">'
            f'<g fill="none" stroke="url(#{gid})" stroke-width="9" stroke-linecap="round" stroke-linejoin="round">'
            f'<path d="M30 136 L72 16 L114 136"/><path d="M58 62 L38 98 H62 L44 138" stroke-width="7"/>'
            f'<path d="M96 92 C112 92 116 68 132 68"/><path d="M178 22 H132 V128 H178 M132 75 H166"/>'
            f'<path d="M262 44 A50 50 0 1 0 262 106"/></g>'
            f'<g fill="url(#{gid})"><circle cx="178" cy="22" r="7"/><circle cx="178" cy="128" r="7"/><circle cx="166" cy="75" r="6"/>'
            f'<circle cx="262" cy="44" r="7"/><circle cx="262" cy="106" r="7"/>'
            f'<path d="M218 98 C200 84 206 58 236 54 C240 80 230 96 218 98 Z"/>'
            f'<circle cx="252" cy="62" r="3"/><circle cx="256" cy="74" r="3"/><circle cx="255" cy="86" r="3"/></g>'
            f'<path d="M218 98 L230 66" stroke="#0A0E1A" stroke-width="3" stroke-linecap="round"/></g>')


def icon_svg(color="Red"):
    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512" width="512" height="512">{_aec_mark(color)}</svg>'


def logo_svg(mode="Dark", color="Red"):
    ink = (LIGHT if mode == "Light" else DARK)["ink"]
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 40" width="200" height="40">'
            f'<svg x="1" y="1" width="38" height="38" viewBox="0 0 512 512">{_aec_mark(color)}</svg>'
            f'<text x="47" y="27" font-family="Segoe UI, Helvetica, Arial, sans-serif" font-size="19" font-weight="700" letter-spacing="1.5">'
            f'<tspan fill="{ink}">AMPER</tspan><tspan fill="{accent(mode, color)}">EC</tspan></text></svg>')


def accent(mode, color):
    return LIGHT_ACCENT[color] if mode == "Light" else SWATCH[color]


def theme_vars(mode, color):
    v = {**(LIGHT if mode == "Light" else DARK), "accent": accent(mode, color), "glow": GLOW[color], "deep": DEEP[color]}
    swatches = "".join(f'.st-key-theme_color [role="radiogroup"] > div:nth-child({i + 1}) [data-testid="stRadioOption"] > div > div:first-child '
                       f'{{ background: {SWATCH[c]}; border-color: {SWATCH[c]}; }}\n' for i, c in enumerate(COLORS))
    return ":root { " + " ".join(f"--{k}: {val};" for k, val in v.items()) + " }\n" + swatches


def scene_for(page, lit=None):
    """The background of a page: the house on Home and the login page, charts on every page without its own."""
    return _house(lit) if page == "home" else SCENES.get(page, CHARTS)


def art_css(page, mode, color, lit=None, rtl=False):
    """The CSS rule that shows the page's scene, with this mode's and color's values written into the image."""
    svg = scene_for(page, lit).replace("var(--accent)", accent(mode, color)).replace("var(--wall)", (LIGHT if mode == "Light" else DARK)["wall"])
    rule = f'.energy-art {{ background-image: url("data:image/svg+xml,{quote(svg, safe="")}"); }}\n'
    if rtl and page == "home":  # Arabic text starts on the right, so the house moves left, clear of the sidebar on a big screen
        rule += ".energy-art { transform: scaleX(-1); }\n@media (min-width: 769px) { .energy-art { transform: translateX(300px) scaleX(-1); } }\n"
    return rule


def apply(page, mode="Dark", color="Red", lit=None, rtl=False):
    """Puts the colors and the page's moving background on the page."""
    # BASE_CSS first: its font @import must open the sheet
    st.html(f"<style>{BASE_CSS}{theme_vars(mode, color)}{art_css(page, mode, color, lit, rtl)}</style>")
    with st.container(key="energy_bg"):
        st.html('<div class="energy-scene"><div class="energy-orb a"></div><div class="energy-orb b"></div><div class="energy-art"></div></div>')
