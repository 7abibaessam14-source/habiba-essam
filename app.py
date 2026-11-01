"""
Smart Energy Monitor - Streamlit app (v3)

Pages (sidebar menu, only the open page runs)
  Home              project summary: data source, key numbers, quick links
  Bill & Budget     bill in EGP, month-end projection, budget alerts, saving tips
  Reports           daily / weekly / monthly report by Gmail, WhatsApp or SMS (preview, send now, save)
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
from billing import CO2_KG_PER_KWH, CO2_KG_PER_TREE_YEAR, EGYPT_TIERS, bill_egp, co2_kg, month_projection, saving_tips
from live import LiveReplay
from ml_models import (FEATURES, add_features, detect_anomalies, feature_importance, forecast_next, to_hourly,
                       train_and_compare)
import notify
from reports import CHANNELS, FREQUENCIES, as_email, as_sms, as_whatsapp, build_report, now_local

APP_NAME = "Smart Energy Monitor"
st.set_page_config(page_title=APP_NAME, page_icon="⚡", layout="wide")

ALL_LABEL = "All households (average)"
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


@st.cache_data(show_spinner="Reading the data files (first time only)...")
def local_frames(signature):
    # signature is only a cache key: it changes when the files in ./data change
    uci = uci_path(DATA_DIR)  # the .txt on your PC, or the .zip copy on the online version
    if uci is not None:
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
st.logo(str(ASSETS / "logo.svg"), icon_image=str(ASSETS / "icon.svg"), size="large")
PAGE_URLS = ["home", "bill", "reports", "analytics", "appliances", "forecast", "anomalies", "alerts", "live", "pipeline", "about"]
if auth.current_user() is None:
    # Every page address shows the login form, in a hidden menu: this replaces the app menu (which
    # Streamlit would otherwise keep showing) and a link like /live opens that page after logging in.
    def _login():
        auth.login_page(f"⚡ {APP_NAME}", "Smart-meter electricity pipeline - graduation project")

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
BILL_DEFAULTS = {"bill_mode": "Egyptian tiers (Aug 2026)", "bill_price": 2.0, "bill_budget": 500.0}
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


def bill_summary(hourly, res):
    """Month-end projection for the 'as of' day: the model forecasts the next 24 hours, typical days the rest."""
    tiers, flat = bill_tariff()
    as_of = pd.Timestamp(st.session_state.bill_asof)
    try:
        nxt = float(forecast_next(res["best_model"], hourly.loc[: as_of + pd.Timedelta(hours=23)], steps=24).sum())
    except Exception:  # too little history before that day
        nxt = None
    return month_projection(hourly, as_of, nxt, tiers, flat), tiers, flat


def appliance_month_kwh(proj):
    """{appliance: kWh} measured this month up to the 'as of' day, when the data has sub-meters (UCI)."""
    cols = list(APPLIANCE_COLUMNS)
    if not set(cols) <= set(fact_df.columns):
        return None
    d = fact_df[(fact_df["DateTime"] >= proj["month"]) & (fact_df["DateTime"] < proj["as_of"] + pd.Timedelta(days=1))]
    if ml_choice != ALL_LABEL:
        d = d[d["LCLid"] == ml_choice]
    s = d[cols].sum()
    return {APPLIANCE_COLUMNS[c]: float(s[c]) for c in cols} if s.sum() > 0 else None


def show_tips(tips):
    for kind, text in tips:
        {"warn": st.warning, "good": st.success}.get(kind, st.info)(text, icon={"warn": "⚠️", "good": "✅"}.get(kind, "💡"))


# ---- Reports settings: the user's saved choice first, else the defaults -------------
_saved_report = notify.load_subscriptions().get(auth.current_user()["name"], {})
REPORT_DEFAULTS = {
    "rep_freq": _saved_report.get("frequency", "Daily"),
    "rep_channels": _saved_report.get("channels", ["Gmail"]),
    "rep_email": _saved_report.get("email", auth.current_user().get("email", "")),
    "rep_phone": _saved_report.get("phone", ""),
    "rep_wa_key": _saved_report.get("whatsapp_apikey", ""),
}
for _k, _v in REPORT_DEFAULTS.items():
    st.session_state.setdefault(_k, _v)
    st.session_state[_k] = st.session_state[_k]
MAX_SENDS_PER_SESSION = 3  # "Send me one now" uses the app's Gmail / Twilio account, so keep it small


@st.cache_data(max_entries=30, show_spinner="Writing the report...")
def report_for(household_key, best_name, frequency, hour, tiers, flat, budget, _hourly, _res, _fact):
    # cached per household, frequency, tariff and hour: the report changes when a new hour of readings arrives
    return build_report(_hourly, _res["best_model"], frequency, now=hour, tiers=tiers, flat_price=flat,
                        budget_egp=budget, fact=_fact)


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


def render_bill():
    try:
        hourly, res = get_ml()
    except Exception as e:
        st.warning(f"Could not train the models for this selection: {e}")
        return
    dmin, dmax = hourly.dropna().index.min().date(), hourly.dropna().index.max().date()

    c1, c2, c3 = st.columns([1.4, 1, 1])
    c1.radio("Tariff", ["Egyptian tiers (Aug 2026)", "Fixed price per kWh"], key="bill_mode", horizontal=True)
    c2.number_input("Monthly budget (EGP)", min_value=0.0, step=50.0, key="bill_budget")
    c3.date_input("Month of (as of this day)", min_value=dmin, max_value=dmax, key="bill_asof",
                  help="The data is historical, so pick the day that counts as 'today'.")
    if st.session_state.bill_mode.startswith("Fixed"):
        st.number_input("Price (EGP per kWh)", min_value=0.05, step=0.05, key="bill_price")
    else:
        with st.expander("Tariff tiers (EGP per kWh) - edit them if the government changes the prices"):
            for box, (i, label) in zip(st.columns(len(TIER_LABELS)), enumerate(TIER_LABELS)):
                box.number_input(f"{label} kWh", min_value=0.0, step=0.01, format="%.2f", key=f"tier_{i}")

    proj, tiers, flat = bill_summary(hourly, res)
    budget = float(st.session_state.bill_budget)

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("This month so far", f"{proj['used_egp']:,.0f} EGP", f"{proj['used_kwh']:,.0f} kWh", delta_color="off")
    m2.metric("Expected at month end", f"{proj['projected_egp']:,.0f} EGP",
              f"{proj['low_egp']:,.0f}–{proj['high_egp']:,.0f} EGP", delta_color="off",
              help="The range shows how much your daily use varied in the last 28 days.")
    if proj["prev_egp"]:
        m3.metric("Last month", f"{proj['prev_egp']:,.0f} EGP",
                  f"{(proj['projected_egp'] - proj['prev_egp']) / proj['prev_egp']:+.0%} this month", delta_color="inverse")
    else:
        m3.metric("Last month", "—", help="Not enough data for last month.")
    if budget > 0:
        left = budget - proj["projected_egp"]
        m4.metric("Budget", f"{budget:,.0f} EGP", f"{left:,.0f} EGP left" if left >= 0 else f"{-left:,.0f} EGP over",
                  delta_color="normal" if left >= 0 else "inverse")
        if left < 0:
            st.error(f"Budget alert: the expected bill ({proj['projected_egp']:,.0f} EGP) is over your budget "
                     f"of {budget:,.0f} EGP.", icon="🚨")
        else:
            st.success(f"On track: the expected bill is {left:,.0f} EGP under your budget.", icon="✅")
    if proj["history_days"] < 28:
        st.info(f"Experimental forecast: only {proj['history_days']} days of history before this day, "
                "so the month-end value is less certain.", icon="🧪")
    st.caption(
        f"{proj['month']:%B %Y}, as of {proj['as_of']:%d %b}: {proj['remaining_days']} days left. Estimate only: each "
        "kWh is priced at its tier's price; real bills can add fees. Tomorrow uses the model's 24-hour forecast, "
        "the other days your average day."
    )
    month_co2 = co2_kg(proj["projected_kwh"])
    st.markdown(f"🌍 **Carbon footprint:** about **{month_co2:,.0f} kg CO₂** this month "
                f"({co2_kg(proj['used_kwh']):,.0f} kg so far). Absorbing it would take about "
                f"**{month_co2 * 12 / CO2_KG_PER_TREE_YEAR:,.0f} trees** a year.",
                help=f"Estimate: {CO2_KG_PER_KWH} kg CO₂ per kWh for Egypt's mostly natural-gas power, "
                     f"one tree absorbs about {CO2_KG_PER_TREE_YEAR} kg CO₂ a year.")

    # cumulative bill through the month: actual days, then expected days, against the budget
    daily = proj["daily"].fillna(0)
    rows, total = [], 0.0
    for day, kwh in daily.items():
        total += kwh
        rows.append((day, bill_egp(total, tiers, flat), "actual"))
    rows_exp = [(proj["as_of"], bill_egp(total, tiers, flat), "expected")]
    for k in range(1, proj["remaining_days"] + 1):
        total += (proj["projected_kwh"] - proj["used_kwh"]) / proj["remaining_days"]
        rows_exp.append((proj["as_of"] + pd.Timedelta(days=k), bill_egp(total, tiers, flat), "expected"))
    cum = pd.DataFrame(rows + rows_exp, columns=["day", "egp", "type"])
    st.subheader("Bill through the month")
    chart = alt.Chart(cum).mark_line(point=True).encode(
        x=alt.X("day:T", title=None),
        y=alt.Y("egp:Q", title="EGP"),
        color=alt.Color("type:N", scale=alt.Scale(domain=["actual", "expected"], range=ACTUAL_VS_MODEL)),
        strokeDash=alt.StrokeDash("type:N", scale=alt.Scale(domain=["actual", "expected"], range=[[1, 0], [5, 3]])),
        tooltip=["day:T", alt.Tooltip("egp:Q", format=",.0f")],
    )
    if budget > 0:
        chart = chart + alt.Chart(pd.DataFrame({"budget": [budget]})).mark_rule(color="red", strokeDash=[4, 3]).encode(y="budget:Q")
    st.altair_chart(chart, width="stretch")
    st.caption("Red dashed line = your budget.")

    st.subheader("Last 12 months")
    months = hourly.loc[: proj["as_of"] + pd.Timedelta(hours=23)].resample("MS").sum(min_count=24 * 20).dropna().tail(12)
    hist = pd.DataFrame({"month": months.index, "kwh": months.values})
    hist["egp"] = [bill_egp(k, tiers, flat) for k in hist["kwh"]]
    st.altair_chart(
        alt.Chart(hist).mark_bar().encode(
            x=alt.X("month:T", title=None, timeUnit="yearmonth"), y=alt.Y("egp:Q", title="EGP"),
            tooltip=[alt.Tooltip("month:T", timeUnit="yearmonth"), alt.Tooltip("kwh:Q", format=",.0f"),
                     alt.Tooltip("egp:Q", format=",.0f")],
        ),
        width="stretch",
    )

    st.subheader("Where is my electricity going?")
    measured = appliance_month_kwh(proj)
    if measured:
        tot = sum(measured.values())
        st.dataframe(
            pd.DataFrame([(n, k, k / tot, proj["used_egp"] * k / tot) for n, k in measured.items()],
                         columns=["Appliance (measured)", "kWh this month", "Share", "Cost (EGP)"]),
            hide_index=True, width="stretch",
            column_config={"kWh this month": st.column_config.NumberColumn(format="%.0f"),
                           "Share": st.column_config.ProgressColumn(format="percent", min_value=0, max_value=1),
                           "Cost (EGP)": st.column_config.NumberColumn(format="%.0f")},
        )
        st.caption("Measured by the house's three sub-meters; 'Other' is everything they do not cover.")
    st.markdown("**Estimate for your own devices** - tick what you have and set the power and hours "
                "(a single meter cannot measure each device, so these are estimates):")
    est = st.data_editor(APPLIANCE_ESTIMATE_DEFAULTS, hide_index=True, width="stretch", key="appliance_editor",
                         column_config={"Power (W)": st.column_config.NumberColumn(min_value=0, step=10),
                                        "Hours per day": st.column_config.NumberColumn(min_value=0.0, max_value=24.0, step=0.5)})
    est = est[est["Have it"]].copy()
    if len(est):
        est["kWh per month"] = est["Power (W)"] * est["Hours per day"] * proj["days_in_month"] / 1000
        est_total = est["kWh per month"].sum()
        est["Cost (EGP)"] = est["kWh per month"] / est_total * bill_egp(est_total, tiers, flat) if est_total else 0.0
        st.dataframe(est[["Device", "kWh per month", "Cost (EGP)"]].sort_values("kWh per month", ascending=False),
                     hide_index=True, width="stretch",
                     column_config={"kWh per month": st.column_config.NumberColumn(format="%.0f"),
                                    "Cost (EGP)": st.column_config.NumberColumn(format="%.0f")})
        st.caption(f"Your devices add up to about {est_total:,.0f} kWh a month; the meter expects "
                   f"{proj['projected_kwh']:,.0f} kWh for this month.")

    st.subheader("Energy-saving assistant")
    show_tips(saving_tips(hourly, proj, budget or None, measured, tiers, flat))
    st.caption("Tips are worked out from your own consumption, forecast and tariff.")


CALLMEBOT_HELP = (
    "Free WhatsApp messages via CallMeBot. Once only: save +34 644 71 81 99 in your contacts and send it the "
    "WhatsApp message 'I allow callmebot to send me messages'. The bot replies with your key; paste it here."
)


def render_reports():
    try:
        hourly, res = get_ml()
    except Exception as e:
        st.warning(f"Could not train the models for this selection: {e}")
        return
    tiers, flat = bill_tariff()
    budget = float(st.session_state.bill_budget) or None

    c1, c2 = st.columns([1, 1.4])
    c1.radio("How often", FREQUENCIES, key="rep_freq", horizontal=True,
             help="Daily: every evening at about 8 PM (Cairo time). Weekly: Saturdays. Monthly: the 1st of the month.")
    c2.multiselect("Send it by", CHANNELS, key="rep_channels")
    channels = st.session_state.rep_channels
    a, b, c = st.columns(3)
    a.text_input("Gmail address", key="rep_email", disabled="Gmail" not in channels, placeholder="you@gmail.com")
    b.text_input("Phone (with country code)", key="rep_phone", placeholder="+201001234567",
                 disabled=not {"WhatsApp", "SMS"} & set(channels))
    c.text_input("WhatsApp key (CallMeBot)", key="rep_wa_key", type="password", disabled="WhatsApp" not in channels,
                 help=CALLMEBOT_HELP)

    now = now_local().floor("h")
    fact = fact_df if ml_choice == ALL_LABEL else fact_df[fact_df["LCLid"] == ml_choice]
    try:
        r = report_for(ml_choice, res["best_name"], st.session_state.rep_freq, now, tiers, flat, budget, hourly, res, fact)
    except Exception as e:
        st.warning(f"Could not build the report: {e}")
        return
    proj = r["proj"]

    m1, m2, m3 = st.columns(3)
    m1.metric("Today (expected total)", f"{r['today_kwh']:.1f} kWh", f"≈ {r['today_egp']:,.0f} EGP", delta_color="off")
    m2.metric("Tomorrow (forecast)", f"{r['tomorrow_kwh']:.1f} kWh", f"{r['change']:+.0%} vs today ({r['trend']})",
              delta_color="inverse", help="Forecast by the trained model for tomorrow, 00:00 to 24:00.")
    m3.metric(f"{proj['month']:%B} bill (expected)", f"{proj['projected_egp']:,.0f} EGP",
              f"{proj['projected_egp'] - budget:+,.0f} EGP vs budget" if budget else None, delta_color="inverse",
              help="Budget and tariff come from the Bill & Budget page.")
    st.caption(f"{r['note']} Readings until {now:%H:%M} today ({now:%d %b %Y}); the model ({res['best_name']}) "
               "forecasts the rest of today and tomorrow.")

    texts = {"sms": as_sms(r), "whatsapp": as_whatsapp(r), "email": as_email(r)}
    st.subheader("Preview")
    t_wa, t_sms, t_mail = st.tabs(["WhatsApp", "SMS", "Gmail"])
    with t_wa:
        st.code(texts["whatsapp"], language=None, wrap_lines=True)
    with t_sms:
        st.code(texts["sms"], language=None, wrap_lines=True)
        st.caption(f"{len(texts['sms'])} characters ≈ {math.ceil(len(texts['sms']) / 153)} SMS.")
    with t_mail:
        st.markdown(f"**Subject:** {texts['email'][0]}")
        with st.container(border=True):
            st.html(texts["email"][2])

    sub = {"frequency": st.session_state.rep_freq, "channels": channels, "email": st.session_state.rep_email.strip(),
           "phone": st.session_state.rep_phone.strip(), "whatsapp_apikey": st.session_state.rep_wa_key.strip(),
           "budget": budget}
    b1, b2 = st.columns(2)
    send = b1.button("Send me one now", type="primary", icon=":material/send:", width="stretch", disabled=not channels)
    save = b2.button("Save my choice", icon=":material/save:", width="stretch", disabled=not channels)
    if send:
        if st.session_state.get("reports_sent", 0) >= MAX_SENDS_PER_SESSION:
            st.error(f"You can send {MAX_SENDS_PER_SESSION} test reports per visit. Log in again to send more.")
        else:
            st.session_state.reports_sent = st.session_state.get("reports_sent", 0) + 1
            with st.spinner("Sending..."):
                results = notify.send_report(sub, texts, *report_senders())
            for ok, msg in results:
                (st.success if ok else st.error)(msg, icon="✅" if ok else "❌")
    if save:
        notify.save_subscription(auth.current_user()["name"], sub)
        st.success(f"Saved: {sub['frequency'].lower()} report by {', '.join(channels)}.", icon="✅")
    st.info("Automatic sending: GitHub Actions sends the reports every evening at about 8 PM (Cairo time). On this "
            "free version the admin adds saved choices to the schedule list. Saved choices may be reset when the "
            "online server restarts.", icon="🗓️")

    if auth.is_admin():
        with st.expander("Schedule list (admin only: it has phone numbers and keys)"):
            subs = notify.load_subscriptions()
            if subs:
                st.code(json.dumps(subs, indent=1), language="json")
                st.caption("Copy all of it into the GitHub secret REPORT_SUBSCRIBERS (repo → Settings → Secrets and "
                           "variables → Actions); GitHub then sends these reports on schedule.")
            else:
                st.caption("Nobody has saved a choice yet.")


def render_home():
    st.title(f"⚡ {APP_NAME}")
    st.caption(
        "Smart-meter electricity pipeline: Azure Data Lake → Spark → Airflow → Snowflake → this dashboard, "
        "with machine learning, alerts and live monitoring on top."
    )

    with st.container(border=True):
        s1, s2, s3 = st.columns(3)
        s1.markdown(f"**Data source**  \n{'⚠️' if source_name.startswith('DEMO') else '✅'} {source_name}")
        s2.markdown(f"**Snowflake**  \n{'✅ connected' if source_name == 'Snowflake' else '❌ not connected'}")
        s3.markdown(f"**Airflow pipeline**  \n{airflow_status(*airflow_config())}")

    st.subheader("The data")
    dmin, dmax = fact_df["DateTime"].min(), fact_df["DateTime"].max()
    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Total kWh", f"{fact_df['KWH_hh'].sum():,.0f}")
    k2.metric("Households", f"{fact_df['LCLid'].nunique():,}")
    k3.metric("Years", f"{dmin:%Y}–{dmax:%Y}", help=f"{dmin:%d %b %Y} to {dmax:%d %b %Y}")
    k4.metric("Readings", f"{len(fact_df):,}", help="Half-hour readings")

    st.subheader("Machine learning")
    try:
        _, res = get_ml()
    except Exception as e:
        st.warning(f"Could not train the models for this selection: {e}")
    else:
        rmse = res["metrics"].set_index("model")["RMSE"]
        better = 100 * (1 - rmse[res["best_name"]] / rmse["Baseline (same hour yesterday)"])
        an = get_anomalies(res)
        alerts = build_alerts(an, household=household_label(), threshold_kwh=compute_threshold(res["train"]["kwh"]))
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Best model", res["best_name"], f"{better:.0f}% vs baseline", help="Lower forecast error (RMSE)")
        m2.metric("RMSE (kWh)", f"{rmse[res['best_name']]:.3f}", help="Forecast error on the unseen hours")
        m3.metric("Anomalies", f"{int(an['is_anomaly'].sum()):,}", help="In the unseen (test) hours")
        m4.metric("Alerts", f"{len(alerts):,}")
        st.caption(f"Household: {household_label()}. Change it, and the sensitivity, on the ML pages.")

        st.subheader("This month's bill")
        proj, tiers, flat = bill_summary(get_ml()[0], res)
        budget = float(st.session_state.bill_budget)
        b1, b2 = st.columns([1, 2])
        b1.metric(f"Expected for {proj['month']:%B %Y}", f"{proj['projected_egp']:,.0f} EGP",
                  f"{proj['projected_egp'] - budget:+,.0f} EGP vs budget" if budget else None, delta_color="inverse")
        with b2:
            tips = saving_tips(get_ml()[0], proj, budget or None, appliance_month_kwh(proj), tiers, flat)
            show_tips(sorted(tips, key=lambda t: t[0] != "warn")[:1])  # the most important tip only
        st.page_link(PAGES["Bill & Budget"], label="Open Bill & Budget for details and saving tips", icon=":material/payments:")

    st.subheader("Where to start")
    q1, q2, q3, q4 = st.columns(4)
    q1.page_link(PAGES["Live"], label="Watch the live monitor", icon=":material/sensors:")
    q2.page_link(PAGES["Forecast"], label="See the forecast", icon=":material/trending_up:")
    q3.page_link(PAGES["Bill & Budget"], label="Check your bill", icon=":material/payments:")
    q4.page_link(PAGES["About project"], label="How it works", icon=":material/info:")

    with st.expander("How to use this dashboard"):
        st.markdown(
            "1. Pick a page from the menu on the left.\n"
            "2. On the ML pages (Forecast, Anomalies, Alerts, Live), choose the household in the sidebar.\n"
            "3. On **Live**, press **Start** to replay the meter readings; **Spike** adds +3 kWh to show an alert on demand.\n"
            "4. Charts: drag to move, **Ctrl + mouse wheel** to zoom, **Reset zoom** to go back.\n"
            "5. Light / dark mode: the **⋮** menu (top right) → **Light** or **Dark**."
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
  airflow [label="Airflow\\n(ADF: in progress)"];
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
                ("Azure Data Factory", "🔄 In progress", "-"),
                ("Pipeline monitoring", "✅ Needs Airflow running", "Pipeline monitor"),
                ("Login with roles, light / dark theme", "✅ Done", "Sidebar"),
                ("Bill in EGP, budget alerts, saving assistant", "✅ Done", "Bill & Budget"),
                ("Daily / weekly / monthly reports by Gmail, WhatsApp, SMS", "✅ Done (sending needs keys)", "Reports"),
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
    "Home": st.Page(render_home, title="Home", icon=":material/home:", url_path="home", default=True),
    "Bill & Budget": st.Page(render_bill, title="Bill & Budget", icon=":material/payments:", url_path="bill"),
    "Reports": st.Page(render_reports, title="Reports", icon=":material/forward_to_inbox:", url_path="reports"),
    "Analytics": st.Page(render_analytics, title="Analytics", icon=":material/bar_chart:", url_path="analytics"),
    "Appliances": st.Page(
        render_appliances, title="Appliances", icon=":material/electrical_services:", url_path="appliances"
    ),
    "Forecast": st.Page(render_forecast, title="Forecast", icon=":material/trending_up:", url_path="forecast"),
    "Anomalies": st.Page(render_anomalies, title="Anomalies", icon=":material/warning:", url_path="anomalies"),
    "Alerts": st.Page(render_alerts, title="Alerts", icon=":material/notifications:", url_path="alerts"),
    "Live": st.Page(render_live, title="Live", icon=":material/sensors:", url_path="live"),
    "Pipeline monitor": st.Page(
        render_monitor, title="Pipeline monitor", icon=":material/account_tree:", url_path="pipeline"
    ),
    "About project": st.Page(render_about, title="About project", icon=":material/info:", url_path="about"),
}
monitoring = [PAGES["Live"]] + ([PAGES["Pipeline monitor"]] if auth.is_admin() else [])
pg = st.navigation(
    {
        "Overview": [PAGES["Home"], PAGES["Bill & Budget"], PAGES["Reports"]],
        "Analysis": [PAGES["Analytics"], PAGES["Appliances"]],
        "Machine learning": [PAGES["Forecast"], PAGES["Anomalies"], PAGES["Alerts"]],
        "Monitoring": monitoring,
        "Project": [PAGES["About project"]],
    }
)

PAGE_INTRO = {
    "Bill & Budget": "Your bill in Egyptian pounds, the expected bill at month end, budget alerts and saving tips.",
    "Reports": "Your energy report by Gmail, WhatsApp or SMS: today, tomorrow's forecast, the bill and tips.",
    "Analytics": "Explore consumption by household, date and day type.",
    "Appliances": "Where the energy goes inside the house (UCI sub-meters).",
    "Forecast": "Random Forest vs XGBoost vs a 'same hour yesterday' baseline, and the next 24 hours.",
    "Anomalies": "Hours the model did not expect, found on data it never trained on.",
    "Alerts": "Hours above the consumption limit, and anomaly alerts.",
    "Live": "Simulated real-time monitoring.",
    "Pipeline monitor": "Live status of the Airflow pipeline, from the Airflow REST API.",
    "About project": "How the project works, the data it uses and what is done.",
}

# ---- Sidebar: ML settings (only where they matter), then the account box ------
if data_error is None and pg.title in ("Forecast", "Anomalies", "Alerts", "Live"):
    ml_sidebar(with_detectors=pg.title in ("Anomalies", "Alerts"))

with st.sidebar:
    st.divider()
    st.caption(f":material/database: Data: {source_name}")
    if auth.login_enabled():
        user = auth.current_user()
        st.markdown(f":material/person: **{user['name']}** · {user['role']}")
        if st.button("Log out", icon=":material/logout:", width="stretch"):
            auth.logout()
            st.rerun()
    else:
        st.warning("Login is off: add [auth] users to secrets.toml (see secrets.toml.example).", icon="🔓")
    st.caption("Light / dark mode: ⋮ menu (top right) → Light or Dark.")

# ---- The open page ------------------------------------------------------------
if source_name.startswith("DEMO"):
    st.warning(
        "DEMO MODE: showing FAKE data because the app could not connect to Snowflake "
        "and there are no data files in the 'data' folder. "
        f"Reason: {str(snowflake_error)[:250]}"
    )
if pg.title != "Home":
    st.title(f"{pg.icon} {pg.title}")
    st.caption(PAGE_INTRO.get(pg.title, ""))

if data_error is not None and pg.title not in ("Pipeline monitor", "About project"):
    st.error(f"Could not load the data: {data_error}")
else:
    pg.run()
