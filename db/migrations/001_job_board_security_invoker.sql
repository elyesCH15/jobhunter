-- 001 — make the job_board view respect Row Level Security (Postgres 15+).
-- Run once in Supabase → SQL Editor on databases created before this change
-- (new databases get it from db/schema.sql). Safe to re-run.
alter view public.job_board set (security_invoker = true);
