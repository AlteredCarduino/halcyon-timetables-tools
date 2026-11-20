-- Run once in Supabase → SQL Editor.
-- Logs use of the Student Emails page (page views, copy, Add Parents).

create table if not exists public.student_email_list_usage (
  id uuid primary key default gen_random_uuid(),
  event_type text not null,
  student_count int not null default 0,
  email_count int not null default 0,
  include_parents boolean not null default false,
  details jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create index if not exists student_email_list_usage_created_idx
  on public.student_email_list_usage (created_at desc);

create index if not exists student_email_list_usage_event_idx
  on public.student_email_list_usage (event_type, created_at desc);

grant usage on schema public to postgres, anon, authenticated, service_role;

grant select, insert, update, delete on table public.student_email_list_usage to service_role;
grant select on table public.student_email_list_usage to anon, authenticated;

alter table public.student_email_list_usage enable row level security;

drop policy if exists student_email_list_usage_read on public.student_email_list_usage;
create policy student_email_list_usage_read on public.student_email_list_usage
  for select to anon, authenticated, service_role
  using (true);

drop policy if exists student_email_list_usage_insert on public.student_email_list_usage;
create policy student_email_list_usage_insert on public.student_email_list_usage
  for insert to service_role
  with check (true);

comment on table public.student_email_list_usage is
  'Audit log for /student-emails (page_view, copy_emails, add_parents)';
