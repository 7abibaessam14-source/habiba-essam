"""
Smart Energy Monitor - Streamlit app (v3)

Pages (tabs at the top, only the open page runs; the look is in theme.py)
  Home              project summary: data source, key numbers, quick links
  Bill & Budget     bill in EGP, month-end projection, budget alerts, saving tips
  Reports           daily / weekly / monthly report by Gmail, WhatsApp or SMS (preview, send now, save)
  Meter assistant   chatbot: meter reading by photo or text (user confirms), consumption, bill and advice
  Analytics        household exploration + naive trend baseline
  Appliances        kitchen / laundry / water heater+AC (needs the UCI file in ./data)
  Forecast          Random Forest vs XGBoost, next-24h forecast
  Anomalies         residual z-score + Isolation Forest on unseen data
  Alerts            threshold + anomaly alerts, CSV download, optional email (admin)
  Live              simulated real-time stream (replay of unseen hours) with instant alerts
  Pipeline monitor  Airflow REST API status (admin)
  About project     architecture, datasets, feature status

Credentials come from Streamlit secrets (.streamlit/secrets.toml), never hardcoded:
  [snowflake]  account, user, password, warehouse, database, schema, role
  [airflow]    base_url, dag_id, user, password
  [smtp]       host, port, user, password, to = ["a@b.com"]      (optional, for email alerts and reports)
  [twilio]     account_sid, auth_token, from_number               (optional, for SMS reports)
  [groq]       api_key                                            (optional, for the Meter assistant's chat and photos)
  [auth.users.<name>]  password_hash, role                        (optional, login; see auth.py)

Light / dark colours: .streamlit/config.toml. Needs the other .py files of this folder.
"""

import json
import math
from pathlib import Path

import altair as alt
import numpy as np
import pandas as pd
import requests
import streamlit as st

from csv_loader import csv_signature, load_csv_folder
from demo_data import make_demo_frames
from uci_loader import APPLIANCE_COLUMNS, UCI_FILENAME, load_uci, uci_path, uci_signature
from alerts import build_alerts, compute_threshold, send_email_alert
import auth
from billing import CO2_KG_PER_KWH, CO2_KG_PER_TREE_YEAR, EGYPT_TIERS, bill_egp, co2_kg, month_projection, next_tier, saving_tips
from live import LiveReplay
import meter_assistant
from ml_models import (FEATURES, add_features, detect_anomalies, feature_importance, forecast_next, to_hourly,
                       train_and_compare)
import notify
import sensor
import startup_cache
import store
import theme
from i18n import RTL_CSS, TRANSLATED_PAGES, T, day_month, is_ar, language_switch, month_name, month_year
from reports import (CHANNELS, FREQUENCIES, as_email, as_sms, as_whatsapp, build_report, note, now_local, sms_parts,
                     today_series)

APP_NAME = "Smart Energy Monitor"
st.set_page_config(page_title=APP_NAME, page_icon=str(Path(__file__).parent / "assets" / "icon.svg"), layout="wide")
st.session_state.setdefault("lang", "en")  # English / Arabic (i18n.py), chosen on the login page or in the sidebar
st.session_state["lang"] = st.session_state["lang"]
for _k, _v in {"theme_mode": "Dark", "theme_color": "Red"}.items():  # the look the user picked (theme.py), kept for the whole visit
    st.session_state.setdefault(_k, _v)
    st.session_state[_k] = st.session_state[_k]

ALL_LABEL = "All households (average)"
GITHUB_REPO = "7abibaessam14-source/habiba-essam"
ASSETS = Path(__file__).parent / "assets"
DATA_DIR = Path(__file__).parent / "data"  # put the CSV files here to run without Snowflake
CSV_MAX_HOUSEHOLDS = 60  # keeps the dashboard fast; raise it if your PC is strong


def secret_section(name, default=None):
    # st.secrets.get() raises (not returns default) when there is no secrets.toml at all
    try:
        return st.secrets.get(name, default)
    except FileNotFoundError:
        return default


# chart colours: actual = blue, model / forecast = orange; one clear colour per appliance
ACTUAL_VS_MODEL = ["#1f77b4", "#ff7f0e"]
APPLIANCE_COLORS = alt.Scale(
    domain=list(APPLIANCE_COLUMNS.values()), range=["#1f77b4", "#2ca02c", "#d62728", "#7f7f7f"]
)


def pan_zoom(tab):
    # drag = pan, Ctrl + mouse wheel = zoom; a plain wheel scrolls the page.
    # Not Shift: Chrome/Edge on Windows turn Shift + wheel into a horizontal scroll, which Vega ignores.
    # The name changes with 'Reset zoom': a new chart spec makes Streamlit redraw the chart, which drops
    # the zoom (double-click, a chart key or a normal rerun do not).
    n = st.session_state.get(f"zoom_reset_{tab}", 0)
    return alt.selection_interval(name=f"zoom_{tab}_{n}", bind="scales", zoom="wheel![event.ctrlKey]")


ZOOM_HELP = "Drag to move, Ctrl + mouse wheel to zoom, 'Reset zoom' to go back."

FEATURE_NAMES = {  # ml_models.FEATURES in plain words, for the "Why does the model predict this?" chart
    "hour": "Hour of the day",
    "dayofweek": "Day of the week",
    "month": "Month (season)",
    "is_weekend": "Weekend or not",
    "lag_1": "Last hour",
    "lag_2": "Two hours ago",
    "lag_24": "Same hour yesterday",
    "lag_168": "Same hour last week",
    "roll_mean_24": "Average of the last 24 hours",
    "roll_std_24": "How much the last 24 hours varied",
}


def _bump_zoom(tab):
    st.session_state[f"zoom_reset_{tab}"] = st.session_state.get(f"zoom_reset_{tab}", 0) + 1


def reset_zoom_button(tab):
    # on_click runs before the rerun, so the charts above already get the new name
    st.button("Reset zoom", key=f"reset_zoom_{tab}", on_click=_bump_zoom, args=(tab,))


# ---- Snowflake connection -------------------------------------------------
@st.cache_resource
def get_snowflake_connection():
    import snowflake.connector

    cfg = st.secrets["snowflake"]
    return snowflake.connector.connect(
        account=cfg["account"],
        user=cfg["user"],
        password=cfg["password"],
        warehouse=cfg["warehouse"],
        database=cfg["database"],
        schema=cfg.get("schema", "PUBLIC"),
        role=cfg.get("role"),
    )


def load_fact_data():
    conn = get_snowflake_connection()
    query = """
        SELECT f.LCLid, f.DateTime, f.KWH_hh, f.date_only, f.hour_of_day,
               d.day_of_week, d.is_weekend, d.day_type
        FROM FACT_CONSUMPTION f
        LEFT JOIN DIM_DATE d ON f.date_only = d.date_only
    """
    df = pd.read_sql(query, conn)
    # Snowflake returns unquoted identifiers in UPPERCASE regardless of how
    # they're written in the query, so normalize to the names the rest of
    # this app expects.
    df.columns = [c.upper() for c in df.columns]
    df = df.rename(
        columns={
            "LCLID": "LCLid",
            "DATETIME": "DateTime",
            "KWH_HH": "KWH_hh",
            "DATE_ONLY": "date_only",
            "HOUR_OF_DAY": "hour_of_day",
            "DAY_OF_WEEK": "day_of_week",
            "IS_WEEKEND": "is_weekend",
            "DAY_TYPE": "day_type",
        }
    )
    return df


def load_agg_data():
    conn = get_snowflake_connection()
    df = pd.read_sql("SELECT LCLid, avg_consumption FROM AGG_AVG_CONSUMPTION", conn)
    df.columns = [c.upper() for c in df.columns]
    df = df.rename(columns={"LCLID": "LCLid", "AVG_CONSUMPTION": "avg_consumption"})
    return df


def load_households():
    conn = get_snowflake_connection()
    df = pd.read_sql("SELECT LCLid FROM DIM_HOUSEHOLD ORDER BY LCLid", conn)
    df.columns = [c.upper() for c in df.columns]
    df = df.rename(columns={"LCLID": "LCLid"})
    return df


@st.cache_data(ttl=300, show_spinner="Connecting to Snowflake...")
def load_all():
    """Real data, or (None, None, None, error_text). Never raises, so a failure is cached and not retried on every click."""
    try:
        return load_fact_data(), load_agg_data(), load_households(), None
    except Exception as e:
        return None, None, None, str(e)


@st.cache_resource(show_spinner=False)
def startup_bundle():
    uci = uci_path(DATA_DIR)
    return startup_cache.load(uci) if uci is not None else None


@st.cache_data(show_spinner="Reading the data files (first time only)...")
def local_frames(signature):
    # signature is only a cache key: it changes when the files in ./data change
    uci = uci_path(DATA_DIR)  # the .txt on your PC, or the .zip copy on the online version
    if uci is not None:
        ready = startup_bundle()  # the saved copy when it fits, so the 2 million rows are not read at every start
        if ready is not None:
            return ready["fact"], ready["agg"], ready["households"], "UCI file (data folder)"
        return (*load_uci(uci), "UCI file (data folder)")
    res = load_csv_folder(DATA_DIR, max_households=CSV_MAX_HOUSEHOLDS)
    return None if res is None else (*res, "CSV files (data folder)")


@st.cache_data
def demo_frames():
    return make_demo_frames()


# ---- ML helpers (cached so sliders don't retrain the models) ---------------
# No time limit: on the free cloud server training takes ~30 s, so keep the results while the app runs.
# max_entries keeps memory bounded when many households are opened.
@st.cache_data(max_entries=20, show_spinner="Building the hourly series...")
def hourly_for(household_key, _df, n_rows):
    # _df is excluded from hashing (leading underscore); n_rows invalidates the cache when data changes
    household = None if household_key == ALL_LABEL else household_key
    return to_hourly(_df, household=household)


@st.cache_resource(max_entries=10, show_spinner="Training Random Forest / XGBoost...")
def train_for(household_key, _hourly, n_hours):
    ready = startup_bundle()
    if ready is not None and household_key == ALL_LABEL and n_hours == ready["n_hours"] and source_name.startswith("UCI"):
        return ready["res"]  # the saved models for exactly this data: no need to train again
    feats = add_features(_hourly)
    if len(feats) < 500:
        raise ValueError(
            f"Only {len(feats)} usable hourly rows for this selection - need ~500 (about 3 weeks) to train."
        )
    return train_and_compare(feats)


@st.cache_data(max_entries=50, show_spinner="Checking for anomalies...")
def anomalies_for(household_key, best_name, n_test, z, contamination, mode, _res):
    # Isolation Forest is the slow part; run it once per household + setting, not on every click
    return detect_anomalies(_res["test"], _res["best_model"], z_thresh=z, contamination=contamination, mode=mode)


# ---- Airflow REST API -----------------------------------------------------
def airflow_config():
    cfg = secret_section("airflow", {})
    return (
        cfg.get("base_url", "http://localhost:8080"),
        cfg.get("dag_id", "power_consumption_pipeline"),
        cfg.get("user", ""),
        cfg.get("password", ""),
    )


