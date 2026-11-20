-- Staff emails allowed to open Registration Attendance (/registration-wholeyear).
-- Run once in Supabase → SQL Editor.

create table if not exists public.halcyon_registration_attendance_access (
  id uuid primary key default gen_random_uuid(),
  school_key text not null default 'halcyon',
  staff_email text not null,
  created_at timestamptz not null default now(),
  constraint halcyon_registration_attendance_access_email_lower check (staff_email = lower(staff_email)),
  constraint halcyon_registration_attendance_access_unique unique (school_key, staff_email)
);

create index if not exists halcyon_registration_attendance_access_email_idx
  on public.halcyon_registration_attendance_access (school_key, staff_email);

grant select, insert, update, delete on table public.halcyon_registration_attendance_access to service_role;
grant select on table public.halcyon_registration_attendance_access to anon, authenticated;

alter table public.halcyon_registration_attendance_access enable row level security;

drop policy if exists halcyon_registration_attendance_access_all
  on public.halcyon_registration_attendance_access;
create policy halcyon_registration_attendance_access_all
  on public.halcyon_registration_attendance_access
  for all to service_role using (true) with check (true);

comment on table public.halcyon_registration_attendance_access is
  'Staff emails allowed to view Registration Attendance (/registration-wholeyear)';
