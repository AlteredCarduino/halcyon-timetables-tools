-- Room blocks: date-range closures that prevent ad-hoc bookings (not shown on the timetable grid).
-- Run once in Supabase → SQL Editor.

create table if not exists public.halcyon_room_blocks (
  id uuid primary key default gen_random_uuid(),
  school_key text not null default 'halcyon',
  room text not null,
  start_date date not null,
  end_date date not null,
  start_time time not null default '08:00',
  end_time time not null default '17:00',
  weekdays smallint[],
  title text not null default '',
  staff_name text not null default '',
  created_at timestamptz not null default now(),
  constraint halcyon_room_blocks_end_after_start check (end_date >= start_date),
  constraint halcyon_room_blocks_time_order check (end_time > start_time)
);

create index if not exists halcyon_room_blocks_school_room_idx
  on public.halcyon_room_blocks (school_key, room, start_date, end_date);

grant select, insert, update, delete on table public.halcyon_room_blocks to service_role;
grant select on table public.halcyon_room_blocks to anon, authenticated;

alter table public.halcyon_room_blocks enable row level security;

drop policy if exists halcyon_room_blocks_all on public.halcyon_room_blocks;
create policy halcyon_room_blocks_all on public.halcyon_room_blocks
  for all to service_role using (true) with check (true);

comment on table public.halcyon_room_blocks is
  'Room closure blocks (date ranges); prevent staff ad-hoc bookings without appearing on Timetabled Rooms grid';

comment on column public.halcyon_room_blocks.weekdays is
  'ISO weekday numbers 0=Mon … 6=Sun; null = every day in the date range';