def get_airflow_dag_runs(base_url, dag_id, user, password, limit=10, timeout=10):
    url = f"{base_url}/api/v1/dags/{dag_id}/dagRuns"
    params = {"limit": limit, "order_by": "-execution_date"}
    resp = requests.get(url, params=params, auth=(user, password), timeout=timeout)
    resp.raise_for_status()
    return resp.json().get("dag_runs", [])


@st.cache_data(ttl=60, show_spinner=False)
def airflow_status(base_url, dag_id, user, password):
    """One short line for the Home page; cached so Home does not call Airflow on every click."""
    try:
        runs = get_airflow_dag_runs(base_url, dag_id, user, password, limit=1, timeout=3)
    except Exception:
        return "❌ not reachable"
    if not runs:
        return "⚪ no runs yet"
    return f"{STATE_COLORS.get(runs[0]['state'], '⚪')} last run: {runs[0]['state']}"


@st.cache_data(ttl=3600, show_spinner=False)
def pipeline_last_run():
    """(conclusion, finish time) of the last weekly pipeline run, from GitHub's public API; None if it cannot be read."""
    try:
        resp = requests.get(f"https://api.github.com/repos/{GITHUB_REPO}/actions/workflows/pipeline.yml/runs",
                            params={"per_page": 1, "status": "completed"}, timeout=5)
        resp.raise_for_status()
        run = resp.json()["workflow_runs"][0]
        return run["conclusion"], run["updated_at"]
    except Exception:
        return None


def get_airflow_task_instances(base_url, dag_id, dag_run_id, user, password):
    url = f"{base_url}/api/v1/dags/{dag_id}/dagRuns/{dag_run_id}/taskInstances"
    resp = requests.get(url, auth=(user, password), timeout=10)
    resp.raise_for_status()
    return resp.json().get("task_instances", [])


STATE_COLORS = {
    "success": "🟢",
    "failed": "🔴",
    "running": "🔵",
    "upstream_failed": "🟠",
    "queued": "⚪",
    "up_for_retry": "🟡",
    "skipped": "⚫",
}


# ---- Login first: nothing is loaded before someone is logged in ---------------
st.logo(theme.logo_svg(st.session_state.theme_mode, st.session_state.theme_color),
        icon_image=theme.icon_svg(st.session_state.theme_color), size="large")
PAGE_URLS = ["home", "bill", "reports", "assistant", "analytics", "appliances", "forecast", "anomalies", "alerts", "live", "sensor", "pipeline", "about"]
if auth.current_user() is None:
    # Every page address shows the login form, in a hidden menu: this replaces the app menu (which
    # Streamlit would otherwise keep showing) and a link like /live opens that page after logging in.
    def _login():
        theme.apply("home", st.session_state.theme_mode, st.session_state.theme_color, rtl=is_ar())
        if is_ar():
            st.markdown(RTL_CSS, unsafe_allow_html=True)
        auth.login_page(APP_NAME, "Smart-meter electricity pipeline - graduation project",
                        logo=theme.icon_svg(st.session_state.theme_color))

    st.navigation(
        [st.Page(_login, title="Log in", url_path=u, default=(u == "home")) for u in PAGE_URLS],
        position="hidden",
    ).run()
    st.stop()

# ---- Load data once: Snowflake -> files in ./data -> demo data -----------------
data_error = None
fact_df, agg_df, households_df, snowflake_error = load_all()
source_name = "Snowflake"
if fact_df is None:
    local = local_frames(csv_signature(DATA_DIR) + uci_signature(DATA_DIR))
    if local is not None:
        fact_df, agg_df, households_df, source_name = local
    else:
        fact_df, agg_df, households_df = demo_frames()
        source_name = "DEMO (fake data)"
try:
    fact_df["DateTime"] = pd.to_datetime(fact_df["DateTime"])
    fact_df["date_only"] = pd.to_datetime(fact_df["date_only"])
except Exception as e:
    data_error = e

# ---- ML settings: kept in session_state so they survive switching pages -------
ML_DEFAULTS = {"ml_household": ALL_LABEL, "ml_z": 3.5, "ml_contamination": 1.0, "ml_mode": "Both detectors agree"}
for _k, _v in ML_DEFAULTS.items():
    st.session_state.setdefault(_k, _v)
    st.session_state[_k] = st.session_state[_k]  # stops Streamlit dropping it on pages without the widget
HOUSEHOLD_OPTIONS = [ALL_LABEL] + (households_df["LCLid"].tolist() if data_error is None else [])
if st.session_state.ml_household not in HOUSEHOLD_OPTIONS:  # e.g. the data source changed
    st.session_state.ml_household = ALL_LABEL

ml_choice = st.session_state.ml_household
z_thresh = st.session_state.ml_z
contamination = st.session_state.ml_contamination / 100
detect_mode = "both" if st.session_state.ml_mode.startswith("Both") else "either"


def ml_sidebar(with_detectors):
    st.sidebar.subheader("ML settings")
    st.sidebar.selectbox(
        "Household",
        HOUSEHOLD_OPTIONS,
        key="ml_household",
        help="'All households (average)' models the typical household. Pick one to model a single meter.",
    )
    if with_detectors:
        st.sidebar.slider(
            "Anomaly sensitivity (z-score)", 2.5, 6.0, step=0.1, key="ml_z", help="Lower = more anomalies flagged."
        )
        st.sidebar.slider("Isolation Forest: expected anomalies (%)", 0.5, 5.0, step=0.5, key="ml_contamination")
        st.sidebar.radio("Flag an hour when", ["Both detectors agree", "Either detector fires"], key="ml_mode")


def get_ml():
    """Hourly series + trained models for the selected household (cached)."""
    hourly = hourly_for(ml_choice, fact_df, len(fact_df))
    res = train_for(ml_choice, hourly, len(hourly))
    return hourly, res


def get_anomalies(res):
    """detect_anomalies() for the current household and sidebar settings (cached)."""
    return anomalies_for(ml_choice, res["best_name"], len(res["test"]), z_thresh, contamination, detect_mode, res)


def household_label():
    return "All households (avg)" if ml_choice == ALL_LABEL else ml_choice


# ---- Bill & Budget settings (kept across pages, like the ML settings) -----------
TIER_LABELS = ["0–50", "51–100", "101–200", "201–350", "351–650", "651–1000", "over 1000"]
BILL_DEFAULTS = {"bill_mode": "Egyptian tiers (Aug 2026)", "bill_price": 2.0, "bill_budget": 500.0, "bill_today": True}
BILL_DEFAULTS.update({f"tier_{i}": price for i, (_, price) in enumerate(EGYPT_TIERS)})
if data_error is None:
    BILL_DEFAULTS["bill_asof"] = fact_df["DateTime"].max().date()  # the data is historical: start at its last day
for _k, _v in BILL_DEFAULTS.items():
    st.session_state.setdefault(_k, _v)
    st.session_state[_k] = st.session_state[_k]


def bill_tariff():
    """(tiers, flat_price) from the Bill & Budget settings; exactly one of them is used."""
    if st.session_state.bill_mode.startswith("Fixed"):
        return None, float(st.session_state.bill_price)
    return [(upper, float(st.session_state[f"tier_{i}"])) for i, (upper, _) in enumerate(EGYPT_TIERS)], None


@st.cache_data(max_entries=20, show_spinner="Reading today's meter...")
def today_for(household_key, best_name, hour, _hourly, _res):
    # today's simulated meter (reports.today_series), recomputed when a new hour of readings "arrives"
    return today_series(_hourly, _res["best_model"], hour)


def bill_summary(hourly, res):
    """
    (month projection, tiers, flat price, hourly series used, offset) for the Bill page and Home.
    Today (default): the history replayed on today's date, the model for the rest of today and tomorrow.
    Otherwise the 'as of' day picked in the data. The model forecasts the next 24 hours, typical days the rest.
    """
    tiers, flat = bill_tariff()
    if st.session_state.bill_today:
        now = now_local().floor("h")
        try:
            series, fc, offset = today_for(ml_choice, res["best_name"], now, hourly, res)
            nxt = float(fc.loc[now.normalize() + pd.Timedelta(days=1):].sum())
            return month_projection(series, now.normalize(), nxt, tiers, flat), tiers, flat, series, offset
        except Exception:  # too little history to replay: use the day picked in the data instead
            pass
    as_of = pd.Timestamp(st.session_state.bill_asof)
    try:
        nxt = float(forecast_next(res["best_model"], hourly.loc[: as_of + pd.Timedelta(hours=23)], steps=24).sum())
    except Exception:  # too little history before that day
        nxt = None
    return month_projection(hourly, as_of, nxt, tiers, flat), tiers, flat, hourly, pd.Timedelta(0)


def appliance_month_kwh(proj, offset=pd.Timedelta(0)):
    """{appliance: kWh} measured this month up to the 'as of' day, when the data has sub-meters (UCI)."""
    cols = list(APPLIANCE_COLUMNS)
    if not set(cols) <= set(fact_df.columns):
        return None
    t = fact_df["DateTime"] + offset  # offset: how far the history was moved to replay it on today's date
    d = fact_df[(t >= proj["month"]) & (t < proj["as_of"] + pd.Timedelta(days=1))]
    if ml_choice != ALL_LABEL:
        d = d[d["LCLid"] == ml_choice]
    s = d[cols].sum()
    return {APPLIANCE_COLUMNS[c]: float(s[c]) for c in cols} if s.sum() > 0 else None


def show_tips(tips):
    for kind, text in tips:
        {"warn": st.warning, "good": st.success}.get(kind, st.info)(text, icon={"warn": "⚠️", "good": "✅"}.get(kind, "💡"))


# ---- Reports settings: the user's saved choice first, else the defaults -------------
if "rep_freq" not in st.session_state:  # once per visit: the saved choice may come from the database
    _saved_report = store.load_subscriptions().get(auth.current_user()["name"], {})
    st.session_state.update({
        "rep_freq": _saved_report.get("frequency", "Daily"),
        "rep_channels": _saved_report.get("channels", ["Gmail"]),
        "rep_email": _saved_report.get("email", auth.current_user().get("email", "")),
        "rep_phone": _saved_report.get("phone", ""),
        "rep_wa_key": _saved_report.get("whatsapp_apikey", ""),
        "rep_lang": _saved_report.get("language", st.session_state.lang),
    })
for _k in ("rep_freq", "rep_channels", "rep_email", "rep_phone", "rep_wa_key", "rep_lang"):
    st.session_state[_k] = st.session_state[_k]
MAX_SENDS_PER_SESSION = 3  # "Send me one now" uses the app's Gmail / Twilio account, so keep it small


@st.cache_data(max_entries=30, show_spinner="Writing the report...")
def report_for(household_key, best_name, frequency, hour, tiers, flat, budget, lang, _hourly, _res, _fact):
    # cached per household, frequency, tariff, language and hour: the report changes when a new hour of readings arrives
    return build_report(_hourly, _res["best_model"], frequency, now=hour, tiers=tiers, flat_price=flat,
                        budget_egp=budget, fact=_fact, lang=lang)


