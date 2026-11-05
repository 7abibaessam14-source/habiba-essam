-- supabase_setup.sql - run ONCE in Supabase: your project -> SQL Editor -> New query -> paste all -> Run.
-- Creates the tables used by store.py (safe to run again: it only adds what is missing). Row Level Security is ON with no rules, so nobody can read them
-- from outside; only the app, with the secret key kept in Streamlit / GitHub secrets, can use them.

create table if not exists app_users (
  username       text primary key,
  username_lower text not null unique,                      -- stops "Sara" and "sara" being two accounts
  password_hash  text not null,                             -- PBKDF2-SHA256, never the password itself
  role           text not null default 'viewer' check (role = 'viewer'),  -- sign-up accounts are viewers only
  created        timestamptz not null default now()
);

create table if not exists report_subscriptions (
  username text primary key,
  data     jsonb not null,                                  -- frequency, channels, email, phone, keys, budget
  saved    timestamptz not null default now()
);

create table if not exists meter_readings (
  username    text not null,
  read_on     date not null,
  reading_kwh numeric not null check (reading_kwh >= 0),    -- the total shown on the meter, confirmed by the user
  source      text not null default 'manual',               -- photo, chat or manual
  created     timestamptz not null default now(),
  primary key (username, read_on)                           -- one reading a day for each user
);

alter table app_users enable row level security;
alter table report_subscriptions enable row level security;
alter table meter_readings enable row level security;
