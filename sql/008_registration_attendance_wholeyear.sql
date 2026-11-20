-- Run once in Supabase → SQL Editor.
-- Stores whole-year registration attendance (Registration Attendance page).
-- Synced from iSAMS batch API by registration_data_wholeyear.py (--sync backfill | incremental).

create table if not exists public.halcyon_registration_attendance_students (
  school_key text not null,
  pupil_id text not null,
  name text not null default '',
  nc_year text not null default '',
  label text not null default '',
  updated_at timestamptz not null default now(),
  primary key (school_key, pupil_id)
);

create table if not exists public.halcyon_registration_attendance_codes (
  school_key text not null,
  code text not null,
  name text not null default '',
  updated_at timestamptz not null default now(),
  primary key (school_key, code)
);

-- One row per school day: marks jsonb is { "pupil_id": { "Pd 1": {mark, class, title}, ... } }
create table if not exists public.halcyon_registration_attendance_days (
  school_key text not null,
  day_date date not null,
  marks jsonb not null default '{}'::jsonb,
  updated_at timestamptz not null default now(),
  primary key (school_key, day_date)
);

create index if not exists halcyon_registration_attendance_days_school_day_idx
  on public.halcyon_registration_attendance_days (school_key, day_date desc);

create table if not exists public.halcyon_registration_attendance_sync (
  school_key text primary key,
  backfill_from date not null,
  backfill_through date,
  backfill_target date not null,
  last_incremental_date date,
  html_generated_at timestamptz,
  updated_at timestamptz not null default now()
);

grant usage on schema public to postgres, anon, authenticated, service_role;

grant select, insert, update, delete on table public.halcyon_registration_attendance_students to service_role;
grant select, insert, update, delete on table public.halcyon_registration_attendance_codes to service_role;
grant select, insert, update, delete on table public.halcyon_registration_attendance_days to service_role;
grant select, insert, update, delete on table public.halcyon_registration_attendance_sync to service_role;

grant select on table public.halcyon_registration_attendance_students to anon, authenticated;
grant select on table public.halcyon_registration_attendance_codes to anon, authenticated;
grant select on table public.halcyon_registration_attendance_days to anon, authenticated;
grant select on table public.halcyon_registration_attendance_sync to anon, authenticated;

alter table public.halcyon_registration_attendance_students enable row level security;
alter table public.halcyon_registration_attendance_codes enable row level security;
alter table public.halcyon_registration_attendance_days enable row level security;
alter table public.halcyon_registration_attendance_sync enable row level security;

drop policy if exists halcyon_registration_attendance_students_all on public.halcyon_registration_attendance_students;
create policy halcyon_registration_attendance_students_all on public.halcyon_registration_attendance_students
  for all to service_role using (true) with check (true);

drop policy if exists halcyon_registration_attendance_codes_all on public.halcyon_registration_attendance_codes;
create policy halcyon_registration_attendance_codes_all on public.halcyon_registration_attendance_codes
  for all to service_role using (true) with check (true);

drop policy if exists halcyon_registration_attendance_days_all on public.halcyon_registration_attendance_days;
create policy halcyon_registration_attendance_days_all on public.halcyon_registration_attendance_days
  for all to service_role using (true) with check (true);

drop policy if exists halcyon_registration_attendance_sync_all on public.halcyon_registration_attendance_sync;
create policy halcyon_registration_attendance_sync_all on public.halcyon_registration_attendance_sync
  for all to service_role using (true) with check (true);

comment on table public.halcyon_registration_attendance_days is
  'Daily registration marks for /registration-wholeyear; marks jsonb keyed by pupil_id then period name';