def report_senders():
    """(smtp settings, twilio settings) from the secrets, or None when they are missing."""
    smtp, tw = secret_section("smtp", {}), secret_section("twilio", {})
    smtp = dict(smtp) if smtp.get("user") and smtp.get("password") else None
    tw = dict(tw) if all(tw.get(k) for k in ("account_sid", "auth_token", "from_number")) else None
    return smtp, tw


# ---- Tab renderers -----------------------------------------------------------
def render_analytics():
    col1, col2 = st.columns([1, 3])
    with col1:
        selected_households = st.multiselect(
            "Households",
            options=households_df["LCLid"].tolist(),
            default=households_df["LCLid"].tolist(),
        )
        date_min = fact_df["date_only"].min()
        date_max = fact_df["date_only"].max()
        date_range = st.date_input("Date range", value=(date_min, date_max))
        weekend_filter = st.radio("Day type", ["All", "Weekday", "Weekend"], horizontal=True)

    filtered = fact_df[fact_df["LCLid"].isin(selected_households)]
    if isinstance(date_range, tuple) and len(date_range) == 2:
        start, end = pd.to_datetime(date_range[0]), pd.to_datetime(date_range[1])
        filtered = filtered[(filtered["date_only"] >= start) & (filtered["date_only"] <= end)]
    if weekend_filter != "All":
        filtered = filtered[filtered["day_type"] == weekend_filter]

    with col2:
        m1, m2, m3 = st.columns(3)
        m1.metric("Total consumption (kWh)", f"{filtered['KWH_hh'].sum():,.1f}")
        m2.metric("Avg reading (kWh)", f"{filtered['KWH_hh'].mean():.3f}" if len(filtered) else "—")
        m3.metric("Households selected", len(selected_households))

    st.subheader("Consumption trend over time")
    trend = filtered.groupby(filtered["date_only"].dt.date)["KWH_hh"].sum().reset_index()
    trend.columns = ["date", "total_kwh"]
    few_days = len(trend) <= 120  # a dot per day only when there are few days; 1,400 dots are slow to draw
    if len(trend) > 1:
        chart = alt.Chart(trend).mark_line(point=few_days).encode(
            x="date:T", y="total_kwh:Q", tooltip=["date:T", "total_kwh:Q"]
        )
        st.altair_chart(chart, width="stretch")
    else:
        st.info("Not enough data points for the selected filters.")

    st.subheader("Average consumption per household")
    agg_filtered = agg_df[agg_df["LCLid"].isin(selected_households)].sort_values(
        "avg_consumption", ascending=False
    )
    bar = alt.Chart(agg_filtered).mark_bar().encode(
        x=alt.X("LCLid:N", sort="-y"), y="avg_consumption:Q", tooltip=["LCLid", "avg_consumption"]
    )
    st.altair_chart(bar, width="stretch")

    st.subheader("Naive trend (baseline)")
    if len(trend) >= 5:
        trend_sorted = trend.sort_values("date").reset_index(drop=True)
        x = np.arange(len(trend_sorted))
        y = trend_sorted["total_kwh"].values
        coeffs = np.polyfit(x, y, 1)  # naive linear trend
        future_x = np.arange(len(trend_sorted), len(trend_sorted) + 7)
        future_y = np.polyval(coeffs, future_x)
        future_dates = pd.date_range(
            pd.to_datetime(trend_sorted["date"].max()) + pd.Timedelta(days=1), periods=7
        )
        forecast_df = pd.DataFrame({"date": future_dates, "total_kwh": future_y, "type": "forecast"})
        actual_df = trend_sorted.assign(date=pd.to_datetime(trend_sorted["date"]), type="actual")
        combined = pd.concat([actual_df, forecast_df], ignore_index=True)
        forecast_chart = alt.Chart(combined).mark_line(point=few_days).encode(
            x="date:T",
            y="total_kwh:Q",
            color=alt.Color("type:N", scale=alt.Scale(domain=["actual", "forecast"], range=ACTUAL_VS_MODEL)),
            strokeDash="type:N",
        )
        st.altair_chart(forecast_chart, width="stretch")
        st.caption(
            "Straight-line baseline only. The ML forecast (Random Forest / XGBoost) is on the Forecast page."
        )
    else:
        st.info("Need at least 5 days of data to compute a trend.")


def render_appliances():
    cols = list(APPLIANCE_COLUMNS)
    if not set(cols) <= set(fact_df.columns):
        st.info(
            "This dataset has no appliance-level columns (it only has whole-house consumption). "
            f"To see this page, put {UCI_FILENAME} (UCI 'Individual Household Electric Power Consumption') "
            "in the 'data' folder."
        )
        return

    dmin, dmax = fact_df["date_only"].min().date(), fact_df["date_only"].max().date()
    c1, c2 = st.columns([2, 1])
    rng_sel = c1.date_input("Appliance date range", value=(dmin, dmax), min_value=dmin, max_value=dmax)
    grain = c2.radio("Group by", ["Daily", "Weekly", "Monthly"], index=2, horizontal=True)

    d = fact_df
    if isinstance(rng_sel, (tuple, list)) and len(rng_sel) == 2:
        d = d[(d["date_only"] >= pd.to_datetime(rng_sel[0])) & (d["date_only"] <= pd.to_datetime(rng_sel[1]))]
    if d.empty:
        st.info("No data in the selected range.")
        return

    totals = d[cols].sum()
    whole = totals.sum()
    st.subheader("Where the energy goes")
    for box, col in zip(st.columns(len(cols)), cols):
        box.metric(APPLIANCE_COLUMNS[col], f"{totals[col]:,.0f} kWh", f"{100 * totals[col] / max(whole, 1e-9):.1f}% of total", delta_color="off")
    # cost: price of a typical month of this house, at the tariff chosen on the Bill & Budget page
    months = max((d["DateTime"].max() - d["DateTime"].min()).days / 30.44, 1.0)
    per_month = whole / months
    tiers, flat = bill_tariff()
    avg_price = bill_egp(per_month, tiers, flat) / per_month if per_month else 0.0
    st.caption(
        "Estimated cost at the current tariff: "
        + " · ".join(f"{APPLIANCE_COLUMNS[c]} **{totals[c] * avg_price:,.0f} EGP**" for c in cols)
        + f" (average {avg_price:.2f} EGP/kWh for a typical month of {per_month:,.0f} kWh)."
    )

    st.subheader("Consumption over time, by appliance")
    rule = {"Daily": "D", "Weekly": "W", "Monthly": "MS"}[grain]
    over_time = d.set_index("DateTime")[cols].resample(rule).sum().rename(columns=APPLIANCE_COLUMNS)
    over_time = over_time.rename_axis("period").reset_index().melt("period", var_name="appliance", value_name="kwh")
    st.altair_chart(
        alt.Chart(over_time)
        .mark_area()
        .encode(
            x="period:T",
            y="kwh:Q",
            color=alt.Color("appliance:N", scale=APPLIANCE_COLORS),
            tooltip=["period:T", "appliance:N", "kwh:Q"],
        ),
        width="stretch",
    )

    st.subheader("Typical day: average kWh per hour, by appliance")
    prof = (d.groupby("hour_of_day")[cols].mean() * 2).rename(columns=APPLIANCE_COLUMNS)  # 2 half-hours = 1 hour
    prof = prof.rename_axis("hour").reset_index().melt("hour", var_name="appliance", value_name="kwh")
    st.altair_chart(
        alt.Chart(prof)
        .mark_line(point=True)
        .encode(x="hour:Q", y="kwh:Q", color=alt.Color("appliance:N", scale=APPLIANCE_COLORS)),
        width="stretch",
    )
    st.caption(
        '"Other" is everything the three sub-meters do not measure: total energy minus kitchen, laundry '
        "and water heater / air conditioner."
    )


def render_forecast():
    try:
        hourly, res = get_ml()
    except Exception as e:
        st.warning(f"Could not train the models for this selection: {e}")
        return

    st.subheader("Model comparison (held-out last 20% of the time series)")
    st.dataframe(res["metrics"].round(4), hide_index=True, width="stretch")
    st.caption(
        "Lower MAE / RMSE is better. The baseline predicts 'same hour yesterday'; "
        "a model is only useful if it beats it."
    )
    st.success(f"Best model: {res['best_name']}")

    st.subheader("Why does the model predict this?")
    imp = feature_importance(res["best_model"])
    why = pd.DataFrame({"feature": [FEATURE_NAMES[f] for f in imp.index], "share": imp.values})
    st.altair_chart(
        alt.Chart(why).mark_bar(color=ACTUAL_VS_MODEL[1]).encode(
            x=alt.X("share:Q", title="Share of the model's decisions", axis=alt.Axis(format="%")),
            y=alt.Y("feature:N", sort="-x", title=None, axis=alt.Axis(labelLimit=260)),
            tooltip=["feature:N", alt.Tooltip("share:Q", format=".1%")],
        ),
        width="stretch",
    )
    top = why.iloc[0]
    st.caption(f"The {res['best_name']} model relies most on **{top['feature'].lower()}** ({top['share']:.0%}). "
               "Lag = the consumption some hours before; the model learns the household's daily and weekly habits.")

    st.subheader("Actual vs predicted (unseen period)")
    days = st.slider("Days to show", 2, 30, 7)
    shown = res["test"].tail(days * 24)
    plot = pd.DataFrame(
        {
            "time": shown.index,
            "Actual": shown["kwh"].values,
            "Predicted": res["best_model"].predict(shown[FEATURES]),
        }
    ).melt("time", var_name="series", value_name="kwh")
    st.altair_chart(
        alt.Chart(plot)
        .mark_line()
        .encode(
            x="time:T",
            y="kwh:Q",
            color=alt.Color("series:N", scale=alt.Scale(domain=["Actual", "Predicted"], range=ACTUAL_VS_MODEL)),
        )
        .add_params(pan_zoom("forecast")),
        width="stretch",
    )
    st.caption(ZOOM_HELP + " (also resets the chart below)")
    reset_zoom_button("forecast")

    st.subheader("Next 24 hours")
    try:
        fc = forecast_next(res["best_model"], hourly, steps=24)
        recent = hourly.dropna().tail(24 * 5)
        nxt = pd.concat(
            [
                pd.DataFrame({"time": recent.index, "kwh": recent.values, "type": "actual"}),
                pd.DataFrame({"time": fc.index, "kwh": fc.values, "type": "forecast"}),
            ],
            ignore_index=True,
        )
        st.altair_chart(
            alt.Chart(nxt)
            .mark_line()
            .encode(
                x="time:T",
                y="kwh:Q",
                color=alt.Color("type:N", scale=alt.Scale(domain=["actual", "forecast"], range=ACTUAL_VS_MODEL)),
                strokeDash="type:N",
            )
            .add_params(pan_zoom("forecast")),
            width="stretch",
        )
        st.caption(
            "Forecast starts after the last reading in the database, so it is only 'tomorrow' "
            "if the data is up to date. " + ZOOM_HELP
        )
    except Exception as e:
        st.info(f"Could not compute the 24-hour forecast: {e}")


