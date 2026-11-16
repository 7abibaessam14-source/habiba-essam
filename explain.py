"""
explain.py - explains the next-24-hours forecast in plain words (explainable AI, no Streamlit here).

The model (Random Forest / XGBoost) only sees calendar features and past use (ml_models.FEATURES). This file turns
what it saw into short sentences a home user understands: is tomorrow a weekend, how the same day went last week,
is use going up or down lately, when the peak comes, and which inputs the model relies on most.
Every number is computed from the household's own history - nothing is invented.
"""

import pandas as pd

WORDS = {  # the model's inputs in everyday words
    "hour": "the time of day",
    "dayofweek": "the day of the week",
    "month": "the month (season)",
    "is_weekend": "weekend or not",
    "lag_1": "the use one hour before",
    "lag_2": "the use two hours before",
    "lag_24": "the same hour yesterday",
    "lag_168": "the same hour last week",
    "roll_mean_24": "the average of the last 24 hours",
    "roll_std_24": "how much the use changed in the last 24 hours",
    "temp": "the outdoor temperature",
}
DAY = pd.Timedelta(days=1)


def _pct(a, b):
    return (a - b) / b if b else 0.0


def explain_forecast(hourly, forecast, importance=None):
    """
    hourly: the real hourly kWh series (index = time); forecast: the next hours (ml_models.forecast_next);
    importance: ml_models.feature_importance(model) or None.
    Returns {"headline": str, "reasons": [str, ...]}.
    """
    h = hourly.dropna()
    last24 = float(h.tail(24).sum())
    nxt = float(forecast.sum())
    change = _pct(nxt, last24)
    if abs(change) < 0.05:
        headline = f"The next 24 hours should use about the same as the last 24 hours ({nxt:.1f} vs {last24:.1f} kWh)."
    else:
        headline = (f"The next 24 hours should use {nxt:.1f} kWh, {abs(change):.0%} "
                    f"{'more' if change > 0 else 'less'} than the last 24 hours ({last24:.1f} kWh).")

    reasons = []
    daily = h.resample("D").sum(min_count=20).dropna()
    target_day = forecast.index[len(forecast) // 2]  # the day most of the forecast falls on
    weekend = daily[daily.index.dayofweek >= 5].mean()
    weekday = daily[daily.index.dayofweek < 5].mean()
    if pd.notna(weekend) and pd.notna(weekday) and weekday > 0:
        diff = _pct(weekend, weekday)
        if target_day.dayofweek >= 5 and abs(diff) >= 0.05:
            reasons.append(f"It is mostly a weekend day ({target_day:%A}). In this home weekend days use "
                           f"{abs(diff):.0%} {'more' if diff > 0 else 'less'} than weekdays on average.")
        elif target_day.dayofweek < 5 and abs(diff) >= 0.05:
            reasons.append(f"It is mostly a weekday ({target_day:%A}). In this home weekdays use "
                           f"{abs(diff):.0%} {'less' if diff > 0 else 'more'} than weekend days on average.")

    same_last_week = h.loc[forecast.index[0] - 7 * DAY: forecast.index[-1] - 7 * DAY]
    if len(same_last_week) >= 20:
        reasons.append(f"The same 24 hours last week used {same_last_week.sum():.1f} kWh.")

    recent, before = daily.tail(7).mean(), daily.iloc[-14:-7].mean()
    if pd.notna(recent) and pd.notna(before) and before > 0 and abs(_pct(recent, before)) >= 0.05:
        trend = _pct(recent, before)
        reasons.append(f"Use has gone {'up' if trend > 0 else 'down'} lately: the last 7 days averaged "
                       f"{recent:.1f} kWh a day, {abs(trend):.0%} {'more' if trend > 0 else 'less'} than the week before.")

    peak = forecast.idxmax()
    reasons.append(f"The busiest hour should be around {peak:%H:00} ({forecast.max():.2f} kWh in that hour).")

    if importance is not None and len(importance):
        top = [WORDS.get(f, f) for f in importance.index[:2]]
        reasons.append(f"The model relies most on {top[0]} and {top[1]}"
                       f" ({importance.iloc[0]:.0%} and {importance.iloc[1]:.0%} of its decisions).")
    return {"headline": headline, "reasons": reasons}
