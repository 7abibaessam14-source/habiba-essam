"""
alerts.py - turns model output into alerts, and optionally emails them.

Two alert types:
  THRESHOLD  hourly consumption above a limit (default: 99th percentile of history)
  ANOMALY    hour flagged by ml_models.detect_anomalies()
"""

import smtplib
from email.message import EmailMessage

import numpy as np
import pandas as pd

ALERT_COLUMNS = ["timestamp", "household", "type", "severity", "kwh", "expected", "message"]


def compute_threshold(kwh_history, percentile=99):
    """Default limit: the 99th percentile of past hourly consumption."""
    return float(np.percentile(pd.Series(kwh_history).dropna(), percentile))


def build_alerts(df_anom, household="ALL", threshold_kwh=None):
    """
    df_anom: output of detect_anomalies() (needs kwh, pred, z, is_anomaly).
    threshold_kwh: pass a number to also raise THRESHOLD alerts.
    Returns a DataFrame with ALERT_COLUMNS, newest first (empty if nothing fired).
    """
    rows = []

    if threshold_kwh is not None:
        for ts, r in df_anom[df_anom["kwh"] > threshold_kwh].iterrows():
            rows.append(
                {
                    "timestamp": ts,
                    "household": household,
                    "type": "THRESHOLD",
                    "severity": "HIGH" if r["kwh"] > 1.5 * threshold_kwh else "MEDIUM",
                    "kwh": round(float(r["kwh"]), 3),
                    "expected": round(float(r["pred"]), 3),
                    "message": f"{r['kwh']:.2f} kWh exceeded the {threshold_kwh:.2f} kWh limit",
                }
            )

    for ts, r in df_anom[df_anom["is_anomaly"]].iterrows():
        direction = "above" if r["residual"] > 0 else "below"
        rows.append(
            {
                "timestamp": ts,
                "household": household,
                "type": "ANOMALY",
                "severity": "HIGH" if abs(r["z"]) > 6 else "MEDIUM",
                "kwh": round(float(r["kwh"]), 3),
                "expected": round(float(r["pred"]), 3),
                "message": f"{r['kwh']:.2f} kWh is {direction} the expected {r['pred']:.2f} kWh",
            }
        )

    out = pd.DataFrame(rows, columns=ALERT_COLUMNS)
    if out.empty:
        return out

    # one row per hour: when both types fire on the same hour, merge them ("THRESHOLD + ANOMALY")
    out = out.groupby(["timestamp", "household"], as_index=False, sort=False).agg(
        type=("type", " + ".join),
        severity=("severity", lambda s: "HIGH" if (s == "HIGH").any() else "MEDIUM"),
        kwh=("kwh", "first"),
        expected=("expected", "first"),
        message=("message", "; ".join),
    )[ALERT_COLUMNS]
    return out.sort_values("timestamp", ascending=False).reset_index(drop=True)


def send_email_alert(alerts_df, smtp_cfg, max_rows=20):
    """
    smtp_cfg = {"host": ..., "port": 587, "user": ..., "password": ..., "to": [...]}
    Read these from st.secrets / environment variables, never hardcode them.
    """
    if alerts_df.empty:
        return False

    msg = EmailMessage()
    msg["Subject"] = f"[Power alert] {len(alerts_df)} new alert(s)"
    msg["From"] = smtp_cfg["user"]
    msg["To"] = ", ".join(smtp_cfg["to"])
    msg.set_content(alerts_df.head(max_rows).to_string(index=False))

    with smtplib.SMTP(smtp_cfg["host"], smtp_cfg.get("port", 587)) as server:
        server.starttls()
        server.login(smtp_cfg["user"], smtp_cfg["password"])
        server.send_message(msg)
    return True