def render_anomalies():
    try:
        _, res = get_ml()
    except Exception as e:
        st.warning(f"Could not train the models for this selection: {e}")
        return

    an = get_anomalies(res)
    flagged = an[an["is_anomaly"]]

    c1, c2, c3 = st.columns(3)
    c1.metric("Hours checked", f"{len(an):,}")
    c2.metric("Anomalies flagged", f"{len(flagged):,}")
    c3.metric("Share of hours", f"{100 * len(flagged) / max(len(an), 1):.2f}%")

    d = an[["kwh", "pred", "residual", "z", "is_anomaly"]].rename_axis("time").reset_index()
    line = alt.Chart(d[["time", "kwh"]]).mark_line(opacity=0.6).encode(x="time:T", y="kwh:Q")
    pts = (
        alt.Chart(d[d["is_anomaly"]][["time", "kwh", "pred", "z"]])
        .mark_point(color="red", filled=True, size=70)
        .encode(x="time:T", y="kwh:Q", tooltip=["time:T", "kwh:Q", "pred:Q", "z:Q"])
    )
    st.altair_chart((line + pts).add_params(pan_zoom("anomalies")), width="stretch")
    st.caption(
        "Anomalies are only checked on the held-out period (data the model never trained on), "
        "otherwise the model has already 'seen the answers'. " + ZOOM_HELP
    )
    reset_zoom_button("anomalies")

    if len(flagged):
        st.dataframe(
            flagged[["kwh", "pred", "residual", "z"]].round(3).rename_axis("time").reset_index(),
            hide_index=True,
            width="stretch",
        )
    else:
        st.info("No anomalies at the current sensitivity.")


def render_alerts():
    try:
        _, res = get_ml()
    except Exception as e:
        st.warning(f"Could not train the models for this selection: {e}")
        return

    default_thr = compute_threshold(res["train"]["kwh"])
    c1, c2 = st.columns(2)
    thr = c1.number_input(
        "Consumption limit (kWh per hour)",
        min_value=0.0,
        value=round(default_thr, 2),
        step=0.1,
        help="Default = 99th percentile of this household's past hourly consumption.",
    )
    use_anom = c2.checkbox("Also raise anomaly alerts", value=True)

    an = get_anomalies(res)
    if not use_anom:
        an = an.assign(is_anomaly=False)
    alerts = build_alerts(an, household=household_label(), threshold_kwh=thr)

    m1, m2, m3 = st.columns(3)
    m1.metric("Alerts", len(alerts))
    m2.metric("HIGH severity", int((alerts["severity"] == "HIGH").sum()))
    m3.metric("Anomaly alerts", int(alerts["type"].str.contains("ANOMALY").sum()))

    if alerts.empty:
        st.success("No alerts in the period checked.")
        return

    st.dataframe(alerts, hide_index=True, width="stretch")
    st.download_button(
        "Download alerts (CSV)", alerts.to_csv(index=False).encode("utf-8"), "alerts.csv", "text/csv"
    )

    if not auth.is_admin():  # viewers can look and download, not send emails
        return
    smtp = secret_section("smtp")
    if smtp:
        if st.button("Email these alerts"):
            try:
                cfg = {
                    "host": smtp["host"],
                    "port": smtp.get("port", 587),
                    "user": smtp["user"],
                    "password": smtp["password"],
                    "to": list(smtp["to"]),
                }
                send_email_alert(alerts, cfg)
                st.success("Email sent.")
            except Exception as e:
                st.error(f"Could not send the email: {e}")
    else:
        st.caption("Add an [smtp] section to secrets.toml to enable emailing alerts.")


def render_live():
    try:
        _, res = get_ml()
    except Exception as e:
        st.warning(f"Could not train the models for this selection: {e}")
        return

    def new_replay():
        st.session_state.live = LiveReplay(
            res["test"],
            res["best_model"],
            compute_threshold(res["train"]["kwh"]),
            household=household_label(),
            spike_kwh=3.0,  # +2 kWh alerted in only ~57% of UCI hours at z = 6, +3 kWh in ~97%
        )
        st.session_state.live_running = False

    key = (ml_choice, res["best_name"], len(res["test"]))  # new replay when the household / data changes
    if st.session_state.get("live_key") != key:
        new_replay()
        st.session_state.live_key = key
    live = st.session_state.live

    st.caption(
        "Replays the unseen hours one by one as if they were arriving from the meter now. "
        "Uses the z-score detector only (Isolation Forest needs a whole batch) and the 99th-percentile limit."
    )
    c1, c2, c3, c4 = st.columns(4)
    if c1.button("Start", icon=":material/play_arrow:", type="primary", disabled=live.finished, width="stretch"):
        st.session_state.live_running = True
    if c2.button("Pause", icon=":material/pause:", width="stretch"):
        st.session_state.live_running = False
    if c3.button("Reset", icon=":material/restart_alt:", width="stretch"):
        new_replay()
        live = st.session_state.live
    if c4.button(
        "Spike",
        icon=":material/bolt:",
        disabled=live.finished,
        help="Adds +3 kWh to the next reading, to show an alert.",
        width="stretch",
    ):
        live.inject_next = True

    s1, s2 = st.columns(2)
    speed = s1.selectbox("Speed", [0.5, 1.0, 2.0], index=1, format_func=lambda s: f"1 hour every {s:g} s")
    # own setting, not the sidebar one: z-score alone on real data flags ~4% of hours at 3.5, ~0.8% at 6
    live.z_thresh = s2.slider(
        "Live anomaly sensitivity (z-score)",
        2.5,
        10.0,
        6.0,
        0.5,
        help="Lower = more anomaly alerts. A change applies to the next readings only.",
    )

    # a full app rerun (any button / widget click) must not advance the stream; only the timer does
    st.session_state.live_skip_step = True

    @st.fragment(run_every=speed if st.session_state.live_running else None)
    def live_panel():
        live = st.session_state.live
        if st.session_state.live_running and not st.session_state.pop("live_skip_step", False):
            out = live.step()
            st.session_state.live_last_wall = pd.Timestamp.now()  # when this reading 'arrived'
            if out is not None:
                for _, a in out[1].iterrows():
                    st.toast(f"{a['type']} ({a['severity']}): {a['message']}", icon="🚨")
            if live.finished:
                st.session_state.live_running = False
                st.rerun()  # full rerun so the timer stops

        last = live.seen.iloc[-1]
        last_ts = live.seen.index[-1]
        alerted = last_ts in set(live.alerts["timestamp"])
        wall = st.session_state.get("live_last_wall") if live.streamed else None
        st.markdown(
            f":material/schedule: **Replay clock:** {last_ts:%Y-%m-%d %H:%M} &nbsp;·&nbsp; "
            f":material/sensors: **Last reading received:** {f'{wall:%H:%M:%S}' if wall is not None else 'waiting for Start'}"
            " &nbsp;·&nbsp; *simulated meter*"
        )
        today_kwh = live.seen.loc[live.seen.index.normalize() == last_ts.normalize(), "kwh"].sum()
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Power now", f"{last['kwh'] * 1000:,.0f} W", help="Average power over the last hour (kWh per hour x 1000).")
        m2.metric("Expected", f"{last['pred'] * 1000:,.0f} W")
        m3.metric("Today so far", f"{today_kwh:.1f} kWh", help="Since midnight of the replay day.")
        if live.streamed == 0:
            m4.metric("Status", "⏳ ready")
        else:
            m4.metric("Status", "🔴 ALERT" if alerted else "🟢 normal")

        state = "running" if st.session_state.live_running else ("finished" if live.finished else "paused")
        st.progress(
            live.streamed / max(live.to_stream, 1),
            text=f"{state}: {live.streamed:,} of {live.to_stream:,} hours streamed"
            + (" | spike queued for the next reading" if live.inject_next else ""),
        )

        recent = live.seen.tail(48).rename_axis("time").reset_index()
        long = recent.melt("time", ["kwh", "pred"], var_name="series", value_name="kwh_value")
        long["series"] = long["series"].map({"kwh": "actual", "pred": "expected"})
        domain = ["actual", "expected"]
        lines = alt.Chart(long).mark_line().encode(
            x="time:T",
            y=alt.Y("kwh_value:Q", title="kWh"),
            color=alt.Color("series:N", scale=alt.Scale(domain=domain, range=ACTUAL_VS_MODEL)),
            strokeDash=alt.StrokeDash("series:N", scale=alt.Scale(domain=domain, range=[[1, 0], [5, 3]])),
        )
        limit = alt.Chart(pd.DataFrame({"limit": [live.threshold_kwh]})).mark_rule(
            color="red", strokeDash=[2, 2]
        ).encode(y="limit:Q")
        pts = (
            alt.Chart(recent[recent["time"].isin(set(live.alerts["timestamp"]))])
            .mark_point(color="red", filled=True, size=90)
            .encode(x="time:T", y="kwh:Q", tooltip=["time:T", "kwh:Q", "pred:Q", "z:Q"])
        )
        st.altair_chart(lines + limit + pts, width="stretch")
        st.caption(
            f"Last 48 hours. Red dashed line = limit ({live.threshold_kwh:.2f} kWh); red dots = alerts."
        )

        st.subheader(f"Live alert log ({len(live.alerts)})")
        if live.alerts.empty:
            st.info("No alerts yet.")
        else:
            st.dataframe(live.alerts, hide_index=True, width="stretch")

    live_panel()


def render_sensor():
    """The real ESP32 + ACS712 readings from Supabase, refreshed every 5 seconds."""
    c1, c2 = st.columns(2)
    hours = c1.selectbox("Show the last", [1, 6, 24, 168], index=0,
                         format_func=lambda h: "7 days" if h == 168 else f"{h} hour{'s' if h > 1 else ''}")
    volts = c2.number_input("Supply voltage (V)", 1.0, 240.0, 12.0, 1.0,
                            help="The sensor measures current only. Power is estimated as current x this voltage.")

    @st.fragment(run_every=5)
    def sensor_panel():
        df, err = sensor.fetch_readings(hours)
        if err:
            st.warning(err)
            return
        s = sensor.summary(df, volts=volts)
        if not s["has_data"]:
            st.info("No readings in this period yet. Turn on the ESP32: it sends one reading every 5 seconds.")
            return

        if s["online"]:
            st.success(f"**{s['device_id']}** is online · last reading {sensor.age_text(s['age_s'])}", icon=":material/wifi:")
        else:
            st.error(f"**{s['device_id']}** is offline · last reading {sensor.age_text(s['age_s'])}", icon=":material/wifi_off:")
        if s["online"] and s["over"]:
            st.error(f"Current is above the limit ({s['current_a']:.2f} A > {s['threshold_a']:.2f} A)", icon="🚨")

        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Current now", f"{s['current_a']:.2f} A", help="Average (RMS) current over the last 5 seconds.")
        m2.metric("Power (estimate)", f"{s['watts']:.1f} W", help=f"Current x {volts:g} V.")
        m3.metric("Peak in this period", f"{s['max_a']:.2f} A")
        m4.metric("Readings above limit", f"{s['alerts']}", help=f"Out of {s['rows']} readings.")

        show = df.assign(time=df["created_at"].dt.tz_convert("Africa/Cairo").dt.tz_localize(None))
        line = alt.Chart(show).mark_line(point=len(show) < 60).encode(
            x=alt.X("time:T", title=None),
            y=alt.Y("current_a:Q", title="Current (A)"),
            tooltip=[alt.Tooltip("time:T", format="%H:%M:%S"), alt.Tooltip("current_a:Q", format=".2f"),
                     alt.Tooltip("peak_a:Q", format=".2f")],
        )
        limit = alt.Chart(pd.DataFrame({"limit": [s["threshold_a"]]})).mark_rule(
            color="red", strokeDash=[4, 4]).encode(y="limit:Q")
        st.altair_chart(line + limit, width="stretch")
        wifi = f" Wi-Fi signal: {s['rssi_dbm']} dBm." if s["rssi_dbm"] is not None else ""
        st.caption(f"Red dashed line = alert limit ({s['threshold_a']:.2f} A). Times are Cairo time.{wifi}")

        with st.expander("Latest readings"):
            st.dataframe(
                show.sort_values("time", ascending=False).head(50)[
                    ["time", "current_a", "peak_a", "over_threshold", "samples", "rssi_dbm"]],
                hide_index=True, width="stretch")

    sensor_panel()


