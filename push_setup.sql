-- push_setup.sql - phone notifications for the AmperEC app.
-- Run once, after app_setup.sql: Supabase -> SQL Editor -> New query -> paste -> Run. Safe to run again.
--
-- 1. push_subscriptions: each phone that pressed "Turn on notifications" (only its owner can add/see/remove it).
-- 2. alert_events: one row when the current GOES above the limit (not every 5 seconds while it stays above).
-- The Edge Function "send-push" (supabase/functions/send-push) sends the notification; a Database Webhook calls it
-- when a row is added to power_save_events or alert_events.

create table if not exists public.push_subscriptions (
    endpoint    text primary key,
    user_id     uuid not null default auth.uid() references auth.users (id) on delete cascade,
    p256dh      text not null,
    auth        text not null,
    created_at  timestamptz not null default now()
);
alter table public.push_subscriptions enable row level security;
drop policy if exists "own push subscriptions" on public.push_subscriptions;
create policy "own push subscriptions" on public.push_subscriptions
    for all to authenticated using (user_id = auth.uid()) with check (user_id = auth.uid());
grant select, insert, update, delete on table public.push_subscriptions to authenticated;

create table if not exists public.alert_events (
    id          bigint generated always as identity primary key,
    created_at  timestamptz not null default now(),
    device_id   text not null,
    event       text not null check (event in ('over_limit')),
    current_a   real,
    threshold_a real
);
alter table public.alert_events enable row level security;
drop policy if exists "app reads alert events" on public.alert_events;
create policy "app reads alert events" on public.alert_events for select to authenticated using (true);
grant select on table public.alert_events to authenticated;

-- a reading above the limit right after one that was not (or after a gap of more than 1 minute) = a new alert
create or replace function public.note_over_limit() returns trigger
    language plpgsql security definer set search_path = public as
$$
declare prev boolean;
begin
    if not new.over_threshold then
        return new;
    end if;
    select over_threshold into prev from public.sensor_readings
     where device_id = new.device_id and id < new.id and created_at > new.created_at - interval '1 minute'
     order by id desc limit 1;
    if prev is distinct from true then
        insert into public.alert_events (device_id, event, current_a, threshold_a)
        values (new.device_id, 'over_limit', new.current_a, new.threshold_a);
    end if;
    return new;
end
$$;
drop trigger if exists sensor_readings_over_limit on public.sensor_readings;
create trigger sensor_readings_over_limit after insert on public.sensor_readings
    for each row execute function public.note_over_limit();
