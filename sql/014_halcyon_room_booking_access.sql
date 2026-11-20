-- Per-room booking allowlists (staff email must be listed to book restricted rooms).
-- Run once in Supabase → SQL Editor.

create table if not exists public.halcyon_room_booking_access (
  id uuid primary key default gen_random_uuid(),
  school_key text not null default 'halcyon',
  room text not null,
  staff_email text not null,
  created_at timestamptz not null default now(),
  constraint halcyon_room_booking_access_email_lower check (staff_email = lower(staff_email)),
  constraint halcyon_room_booking_access_unique unique (school_key, room, staff_email)
);

create index if not exists halcyon_room_booking_access_room_idx
  on public.halcyon_room_booking_access (school_key, room);

grant select, insert, update, delete on table public.halcyon_room_booking_access to service_role;
grant select on table public.halcyon_room_booking_access to anon, authenticated;

alter table public.halcyon_room_booking_access enable row level security;

drop policy if exists halcyon_room_booking_access_all on public.halcyon_room_booking_access;
create policy halcyon_room_booking_access_all on public.halcyon_room_booking_access
  for all to service_role using (true) with check (true);

comment on table public.halcyon_room_booking_access is
  'Staff emails allowed to book specific restricted rooms on Timetabled Rooms';
