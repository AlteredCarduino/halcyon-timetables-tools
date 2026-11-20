-- Ad-hoc room bookings on /timetabled-rooms (15-minute slots in iSAMS-empty cells).
-- Run once in Supabase → SQL Editor.

create table if not exists public.halcyon_room_bookings (
  id uuid primary key default gen_random_uuid(),
  school_key text not null default 'halcyon',
  room text not null,
  day_date date not null,
  start_time time not null,
  end_time time not null,
  period_order integer,
  title text not null default '',
  staff_name text not null default '',
  created_at timestamptz not null default now(),
  constraint halcyon_room_bookings_end_after_start check (end_time > start_time)
);

create index if not exists halcyon_room_bookings_school_day_idx
  on public.halcyon_room_bookings (school_key, day_date, room);

create index if not exists halcyon_room_bookings_day_time_idx
  on public.halcyon_room_bookings (school_key, day_date, start_time, end_time);

create index if not exists halcyon_room_bookings_period_order_idx
  on public.halcyon_room_bookings (school_key, day_date, period_order);

grant select, insert, update, delete on table public.halcyon_room_bookings to service_role;
grant select on table public.halcyon_room_bookings to anon, authenticated;

alter table public.halcyon_room_bookings enable row level security;

drop policy if exists halcyon_room_bookings_all on public.halcyon_room_bookings;
create policy halcyon_room_bookings_all on public.halcyon_room_bookings
  for all to service_role using (true) with check (true);

comment on table public.halcyon_room_bookings is
  'Staff ad-hoc room bookings (15 min) shown on Timetabled Rooms; iSAMS lessons take precedence in the UI';
