"""
live.py - simulated real-time monitoring.

Replays the held-out hours (data the model never trained on) one at a time,
as if each reading had just arrived from the meter. For every new hour:
  1. the trained model predicts the expected kWh (features use only past hours)
  2. residual = actual - expected
  3. robust z-score of the residual against the last `window` streamed hours
     (median / MAD, never looks at future hours)
  4. THRESHOLD / ANOMALY alerts through alerts.build_alerts()

The first `window` hours of the held-out period are shown as history and seed
the z-score window; streaming starts after them.

No Isolation Forest here: it has to be fitted on a whole batch at once, so it
does not suit a one-reading-at-a-time stream. The Anomalies tab still uses both.

In production the replay would be replaced by Event Hubs / Kafka + Spark
Structured Streaming; the scoring and alert logic would stay the same.
"""

import pandas as pd

from alerts import ALERT_COLUMNS, build_alerts
from ml_models import FEATURES


class LiveReplay:
    def __init__(self, feat_df, model, threshold_kwh, household="ALL", window=168, z_thresh=3.5, spike_kwh=2.0):
        self.feats = feat_df
        self.model = model
        self.threshold_kwh = threshold_kwh
        self.household = household
        self.window = window
        self.z_thresh = z_thresh
        self.spike_kwh = spike_kwh
        self.inject_next = False  # set by the "Inject spike" button, used once

        # history shown before streaming starts (half the data at most, for short series)
        self.start = min(window, len(feat_df) // 2)
        warm = feat_df.iloc[: self.start]
        pred = model.predict(warm[FEATURES])
        self.seen = pd.DataFrame(
            {
                "kwh": warm["kwh"].values,
                "pred": pred,
                "residual": warm["kwh"].values - pred,
                "z": float("nan"),
                "is_anomaly": False,
                "injected": False,
            },
            index=warm.index,
        )
        self.pos = self.start
        self.alerts = pd.DataFrame(columns=ALERT_COLUMNS)

    @property
    def finished(self):
        return self.pos >= len(self.feats)

    @property
    def streamed(self):
        return self.pos - self.start

    @property
    def to_stream(self):
        return len(self.feats) - self.start

    def step(self):
        """Process the next hour. Returns (record, new_alerts), or None when the replay is over."""
        if self.finished:
            return None

        row = self.feats.iloc[[self.pos]]
        ts = row.index[0]
        kwh = float(row["kwh"].iloc[0])
        injected = self.inject_next
        if injected:
            kwh += self.spike_kwh
            self.inject_next = False

        pred = float(self.model.predict(row[FEATURES])[0])
        residual = kwh - pred

        ref = self.seen["residual"].tail(self.window)
        med = ref.median()
        mad = (ref - med).abs().median()
        mad = mad if mad > 1e-9 else 1e-9
        z = 0.6745 * (residual - med) / mad

        rec = pd.DataFrame(
            {
                "kwh": [kwh],
                "pred": [pred],
                "residual": [residual],
                "z": [z],
                "is_anomaly": [abs(z) > self.z_thresh],
                "injected": [injected],
            },
            index=pd.DatetimeIndex([ts]),
        )
        new_alerts = build_alerts(rec, household=self.household, threshold_kwh=self.threshold_kwh)
        if injected and not new_alerts.empty:
            new_alerts["message"] = new_alerts["message"] + " (injected)"

        self.seen = pd.concat([self.seen, rec])
        if not new_alerts.empty:
            self.alerts = new_alerts if self.alerts.empty else pd.concat([new_alerts, self.alerts], ignore_index=True)
        self.pos += 1
        return rec, new_alerts