def render_monitor():
    base_url, dag_id, af_user, af_password = airflow_config()

    if st.button("Refresh status"):
        st.cache_data.clear()

    try:
        runs = get_airflow_dag_runs(base_url, dag_id, af_user, af_password, limit=10)
    except Exception as e:
        st.error(f"Could not reach Airflow API at {base_url}: {e}")
        runs = []

    if runs:
        latest = runs[0]
        st.subheader(f"Latest run: {latest['dag_run_id']}")
        state_icon = STATE_COLORS.get(latest["state"], "⚪")
        st.write(f"{state_icon} State: **{latest['state']}**  |  Execution date: {latest['execution_date']}")

        try:
            tasks = get_airflow_task_instances(
                base_url, dag_id, latest["dag_run_id"], af_user, af_password
            )
            task_df = pd.DataFrame(
                [{"task_id": t["task_id"], "state": t["state"]} for t in tasks]
            )
            task_df["status"] = task_df["state"].map(lambda s: STATE_COLORS.get(s, "⚪") + " " + str(s))
            st.table(task_df[["task_id", "status"]])
        except Exception as e:
            st.warning(f"Could not load task instances: {e}")

        st.subheader("Run history")
        history_df = pd.DataFrame(
            [{"run_id": r["dag_run_id"], "state": r["state"], "execution_date": r["execution_date"]} for r in runs]
        )
        history_df["status"] = history_df["state"].map(lambda s: STATE_COLORS.get(s, "⚪") + " " + str(s))
        st.table(history_df[["run_id", "execution_date", "status"]])
    else:
        st.info("No DAG runs found yet, or Airflow API is unreachable.")


APPLIANCE_ESTIMATE_DEFAULTS = pd.DataFrame(
    [
        ("Air conditioner (1.5 HP)", True, 1100, 8.0),
        ("Refrigerator", True, 150, 10.0),
        ("Electric water heater", True, 1500, 2.0),
        ("Washing machine", True, 500, 1.0),
        ("Television", True, 100, 5.0),
        ("Lighting (all LED)", True, 100, 6.0),
        ("Computer / laptop", True, 60, 4.0),
    ],
    columns=["Device", "Have it", "Power (W)", "Hours per day"],
)


def bill_whatif(proj, tiers, flat):
    """Drag the monthly use: the bill and the price tier change right away (same calculation as the bill above)."""
    egp, kwh_unit = T("EGP"), T("kWh")
    with st.container(key="card_whatif"):
        st.subheader(T("What would your bill be?"))
        st.session_state.setdefault("bill_whatif", int(min(round(proj["projected_kwh"], -1), 1500)))
        kwh = st.slider(T("Monthly use ({unit})", unit=kwh_unit), 0, 1500, step=10, key="bill_whatif")
        c1, c2 = st.columns([1, 2], vertical_alignment="center")
        c1.metric(T("Estimated bill"), f"{bill_egp(kwh, tiers, flat):,.0f} {egp}")
        if flat is None:
            nt = next_tier(kwh, tiers)
            c2.markdown(T("{x} kWh left before the price rises to {p} EGP per kWh.", x=f"{nt[0] - kwh:,}", p=f"{nt[1]:.2f}")
                        if nt else T("You are in the highest price tier."))
            current = next(i for i, (upper, _) in enumerate(tiers) if upper is None or kwh < upper)
            st.html('<div class="energy-tiers">' + "".join(
                f'<div class="{"on" if i == current else ""}">{price:.2f}</div>' for i, (_, price) in enumerate(tiers)) + "</div>")
            st.caption(T("EGP per kWh in each price tier"))


def render_bill():
    try:
        hourly, res = get_ml()
    except Exception as e:
        st.warning(T("Could not train the models for this selection: {e}", e=e))
        return
    dmin, dmax = hourly.dropna().index.min().date(), hourly.dropna().index.max().date()
    egp, kwh_unit = T("EGP"), T("kWh")

    c1, c2, c3 = st.columns([1.4, 1, 1])
    c1.radio(T("Tariff"), ["Egyptian tiers (Aug 2026)", "Fixed price per kWh"], key="bill_mode", horizontal=True,
             format_func=T)
    c2.number_input(T("Monthly budget (EGP)"), min_value=0.0, step=50.0, key="bill_budget")
    with c3:
        st.toggle(T("Today (live simulation)"), key="bill_today",
                  help=T("On: the meter readings are replayed on today's date and the model forecasts the rest. "
                         "Off: pick a day in the historical data."))
        if not st.session_state.bill_today:
            st.date_input(T("Month of (as of this day)"), min_value=dmin, max_value=dmax, key="bill_asof",
                          help=T("The data is historical, so pick the day that counts as 'today'."))
    if st.session_state.bill_mode.startswith("Fixed"):
        st.number_input(T("Price (EGP per kWh)"), min_value=0.05, step=0.05, key="bill_price")
    else:
        with st.expander(T("Tariff tiers (EGP per kWh) - edit them if the government changes the prices")):
            for box, (i, label) in zip(st.columns(len(TIER_LABELS)), enumerate(TIER_LABELS)):
                box.number_input(f"{label} {kwh_unit}", min_value=0.0, step=0.01, format="%.2f", key=f"tier_{i}")

    proj, tiers, flat, series, offset = bill_summary(hourly, res)
    budget = float(st.session_state.bill_budget)

    m1, m2, m3, m4 = st.columns(4)
    m1.metric(T("This month so far"), f"{proj['used_egp']:,.0f} {egp}", f"{proj['used_kwh']:,.0f} {kwh_unit}",
              delta_color="off")
    m2.metric(T("Expected at month end"), f"{proj['projected_egp']:,.0f} {egp}",
              f"{proj['low_egp']:,.0f}–{proj['high_egp']:,.0f} {egp}", delta_color="off",
              help=T("The range shows how much your daily use varied in the last 28 days."))
    if proj["prev_egp"]:
        m3.metric(T("Last month"), f"{proj['prev_egp']:,.0f} {egp}",
                  T("{x} this month", x=f"{(proj['projected_egp'] - proj['prev_egp']) / proj['prev_egp']:+.0%}"),
                  delta_color="inverse")
    else:
        m3.metric(T("Last month"), "—", help=T("Not enough data for last month."))
    if budget > 0:
        left = budget - proj["projected_egp"]
        m4.metric(T("Budget"), f"{budget:,.0f} {egp}",
                  T("{x} EGP left", x=f"{left:,.0f}") if left >= 0 else T("{x} EGP over", x=f"{-left:,.0f}"),
                  delta_color="normal" if left >= 0 else "inverse")
        if left < 0:
            st.error(T("Budget alert: the expected bill ({a} EGP) is over your budget of {b} EGP.",
                       a=f"{proj['projected_egp']:,.0f}", b=f"{budget:,.0f}"), icon="🚨")
        else:
            st.success(T("On track: the expected bill is {x} EGP under your budget.", x=f"{left:,.0f}"), icon="✅")
    if proj["history_days"] < 28:
        st.info(T("Experimental forecast: only {n} days of history before this day, so the month-end value is "
                  "less certain.", n=proj["history_days"]), icon="🧪")
    st.caption(T("{month}, as of {day}: {n} days left. Estimate only: each kWh is priced at its tier's price; real "
                 "bills can add fees. Tomorrow uses the model's 24-hour forecast, the other days your average day.",
                 month=month_year(proj["month"]), day=day_month(proj["as_of"]), n=proj["remaining_days"]))
    month_co2 = co2_kg(proj["projected_kwh"])
    st.markdown(T("🌍 **Carbon footprint:** about **{m} kg CO₂** this month ({u} kg so far). Absorbing it would take "
                  "about **{t} trees** a year.", m=f"{month_co2:,.0f}", u=f"{co2_kg(proj['used_kwh']):,.0f}",
                  t=f"{month_co2 * 12 / CO2_KG_PER_TREE_YEAR:,.0f}"),
                help=T("Estimate: {f} kg CO₂ per kWh for Egypt's mostly natural-gas power, one tree absorbs about "
                       "{t} kg CO₂ a year.", f=CO2_KG_PER_KWH, t=CO2_KG_PER_TREE_YEAR))

    bill_whatif(proj, tiers, flat)

    # cumulative bill through the month: actual days, then expected days, against the budget
    actual, expected = T("actual"), T("expected")
    daily = proj["daily"].fillna(0)
    rows, total = [], 0.0
    for day, kwh in daily.items():
        total += kwh
        rows.append((day, bill_egp(total, tiers, flat), actual))
    rows_exp = [(proj["as_of"], bill_egp(total, tiers, flat), expected)]
    for k in range(1, proj["remaining_days"] + 1):
        total += (proj["projected_kwh"] - proj["used_kwh"]) / proj["remaining_days"]
        rows_exp.append((proj["as_of"] + pd.Timedelta(days=k), bill_egp(total, tiers, flat), expected))
    cum = pd.DataFrame(rows + rows_exp, columns=["day", "egp", "type"])
    st.subheader(T("Bill through the month"))
    chart = alt.Chart(cum).mark_line(point=True).encode(
        x=alt.X("day:T", title=None),
        y=alt.Y("egp:Q", title=egp),
        color=alt.Color("type:N", title=None, scale=alt.Scale(domain=[actual, expected], range=ACTUAL_VS_MODEL)),
        strokeDash=alt.StrokeDash("type:N", title=None, scale=alt.Scale(domain=[actual, expected], range=[[1, 0], [5, 3]])),
        tooltip=["day:T", alt.Tooltip("egp:Q", format=",.0f")],
    )
    if budget > 0:
        chart = chart + alt.Chart(pd.DataFrame({"budget": [budget]})).mark_rule(color="red", strokeDash=[4, 3]).encode(y="budget:Q")
    st.altair_chart(chart, width="stretch")
    st.caption(T("Red dashed line = your budget."))

    st.subheader(T("Last 12 months"))
    months = series.loc[: proj["as_of"] + pd.Timedelta(hours=23)].resample("MS").sum(min_count=24 * 20).dropna().tail(12)
    hist = pd.DataFrame({"month": months.index, "kwh": months.values})
    hist["egp"] = [bill_egp(k, tiers, flat) for k in hist["kwh"]]
    st.altair_chart(
        alt.Chart(hist).mark_bar().encode(
            x=alt.X("month:T", title=None, timeUnit="yearmonth"), y=alt.Y("egp:Q", title=egp),
            tooltip=[alt.Tooltip("month:T", timeUnit="yearmonth"), alt.Tooltip("kwh:Q", format=",.0f"),
                     alt.Tooltip("egp:Q", format=",.0f")],
        ),
        width="stretch",
    )

    st.subheader(T("Where is my electricity going?"))
    measured = appliance_month_kwh(proj, offset)
    if measured:
        tot = sum(measured.values())
        col_app, col_kwh, col_share, col_cost = T("Appliance (measured)"), T("kWh this month"), T("Share"), T("Cost (EGP)")
        st.dataframe(
            pd.DataFrame([(T(n), k, k / tot, proj["used_egp"] * k / tot) for n, k in measured.items()],
                         columns=[col_app, col_kwh, col_share, col_cost]),
            hide_index=True, width="stretch",
            column_config={col_kwh: st.column_config.NumberColumn(format="%.0f"),
                           col_share: st.column_config.ProgressColumn(format="percent", min_value=0, max_value=1),
                           col_cost: st.column_config.NumberColumn(format="%.0f")},
        )
        st.caption(T("Measured by the house's three sub-meters; 'Other' is everything they do not cover."))
    st.markdown(T("**Estimate for your own devices** - tick what you have and set the power and hours "
                  "(a single meter cannot measure each device, so these are estimates):"))
    defaults = APPLIANCE_ESTIMATE_DEFAULTS.assign(Device=APPLIANCE_ESTIMATE_DEFAULTS["Device"].map(T))
    defaults.columns = [T(c) for c in defaults.columns]
    col_dev, col_have, col_w, col_h = defaults.columns
    col_month, col_cost = T("kWh per month"), T("Cost (EGP)")
    est = st.data_editor(defaults, hide_index=True, width="stretch", key=f"appliance_editor_{st.session_state.lang}",
                         column_config={col_w: st.column_config.NumberColumn(min_value=0, step=10),
                                        col_h: st.column_config.NumberColumn(min_value=0.0, max_value=24.0, step=0.5)})
    est = est[est[col_have]].copy()
    if len(est):
        est[col_month] = est[col_w] * est[col_h] * proj["days_in_month"] / 1000
        est_total = est[col_month].sum()
        est[col_cost] = est[col_month] / est_total * bill_egp(est_total, tiers, flat) if est_total else 0.0
        st.dataframe(est[[col_dev, col_month, col_cost]].sort_values(col_month, ascending=False),
                     hide_index=True, width="stretch",
                     column_config={col_month: st.column_config.NumberColumn(format="%.0f"),
                                    col_cost: st.column_config.NumberColumn(format="%.0f")})
        st.caption(T("Your devices add up to about {a} kWh a month; the meter expects {b} kWh for this month.",
                     a=f"{est_total:,.0f}", b=f"{proj['projected_kwh']:,.0f}"))

    st.subheader(T("Energy-saving assistant"))
    show_tips(saving_tips(series, proj, budget or None, measured, tiers, flat, st.session_state.lang))
    st.caption(T("Tips are worked out from your own consumption, forecast and tariff."))


