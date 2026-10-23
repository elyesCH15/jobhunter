-- 002 — store the raw LLM fit and the career level on each score, and expose them (plus
-- pre_filter_score, which "Re-apply to all jobs" needs) on the job_board view.
-- Run once in Supabase → SQL Editor on databases created before this change
-- (new databases get it from db/schema.sql). Safe to re-run.
-- Then run Actions → "Rescore all jobs" so existing jobs get the new junior-first scores.
alter table scores add column if not exists llm_fit real;
alter table scores add column if not exists level text;

drop view if exists public.job_board;
create view public.job_board with (security_invoker = true) as
select j.*, s.score, s.base_score, s.profile_id, s.cv_id, s.reasons, s.gaps,
       s.hard_filtered, s.filtered_by, s.llm_used,
       coalesce(a.status, 'new') as status, a.notes, a.updated_at as status_updated_at,
       s.pre_filter_score, s.llm_fit, s.level
from jobs j
left join scores s on s.job_id = j.id
left join applications a on a.job_id = j.id;
