-- Run once in Supabase → SQL Editor.
-- Replaces boolean period columns with O / P / A / - status codes on halcyon_registration_data.
--
-- O = not on this period's register (UI 〇)
-- P = present (UI ✅)
-- A = absent / out (UI ❌)
-- - = on register, not marked yet
--
-- Note: these letters are app storage codes, not iSAMS DfE absence code letters.

alter table public.halcyon_registration_data
  alter column eca1 type text using (
    case when eca1 is true then 'P' when eca1 is false then 'A' else 'O' end
  ),
  alter column pd1 type text using (
    case when pd1 is true then 'P' when pd1 is false then 'A' else 'O' end
  ),
  alter column pd2 type text using (
    case when pd2 is true then 'P' when pd2 is false then 'A' else 'O' end
  ),
  alter column pd3 type text using (
    case when pd3 is true then 'P' when pd3 is false then 'A' else 'O' end
  ),
  alter column pd4 type text using (
    case when pd4 is true then 'P' when pd4 is false then 'A' else 'O' end
  ),
  alter column pd5 type text using (
    case when pd5 is true then 'P' when pd5 is false then 'A' else 'O' end
  ),
  alter column pd6 type text using (
    case when pd6 is true then 'P' when pd6 is false then 'A' else 'O' end
  ),
  alter column eca2 type text using (
    case when eca2 is true then 'P' when eca2 is false then 'A' else 'O' end
  );

alter table public.halcyon_registration_data
  alter column eca1 set default 'O',
  alter column pd1 set default 'O',
  alter column pd2 set default 'O',
  alter column pd3 set default 'O',
  alter column pd4 set default 'O',
  alter column pd5 set default 'O',
  alter column pd6 set default 'O',
  alter column eca2 set default 'O';

alter table public.halcyon_registration_data
  add constraint halcyon_registration_data_eca1_status_chk
    check (eca1 in ('O', 'P', 'A', '-')),
  add constraint halcyon_registration_data_pd1_status_chk
    check (pd1 in ('O', 'P', 'A', '-')),
  add constraint halcyon_registration_data_pd2_status_chk
    check (pd2 in ('O', 'P', 'A', '-')),
  add constraint halcyon_registration_data_pd3_status_chk
    check (pd3 in ('O', 'P', 'A', '-')),
  add constraint halcyon_registration_data_pd4_status_chk
    check (pd4 in ('O', 'P', 'A', '-')),
  add constraint halcyon_registration_data_pd5_status_chk
    check (pd5 in ('O', 'P', 'A', '-')),
  add constraint halcyon_registration_data_pd6_status_chk
    check (pd6 in ('O', 'P', 'A', '-')),
  add constraint halcyon_registration_data_eca2_status_chk
    check (eca2 in ('O', 'P', 'A', '-'));

comment on column public.halcyon_registration_data.pd1 is
  'Registration status: O=not on register, P=present, A=absent, -=unmarked';
