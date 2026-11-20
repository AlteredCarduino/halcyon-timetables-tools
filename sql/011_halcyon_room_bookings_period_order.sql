-- Remember which timetable period row a booking was made in (avoids boundary misplacement).
-- Run once in Supabase → SQL Editor.

alter table public.halcyon_room_bookings
  add column if not exists period_order integer;

create index if not exists halcyon_room_bookings_period_order_idx
  on public.halcyon_room_bookings (school_key, day_date, period_order);
