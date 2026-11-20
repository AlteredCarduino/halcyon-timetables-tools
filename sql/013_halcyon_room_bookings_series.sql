-- Recurring booking series + staff email for ownership checks.
-- Run once in Supabase → SQL Editor.

alter table public.halcyon_room_bookings
  add column if not exists series_id uuid,
  add column if not exists staff_email text not null default '';

create index if not exists halcyon_room_bookings_series_idx
  on public.halcyon_room_bookings (school_key, series_id)
  where series_id is not null;

comment on column public.halcyon_room_bookings.series_id is
  'Shared id for weekly recurring bookings; null for one-off bookings';
comment on column public.halcyon_room_bookings.staff_email is
  'School email of booker; required for delete ownership checks';
