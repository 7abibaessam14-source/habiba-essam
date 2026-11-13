-- app_setup.sql - lets the AmperEC phone app read the sensor and use power save, ONLY for signed-in users.
-- Run once, AFTER hardware/sensor_setup.sql and hardware/power_save_setup.sql:
--   Supabase -> SQL Editor -> New query -> paste -> Run. Safe to run again.
--
-- The app signs in with Supabase Auth (email + password). Only accounts you add yourself in
-- Authentication -> Users exist, as long as "Allow new users to sign up" is OFF (Authentication -> Sign In / Providers).
-- Not signed in (role "anon", the ESP32's key) gets nothing new from this file.

-- read the readings
drop policy if exists "app reads readings" on public.sensor_readings;
create policy "app reads readings" on public.sensor_readings
    for select to authenticated using (true);
grant select on table public.sensor_readings to authenticated;

-- read and change the power save settings (Save, Cancel, Turn back on)
drop policy if exists "app reads power save settings" on public.power_save_settings;
create policy "app reads power save settings" on public.power_save_settings
    for select to authenticated using (true);
drop policy if exists "app adds power save settings" on public.power_save_settings;
create policy "app adds power save settings" on public.power_save_settings
    for insert to authenticated with check (true);
drop policy if exists "app changes power save settings" on public.power_save_settings;
create policy "app changes power save settings" on public.power_save_settings
    for update to authenticated using (true) with check (true);
grant select, insert, update on table public.power_save_settings to authenticated;

-- OWNER ONLY: only emails in app_admins may change power save. Accounts made on the app's "Create account" only view.
-- Add the owner once (NOT in this file, so the email is not published):
--   insert into public.app_admins (email) values ('owner@example.com') on conflict do nothing;
create table if not exists public.app_admins (email text primary key);
alter table public.app_admins enable row level security;   -- no rules: nobody can read the list from outside

create or replace function public.is_app_admin() returns boolean
    language sql stable security definer set search_path = public as
$$ select exists (select 1 from public.app_admins where lower(email) = lower(auth.jwt() ->> 'email')) $$;
revoke all on function public.is_app_admin() from public;
grant execute on function public.is_app_admin() to authenticated;

drop policy if exists "app adds power save settings" on public.power_save_settings;
create policy "app adds power save settings" on public.power_save_settings
    for insert to authenticated with check (public.is_app_admin());
drop policy if exists "app changes power save settings" on public.power_save_settings;
create policy "app changes power save settings" on public.power_save_settings
    for update to authenticated using (public.is_app_admin()) with check (public.is_app_admin());

-- read the power save log
drop policy if exists "app reads power save events" on public.power_save_events;
create policy "app reads power save events" on public.power_save_events
    for select to authenticated using (true);
grant select on table public.power_save_events to authenticated;