CALLMEBOT_HELP = (
    "Free WhatsApp messages via CallMeBot. Once only: save +34 644 71 81 99 in your contacts and send it the "
    "WhatsApp message 'I allow callmebot to send me messages'. The bot replies with your key; paste it here."
)


def render_reports():
    try:
        hourly, res = get_ml()
    except Exception as e:
        st.warning(T("Could not train the models for this selection: {e}", e=e))
        return
    tiers, flat = bill_tariff()
    budget = float(st.session_state.bill_budget) or None
    egp, kwh_unit = T("EGP"), T("kWh")

    c1, c2, c3 = st.columns([1.2, 1.4, 1])
    c1.radio(T("How often"), FREQUENCIES, key="rep_freq", horizontal=True, format_func=T,
             help=T("Daily: every evening at about 8 PM (Cairo time). Weekly: Saturdays. Monthly: the 1st of the month."))
    c2.multiselect(T("Send it by"), CHANNELS, key="rep_channels", format_func=T)
    c3.radio(T("Report language"), ["en", "ar"], key="rep_lang", horizontal=True,
             format_func=lambda c: {"en": "English", "ar": "العربية"}[c])
    channels = st.session_state.rep_channels
    a, b, c = st.columns(3)
    a.text_input(T("Gmail address"), key="rep_email", disabled="Gmail" not in channels, placeholder="you@gmail.com")
    b.text_input(T("Phone (with country code)"), key="rep_phone", placeholder="+201001234567",
                 disabled=not {"WhatsApp", "SMS"} & set(channels))
    c.text_input(T("WhatsApp key (CallMeBot)"), key="rep_wa_key", type="password", disabled="WhatsApp" not in channels,
                 help=T("CALLMEBOT_HELP") if is_ar() else CALLMEBOT_HELP)

    now = now_local().floor("h")
    fact = fact_df if ml_choice == ALL_LABEL else fact_df[fact_df["LCLid"] == ml_choice]
    try:
        r = report_for(ml_choice, res["best_name"], st.session_state.rep_freq, now, tiers, flat, budget,
                       st.session_state.rep_lang, hourly, res, fact)
    except Exception as e:
        st.warning(T("Could not build the report: {e}", e=e))
        return
    proj = r["proj"]

    m1, m2, m3 = st.columns(3)
    m1.metric(T("Today (expected total)"), f"{r['today_kwh']:.1f} {kwh_unit}", f"≈ {r['today_egp']:,.0f} {egp}",
              delta_color="off")
    m2.metric(T("Tomorrow (forecast)"), f"{r['tomorrow_kwh']:.1f} {kwh_unit}",
              T("{x} vs today ({trend})", x=f"{r['change']:+.0%}", trend=T(r["trend"])),
              delta_color="inverse", help=T("Forecast by the trained model for tomorrow, 00:00 to 24:00."))
    m3.metric(T("{month} bill (expected)", month=month_name(proj["month"])), f"{proj['projected_egp']:,.0f} {egp}",
              T("{x} EGP vs budget", x=f"{proj['projected_egp'] - budget:+,.0f}") if budget else None,
              delta_color="inverse", help=T("Budget and tariff come from the Bill & Budget page."))
    st.caption(note({**r, "lang": st.session_state.lang}) + " " +
               T("Readings until {time} today ({date}); the model ({model}) forecasts the rest of today and tomorrow.",
                 time=f"{now:%H:%M}", date=f"{day_month(now)} {now.year}", model=res["best_name"]))

    texts = {"sms": as_sms(r), "whatsapp": as_whatsapp(r), "email": as_email(r)}
    st.subheader(T("Preview"))
    t_wa, t_sms, t_mail = st.tabs([T("WhatsApp"), T("SMS"), "Gmail"])
    with t_wa:
        st.code(texts["whatsapp"], language=None, wrap_lines=True)
    with t_sms:
        st.code(texts["sms"], language=None, wrap_lines=True)
        st.caption(T("{n} characters ≈ {m} SMS.", n=len(texts["sms"]), m=sms_parts(texts["sms"])))
    with t_mail:
        st.markdown(f"**{T('Subject')}:** {texts['email'][0]}")
        with st.container(border=True):
            st.html(texts["email"][2])

    sub = {"frequency": st.session_state.rep_freq, "channels": channels, "email": st.session_state.rep_email.strip(),
           "phone": st.session_state.rep_phone.strip(), "whatsapp_apikey": st.session_state.rep_wa_key.strip(),
           "budget": budget, "language": st.session_state.rep_lang}
    b1, b2 = st.columns(2)
    send = b1.button(T("Send me one now"), type="primary", icon=":material/send:", width="stretch", disabled=not channels)
    save = b2.button(T("Save my choice"), icon=":material/save:", width="stretch", disabled=not channels)
    if send:
        if st.session_state.get("reports_sent", 0) >= MAX_SENDS_PER_SESSION:
            st.error(T("You can send {n} test reports per visit. Log in again to send more.", n=MAX_SENDS_PER_SESSION))
        else:
            st.session_state.reports_sent = st.session_state.get("reports_sent", 0) + 1
            with st.spinner(T("Sending...")):
                results = notify.send_report(sub, texts, *report_senders())
            for ok, msg in results:
                (st.success if ok else st.error)(msg, icon="✅" if ok else "❌")
    if save:
        ok, msg = store.save_subscription(auth.current_user()["name"], sub)
        if ok:
            st.success(T("Saved: {freq} report by {channels}.", freq=T(sub["frequency"]).lower(),
                         channels=", ".join(T(ch) for ch in channels)), icon="✅")
        else:
            st.error(T(msg), icon="❌")
    if store.supabase_config():
        st.info(T("Automatic sending: GitHub Actions sends the reports every evening at about 8 PM (Cairo time) to "
                  "everyone who saved a choice. Choices are kept in the database."), icon="🗓️")
    else:
        st.info(T("Automatic sending: GitHub Actions sends the reports every evening at about 8 PM (Cairo time). On "
                  "this version the admin adds saved choices to the schedule list. Saved choices may be reset when "
                  "the online server restarts."), icon="🗓️")

    if auth.is_admin():
        with st.expander(T("Schedule list (admin only: it has phone numbers and keys)")):
            subs = store.load_subscriptions()
            if subs:
                st.code(json.dumps(subs, indent=1, ensure_ascii=False), language="json")
                st.caption(T("Kept in the Supabase database: GitHub reads them by itself, nothing to copy.")
                           if store.supabase_config() else
                           T("Copy all of it into the GitHub secret REPORT_SUBSCRIBERS (repo → Settings → Secrets and "
                             "variables → Actions); GitHub then sends these reports on schedule."))
            else:
                st.caption(T("Nobody has saved a choice yet."))


MAX_ASSISTANT_CALLS = 30  # each chat reply or photo costs money on the app's API key, so keep a limit per visit


def assistant_call_allowed():
    if st.session_state.get("meter_calls", 0) >= MAX_ASSISTANT_CALLS:
        st.error(T("You reached the assistant's limit of {n} requests per visit. Log in again to continue.",
                   n=MAX_ASSISTANT_CALLS))
        return False
    st.session_state.meter_calls = st.session_state.get("meter_calls", 0) + 1
    return True


def add_assistant_note(text):
    st.session_state.meter_chat.append({"role": "assistant", "content": text})


