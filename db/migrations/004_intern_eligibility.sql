-- 004 — internships open to graduates: store whether a graduate can take each internship
-- (jh/internships.py, rules in config/internship_rules.yaml) and expose it on the job_board view.
-- Run once in Supabase → SQL Editor on databases created before this change
-- (new databases get it from db/schema.sql). Safe to re-run.
-- Then run Actions → "Rescore all jobs" so internships already stored get classified.
alter table jobs add column if not exists intern_eligibility text;
alter table jobs add column if not exists intern_reason text;

-- j.* is expanded when a view is created, so the view must be recreated to show the new columns.
drop view if exists public.job_board;
create view public.job_board with (security_invoker = true) as
select j.*, s.score, s.base_score, s.profile_id, s.cv_id, s.reasons, s.gaps,
       s.hard_filtered, s.filtered_by, s.llm_used,
       coalesce(a.status, 'new') as status, a.notes, a.updated_at as status_updated_at,
       s.pre_filter_score, s.llm_fit, s.level
from jobs j
left join scores s on s.job_id = j.id
left join applications a on a.job_id = j.id;
