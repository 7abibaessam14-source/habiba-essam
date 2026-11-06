# Smart Energy Monitor

A web dashboard for smart-meter electricity data: it forecasts hourly consumption with Random Forest and
XGBoost, detects unusual hours, raises alerts, shows energy per appliance and simulates real-time monitoring.
Graduation project (Big Data / energy analytics).

## Pages
Home, Bill & Budget, Reports, Meter assistant, Analytics, Appliances, Forecast, Anomalies, Alerts, Live,
Pipeline monitor (admin), About project.
Login with admin / viewer roles. Tabs at the top; Dark / Light, a color and a moving background for each page (`theme.py`).

## Meter assistant
A chatbot (`meter_assistant.py`, Groq API) that reads a photo of the meter or a reading typed in the chat, and
explains the user's consumption with advice. The model only proposes a reading: the user confirms it before it is saved.
The code works out kWh, bill and CO2 (`billing.py`), and the model explains those numbers without calculating them.
It needs `[groq] api_key` in the secrets and has daily limits, so requests are limited per visit. Without the key
the page still saves typed readings and shows the numbers. With Supabase, run `supabase_setup.sql` again to add the
`meter_readings` table.

## Data
- `data/household_power_consumption.zip` - UCI "Individual Household Electric Power Consumption"
  (one house, 2006-2010, 1-minute readings with 3 appliance sub-meters).
- With Snowflake credentials in the secrets, the app reads the London smart-meter data from Snowflake instead.

## Run on your PC
```
py -m pip install -r requirements.txt
py -m streamlit run app.py
```
Tests: `py test_ml_local.py`, `py test_live_local.py`, `py test_meter_assistant_local.py` and the other `test_*_local.py`.

## Settings and secrets
Copy `secrets.toml.example` to `.streamlit/secrets.toml` (never upload it). Login users are added with
`py make_password_hash.py`. "Sign in with Google" is turned on by the `[auth]`, `[auth.google]` and
`[roles]` sections (see the example file). Without any of these the app opens without a login page.