def render_assistant():
    st.session_state.setdefault("meter_chat", [])
    st.session_state.setdefault("meter_nonce", 0)  # a new number gives the upload box and the form fresh values
    user = auth.current_user()["name"]
    tiers, flat = bill_tariff()
    budget = float(st.session_state.bill_budget) or None
    lang, has_key = st.session_state.lang, meter_assistant.api_key() is not None
    egp, kwh_unit = T("EGP"), T("kWh")

    readings = store.load_meter_readings(user)
    if readings is None:  # never go on without them: the checks need the last reading
        st.error(T("Could not reach the database. Please try again."))
        return
    facts = meter_assistant.consumption_summary(readings, tiers, flat, budget)

    if "kwh_per_day" in facts:
        m1, m2, m3, m4 = st.columns(4)
        m1.metric(T("Last reading"), f"{facts['last_reading_kwh']:,.1f} {kwh_unit}", facts["last_read_on"], delta_color="off")
        m2.metric(T("Use per day"), f"{facts['kwh_per_day']:.1f} {kwh_unit}",
                  T("{x} from the period before", x=f"{facts['change_from_previous_period_percent']:+.0f}%")
                  if "change_from_previous_period_percent" in facts else None, delta_color="inverse",
                  help=T("From your last two readings: {k} kWh in {d} days.", k=f"{facts['period_kwh']:,.1f}", d=facts["period_days"]))
        m3.metric(T("A month at this rate"), f"{facts['typical_month_kwh']:,.0f} {kwh_unit}", help=T("30 days at your use per day."))
        m4.metric(T("Expected bill"), f"{facts['typical_month_bill_egp']:,.0f} {egp}",
                  T("Tariff and budget come from the Bill & Budget page."), delta_color="off")
        if facts["short_period_less_certain"]:
            st.info(T("Your last two readings are less than a week apart, so these numbers are less certain."), icon="🧪")
    elif readings:
        st.info(T("First reading saved: {r} kWh. Send another one after a few days to see your consumption.",
                  r=f"{readings[-1]['reading_kwh']:,.1f}"), icon="👍")
    else:
        st.info(T("Start with a photo of your meter, or type the reading. Consumption is the difference between two "
                  "readings, so you will see your numbers from the second one."), icon="📷")

    c1, c2 = st.columns([2, 1], vertical_alignment="bottom")
    photo = c1.file_uploader(T("Photo of your meter"), type=["jpg", "jpeg", "png", "webp"], disabled=not has_key,
                             key=f"meter_photo_{st.session_state.meter_nonce}",
                             help=T("The photo is sent to the Groq API to read it, and is not saved by this app."))
    read_it = c2.button(T("Read the photo"), type="primary", icon=":material/document_scanner:", width="stretch",
                        disabled=photo is None)
    if c2.button(T("Type a reading"), icon=":material/edit:", width="stretch"):
        st.session_state.meter_pending = {"reading": readings[-1]["reading_kwh"] if readings else 0.0, "source": "manual"}
        st.session_state.meter_nonce += 1
    if not has_key:
        st.caption(T("The chat and photo reading need an API key: add [groq] to secrets.toml (see secrets.toml.example)."))

    if read_it and assistant_call_allowed():
        with st.spinner(T("Reading the meter...")):
            try:
                found = meter_assistant.read_meter_photo(photo.getvalue(), lang)
            except ValueError:
                st.error(T("This file is not an image the app can open."))
            except meter_assistant.AssistantError as e:
                st.error(T(str(e)))
            else:
                st.session_state.meter_nonce += 1
                if found["kind"] == "kwh":
                    st.session_state.meter_pending = {"reading": found["reading"], "source": "photo"}
                add_assistant_note(found["note"])
                st.rerun()

    if readings:
        with st.expander(T("My readings ({n})", n=len(readings))):
            col_date, col_read, col_src = T("Date"), T("Reading (kWh)"), T("Source")
            st.dataframe(pd.DataFrame([(r["read_on"], r["reading_kwh"], T(r["source"])) for r in reversed(readings)],
                                      columns=[col_date, col_read, col_src]), hide_index=True, width="stretch")
            if st.button(T("Delete the last reading"), icon=":material/delete:"):
                ok, msg = store.delete_meter_reading(user, readings[-1]["read_on"])
                if ok:
                    st.rerun()
                st.error(T(msg))

    for m in st.session_state.meter_chat:
        if not m.get("hidden"):
            st.chat_message(m["role"]).markdown(m["content"])

    if st.session_state.pop("meter_ask", False):
        if assistant_call_allowed():
            with st.chat_message("assistant"), st.spinner(T("Thinking...")):
                try:
                    reply = meter_assistant.chat(st.session_state.meter_chat, facts, lang)
                except meter_assistant.AssistantError as e:
                    st.error(T(str(e)))
                else:
                    if reply["proposed"] is not None:
                        st.session_state.meter_pending = {"reading": reply["proposed"], "source": "chat"}
                        st.session_state.meter_nonce += 1
                    add_assistant_note(reply["text"] or T("Please check the reading below."))
                    st.rerun()

    # a reading from a photo or the chat is only a proposal until the user presses Confirm
    pending = st.session_state.get("meter_pending")
    if pending:
        with st.container(border=True):
            st.markdown(f"**{T('Is this reading right?')}**")
            today = now_local().date()
            f1, f2 = st.columns(2)
            value = f1.number_input(T("Reading (kWh)"), min_value=0.0, value=float(pending["reading"]), step=1.0, format="%.1f",
                                    key=f"meter_value_{st.session_state.meter_nonce}")
            on_date = f2.date_input(T("Date of the reading"), value=today, max_value=today,
                                    key=f"meter_date_{st.session_state.meter_nonce}")
            if meter_assistant.looks_too_high(readings, value, on_date):
                st.warning(T("This means more than {n} kWh a day. Please check the digits.", n=meter_assistant.MAX_DAY_KWH), icon="⚠️")
            b1, b2 = st.columns(2)
            if b1.button(T("Confirm and save"), type="primary", icon=":material/check:", width="stretch"):
                problem = meter_assistant.check_reading(readings, value, on_date, today)
                ok, msg = (False, problem) if problem else store.add_meter_reading(user, on_date.isoformat(), value, pending["source"])
                if ok:
                    del st.session_state.meter_pending
                    st.session_state.meter_nonce += 1
                    add_assistant_note(T("Saved: {r} kWh on {d}.", r=f"{value:,.1f}", d=on_date.isoformat()))
                    if readings and has_key:  # a second reading means there are new numbers to explain
                        st.session_state.meter_chat.append({"role": "user", "hidden": True, "content": (
                            "I saved a new meter reading. Explain my numbers briefly and give me my most useful tips.")})
                        st.session_state.meter_ask = True
                    st.rerun()
                st.error(T(msg))
            if b2.button(T("Cancel"), icon=":material/close:", width="stretch"):
                del st.session_state.meter_pending
                st.rerun()

    if question := st.chat_input(T("Ask about your electricity, or write your meter reading"), disabled=not has_key):
        st.session_state.meter_chat.append({"role": "user", "content": question})
        st.session_state.meter_ask = True
        st.rerun()


def _pick_area(name):
    st.session_state.home_area = name


def home_energy_row():
    """Today's use by hour and where this month's energy goes; picking a row lights that part of the house."""
    try:
        hourly, res = get_ml()
    except Exception:  # render_home shows the message
        return
    proj, _, _, series, offset = bill_summary(hourly, res)
    today = series.loc[proj["as_of"]: proj["as_of"] + pd.Timedelta(hours=23)].dropna()
    kwh_unit = T("kWh")
    left, right = st.columns([3, 2])
    with left, st.container(key="card_day"):
        st.subheader(T("Your day"))
        if len(today):
            st.metric(T("Last hour"), f"{today.iloc[-1]:.2f} {kwh_unit}")
            day = pd.DataFrame({"time": today.index, "kwh": today.values})
            st.altair_chart(
                alt.Chart(day).mark_bar(color=theme.SWATCH[st.session_state.theme_color], cornerRadiusEnd=3).encode(
                    x=alt.X("hours(time):O", title=None), y=alt.Y("kwh:Q", title=kwh_unit),
                    tooltip=[alt.Tooltip("time:T", format="%H:%M"), alt.Tooltip("kwh:Q", format=".2f")],
                ).properties(height=190),
                width="stretch",
            )
    apps = appliance_month_kwh(proj, offset)
    if apps:
        total = sum(apps.values())
        with right, st.container(key="card_where"):
            st.subheader(T("Where it goes"))
            st.caption(T("This month"))
            for i, (name, kwh) in enumerate(sorted(apps.items(), key=lambda kv: -kv[1])):
                chosen = st.session_state.get("home_area") == name
                st.button(f"{T(name)} · {kwh / total:.0%}", key=f"home_area_{i}", on_click=_pick_area, args=(name,),
                          type="primary" if chosen else "secondary", width="stretch")
                st.progress(kwh / total)


def render_home():
    hero, _ = st.columns([3, 2])
    hero.html(f'<h1 class="energy-hero">{T("Power you can")} <em>{T("finally see.")}</em></h1>')
    hero.caption(T(
        "Smart-meter electricity pipeline: Azure Data Lake → Spark → Airflow → Snowflake → this dashboard, "
        "with machine learning, alerts and live monitoring on top."
    ))
    st.space("large")
    home_energy_row()
    egp = T("EGP")

    with st.container(key="card_status"):
        s1, s2 = st.columns(2)
        s1.markdown(f"**{T('Data source')}**  \n{'⚠️' if source_name.startswith('DEMO') else '✅'} {source_name}")
        last_run = pipeline_last_run()
        if last_run is None:
            run_text = "⚪ " + T("status not available right now")
        else:
            run_day = pd.Timestamp(last_run[1])
            run_text = (("✅ " + T("last run succeeded on {day}", day=day_month(run_day))) if last_run[0] == "success"
                        else ("❌ " + T("last run failed on {day}", day=day_month(run_day))))
        s2.markdown(f"**{T('Weekly data pipeline')}**  \n{run_text}")

    st.subheader(T("The data"))
    dmin, dmax = fact_df["DateTime"].min(), fact_df["DateTime"].max()
    k1, k2, k3, k4 = st.columns(4)
    k1.metric(T("Total kWh"), f"{fact_df['KWH_hh'].sum():,.0f}")
    k2.metric(T("Households"), f"{fact_df['LCLid'].nunique():,}")
    k3.metric(T("Years"), f"{dmin:%Y}–{dmax:%Y}",
              help=T("{a} to {b}", a=f"{day_month(dmin)} {dmin.year}", b=f"{day_month(dmax)} {dmax.year}"))
    k4.metric(T("Readings"), f"{len(fact_df):,}", help=T("Half-hour readings"))

    st.subheader(T("Machine learning"))
    try:
        _, res = get_ml()
    except Exception as e:
        st.warning(T("Could not train the models for this selection: {e}", e=e))
    else:
        rmse = res["metrics"].set_index("model")["RMSE"]
        better = 100 * (1 - rmse[res["best_name"]] / rmse["Baseline (same hour yesterday)"])
        an = get_anomalies(res)
        alerts = build_alerts(an, household=household_label(), threshold_kwh=compute_threshold(res["train"]["kwh"]))
        m1, m2, m3, m4 = st.columns(4)
        m1.metric(T("Best model"), res["best_name"], T("{x}% vs baseline", x=f"{better:.0f}"),
                  help=T("Lower forecast error (RMSE)"))
        m2.metric("RMSE (kWh)", f"{rmse[res['best_name']]:.3f}", help=T("Forecast error on the unseen hours"))
        m3.metric(T("Anomalies"), f"{int(an['is_anomaly'].sum()):,}", help=T("In the unseen (test) hours"))
        m4.metric(T("Alerts"), f"{len(alerts):,}")
        st.caption(T("Household: {h}. Change it, and the sensitivity, on the ML pages.", h=household_label()))

        st.subheader(T("This month's bill"))
        proj, tiers, flat, series, offset = bill_summary(get_ml()[0], res)
        budget = float(st.session_state.bill_budget)
        b1, b2 = st.columns([1, 2])
        b1.metric(T("Expected for {month}", month=month_year(proj["month"])), f"{proj['projected_egp']:,.0f} {egp}",
                  T("{x} EGP vs budget", x=f"{proj['projected_egp'] - budget:+,.0f}") if budget else None,
                  delta_color="inverse")
        with b2:
            tips = saving_tips(series, proj, budget or None, appliance_month_kwh(proj, offset), tiers, flat,
                               st.session_state.lang)
            show_tips(sorted(tips, key=lambda t: t[0] != "warn")[:1])  # the most important tip only
        st.page_link(PAGES["Bill & Budget"], label=T("Open Bill & Budget for details and saving tips"),
                     icon=":material/payments:")

    st.subheader(T("Where to start"))
    q1, q2, q3, q4 = st.columns(4)
    q1.page_link(PAGES["Live"], label=T("Watch the live monitor"), icon=":material/sensors:")
    q2.page_link(PAGES["Forecast"], label=T("See the forecast"), icon=":material/trending_up:")
    q3.page_link(PAGES["Bill & Budget"], label=T("Check your bill"), icon=":material/payments:")
    q4.page_link(PAGES["About project"], label=T("How it works"), icon=":material/info:")

    with st.expander(T("How to use this dashboard")):
        st.markdown(
            T("HOW_TO") if is_ar() else
            "1. Pick a page from the tabs at the top.\n"
            "2. On the ML pages (Forecast, Anomalies, Alerts, Live), choose the household in the sidebar.\n"
            "3. On **Live**, press **Start** to replay the meter readings; **Spike** adds +3 kWh to show an alert on demand.\n"
            "4. Charts: drag to move, **Ctrl + mouse wheel** to zoom, **Reset zoom** to go back.\n"
            "5. Dark or Light and your color: at the top of every page."
        )


