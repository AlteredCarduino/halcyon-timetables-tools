-- Add cached HTML for /registration-wholeyear (built by --sync build-html; App Platform reads this).
alter table public.halcyon_registration_attendance_sync
  add column if not exists html_content text;

comment on column public.halcyon_registration_attendance_sync.html_content is
  'Pre-built attendance.html; avoids building ~50MB HTML on each App Platform request';
