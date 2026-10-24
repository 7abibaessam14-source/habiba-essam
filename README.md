# Smart Energy Monitor

A web dashboard for smart-meter electricity data: it forecasts hourly consumption with Random Forest and
XGBoost, detects unusual hours, raises alerts, shows energy per appliance and simulates real-time monitoring.
Graduation project (Big Data / energy analytics).

## Pages
Home, Analytics, Appliances, Forecast, Anomalies, Alerts, Live, Pipeline monitor (admin), About project.
Login with admin / viewer roles; light and dark mode (menu top right).

## Data
- `data/household_power_consumption.zip` - UCI "Individual Household Electric Power Consumption"
  (one house, 2006-2010, 1-minute readings with 3 appliance sub-meters).
- With Snowflake credentials in the secrets, the app reads the London smart-meter data from Snowflake instead.

## Run on your PC
```
py -m pip install -r requirements.txt
py -m streamlit run app.py
```
Tests: `py test_ml_local.py` and `py test_live_local.py`.

## Settings and secrets
Copy `secrets.toml.example` to `.streamlit/secrets.toml` (never upload it). Login users are added with
`py make_password_hash.py`. "Sign in with Google" is turned on by the `[auth]`, `[auth.google]` and
`[roles]` sections (see the example file). Without any of these the app opens without a login page.