ARCHITECTURE_DOT = """
digraph {
  bgcolor="transparent"; nodesep=0.4; ranksep=0.5;
  node [shape=box, style="rounded,filled", fillcolor="#E3F2F4", color="#0E7C86", fontcolor="#1B2430",
        fontname="Helvetica", fontsize=14, margin="0.2,0.1"];
  edge [color="#7A8794", fontcolor="#7A8794", fontname="Helvetica", fontsize=11];
  { rank=same; csv; raw; spark; curated; }
  { rank=same; uci; app; snow; airflow; }
  csv     [label="London LCL\\nCSV files"];
  raw     [label="Data Lake\\nraw"];
  spark   [label="Spark\\ncleaning"];
  curated [label="Data Lake\\ncurated"];
  airflow [label="Airflow / ADF\\n(weekly pipeline)"];
  snow    [label="Snowflake\\nFACT + DIM"];
  app     [label="Streamlit\\nML · alerts · live"];
  uci     [label="UCI file\\n(appliances)"];
  csv -> raw -> spark -> curated -> airflow;
  app -> snow -> airflow [dir=back];
  uci -> app [style=dashed];
}
"""


def render_about():
    st.subheader("Architecture")
    st.graphviz_chart(ARCHITECTURE_DOT, width="stretch")

    st.subheader("Datasets")
    st.table(
        pd.DataFrame(
            [
                {
                    "Dataset": "London LCL (LCL-June2015v2)",
                    "What it has": "Whole-house kWh per half hour, thousands of households",
                    "Used for": "Azure / Spark / Snowflake pipeline, forecasting, anomalies, alerts",
                },
                {
                    "Dataset": "UCI Individual Household Electric Power Consumption",
                    "What it has": "One house, 1-minute readings + 3 sub-meters (kitchen, laundry, heater & AC)",
                    "Used for": "Appliance-level analysis (the only dataset with appliance data)",
                },
            ]
        ).set_index("Dataset")
    )

    st.subheader("Features promised in the report")
    st.table(
        pd.DataFrame(
            [
                ("Random Forest + XGBoost forecasting", "✅ Done", "Forecast"),
                ("ML anomaly detection (z-score + Isolation Forest)", "✅ Done", "Anomalies"),
                ("Appliance-level analysis", "✅ Done (UCI data)", "Appliances"),
                ("Consumption alerts", "✅ Done (email not tested yet)", "Alerts"),
                ("Real-time monitoring", "✅ Simulated stream (replay)", "Live"),
                ("Azure Data Factory", "📄 Template ready (needs an Azure subscription)", "adf/"),
                ("Weekly data pipeline raw → curated", "✅ Runs free on GitHub Actions", "pipeline.py"),
                ("Pipeline monitoring", "✅ Needs Airflow running", "Pipeline monitor"),
                ("Login with roles, light / dark theme", "✅ Done", "Sidebar"),
                ("Bill in EGP, budget alerts, saving assistant", "✅ Done", "Bill & Budget"),
                ("Daily / weekly / monthly reports by Gmail, WhatsApp, SMS", "✅ Done (sending needs keys)", "Reports"),
                ("LLM chatbot: meter reading by photo or text, advice", "✅ Done (chat needs a key)", "Meter assistant"),
            ],
            columns=["Feature", "Status", "Page"],
        ).set_index("Feature")
    )
    st.caption(
        "Tech stack: Azure Data Lake Storage, Apache Spark, Apache Airflow, Snowflake, Python "
        "(pandas, scikit-learn, XGBoost), Streamlit, Altair."
    )


# ---- Menu ---------------------------------------------------------------------
PAGES = {
    "Home": st.Page(render_home, title=T("Home"), icon=":material/home:", url_path="home", default=True),
    "Bill & Budget": st.Page(render_bill, title=T("Bill & Budget"), icon=":material/payments:", url_path="bill"),
    "Reports": st.Page(render_reports, title=T("Reports"), icon=":material/forward_to_inbox:", url_path="reports"),
    "Meter assistant": st.Page(render_assistant, title=T("Meter assistant"), icon=":material/smart_toy:", url_path="assistant"),
    "Analytics": st.Page(render_analytics, title=T("Analytics"), icon=":material/bar_chart:", url_path="analytics"),
    "Appliances": st.Page(
        render_appliances, title=T("Appliances"), icon=":material/electrical_services:", url_path="appliances"
    ),
    "Forecast": st.Page(render_forecast, title=T("Forecast"), icon=":material/trending_up:", url_path="forecast"),
    "Anomalies": st.Page(render_anomalies, title=T("Anomalies"), icon=":material/warning:", url_path="anomalies"),
    "Alerts": st.Page(render_alerts, title=T("Alerts"), icon=":material/notifications:", url_path="alerts"),
    "Live": st.Page(render_live, title=T("Live"), icon=":material/sensors:", url_path="live"),
    "Sensor": st.Page(render_sensor, title=T("Sensor"), icon=":material/memory:", url_path="sensor"),
    "Pipeline monitor": st.Page(
        render_monitor, title=T("Pipeline monitor"), icon=":material/account_tree:", url_path="pipeline"
    ),
    "About project": st.Page(render_about, title=T("About project"), icon=":material/info:", url_path="about"),
}
monitoring = [PAGES["Sensor"], PAGES["Live"]] +([PAGES["Pipeline monitor"]] if auth.is_admin() else [])
pg = st.navigation(
    {
        "": [PAGES["Home"], PAGES["Bill & Budget"], PAGES["Meter assistant"], PAGES["Reports"]],
        T("Insights"): [PAGES["Forecast"], PAGES["Appliances"], PAGES["Analytics"], PAGES["Anomalies"], PAGES["Alerts"],
                        *monitoring, PAGES["About project"]],
    },
    position="top",
)
page_key = pg.url_path or "home"  # the default page's url_path is ""; titles change with the language

PAGE_INTRO = {
    "bill": "Your bill in Egyptian pounds, the expected bill at month end, budget alerts and saving tips.",
    "reports": "Your energy report by Gmail, WhatsApp or SMS: today, tomorrow's forecast, the bill and tips.",
    "assistant": "Send a photo of your meter or type the reading, then ask how to lower your bill.",
    "analytics": "Explore consumption by household, date and day type.",
    "appliances": "Where the energy goes inside the house (UCI sub-meters).",
    "forecast": "Random Forest vs XGBoost vs a 'same hour yesterday' baseline, and the next 24 hours.",
    "anomalies": "Hours the model did not expect, found on data it never trained on.",
    "alerts": "Hours above the consumption limit, and anomaly alerts.",
    "live": "Simulated real-time monitoring.",
    "sensor": "Real readings from the ESP32 + ACS712 current sensor, updated every 5 seconds.",
    "pipeline": "Live status of the Airflow pipeline, from the Airflow REST API.",
    "about": "How the project works, the data it uses and what is done.",
}

# ---- Sidebar: ML settings (only where they matter), then the account box ------
if data_error is None and page_key in ("forecast", "anomalies", "alerts", "live"):
    ml_sidebar(with_detectors=page_key in ("anomalies", "alerts"))

with st.sidebar:
    st.divider()
    st.caption(f":material/database: {T('Data: {source}', source=source_name)}")
    if auth.login_enabled():
        user = auth.current_user()
        st.markdown(f":material/person: **{user['name']}** · {T(user['role'])}")
        if st.button(T("Log out"), icon=":material/logout:", width="stretch"):
            auth.logout()
            st.rerun()
    else:
        st.warning("Login is off: add [auth] users to secrets.toml (see secrets.toml.example).", icon="🔓")

# ---- The look: the page's moving background, then Dark / Light, color and language --
theme.apply(page_key, st.session_state.theme_mode, st.session_state.theme_color,
            theme.WINDOWS.get(st.session_state.get("home_area")), is_ar())
with st.container(key="energy_controls", horizontal=True, horizontal_alignment="right"):
    st.radio(T("Dark or light mode"), theme.MODES, key="theme_mode", horizontal=True, format_func=T,
             label_visibility="collapsed", width="content")
    st.radio(T("Color"), theme.COLORS, key="theme_color", horizontal=True, format_func=T,
             label_visibility="collapsed", width="content")
    language_switch()

# ---- The open page ------------------------------------------------------------
if is_ar():  # right-to-left and an Arabic font; pages not translated yet keep the English layout
    st.markdown(RTL_CSS if page_key in TRANSLATED_PAGES else
                '<style>[data-testid="stSidebarContent"] { direction: rtl; }</style>', unsafe_allow_html=True)
if source_name.startswith("DEMO"):
    st.warning(
        "DEMO MODE: showing FAKE data because the app could not connect to Snowflake "
        "and there are no data files in the 'data' folder. "
        f"Reason: {str(snowflake_error)[:250]}"
    )
if page_key != "home":
    st.title(f"{pg.icon} {pg.title}")
    st.caption(T(PAGE_INTRO.get(page_key, "")))
    if is_ar() and page_key not in TRANSLATED_PAGES:
        st.info(T("This page is in English for now."), icon="ℹ️")

if data_error is not None and page_key not in ("pipeline", "about"):
    st.error(f"Could not load the data: {data_error}")
else:
    pg.run()
