-- Supabase table for GroundTruth session uploads: one row per IMU reading.
-- Run in the Supabase dashboard → SQL editor. Matches RoadReader/Services/SupabaseUploader.swift.

create table if not exists public.readings (
  id               bigint generated always as identity primary key,  -- unique auto-generated reading id
  session_date     date not null,                                     -- date of data collection (phone's local date)
  session_number   integer not null,                                  -- collection session number within that date
  recorded_at      timestamptz not null,                              -- timestamp of the individual reading
  ax               double precision,                                  -- accelerometer X
  ay               double precision,                                  -- accelerometer Y
  az               double precision,                                  -- accelerometer Z
  gx               double precision,                                  -- gyroscope X
  gy               double precision,                                  -- gyroscope Y
  gz               double precision,                                  -- gyroscope Z
  latitude         double precision,                                  -- GPS latitude  (null until the phone had a fix)
  longitude        double precision,                                  -- GPS longitude (null until the phone had a fix)
  anomaly_detected smallint not null default 0                        -- 0 = normal, 1 = anomaly (Detect Bump held)
);

create index if not exists readings_session_idx
  on public.readings (session_date, session_number, recorded_at);

-- The app uses the anon key, so row-level security decides what it may do.
alter table public.readings enable row level security;

-- Insert readings.
create policy "anon can insert readings"
  on public.readings for insert to anon with check (true);

-- Read back the highest session_number of the day so the next upload gets number + 1.
-- (If you drop this policy the app still numbers sessions, but only from what that phone has uploaded.)
create policy "anon can read readings"
  on public.readings for select to anon using (true);

-- Live Broadcasting: let dashboards subscribe to new rows as the app inserts them
-- (Supabase Realtime, "postgres_changes" on INSERT). Idempotent.
do $$
begin
  if not exists (
    select 1 from pg_publication_tables
    where pubname = 'supabase_realtime' and schemaname = 'public' and tablename = 'readings'
  ) then
    alter publication supabase_realtime add table public.readings;
  end if;
end $$;

-- Convenience view for a live dashboard: the most recent session of today.
create or replace view public.live_session as
select *
from public.readings
where session_date = current_date
  and session_number = (select max(session_number) from public.readings where session_date = current_date)
order by recorded_at;

