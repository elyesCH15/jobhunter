-- 003 — CV editor: saved CV versions (one row per saved CV, base CV when job_id is null).
-- Run once in Supabase → SQL Editor on databases created before this change
-- (new databases get it from db/schema.sql). Safe to re-run.
create extension if not exists pgcrypto;

create table if not exists cv_versions (
  id            uuid primary key default gen_random_uuid(),
  job_id        text references jobs(id) on delete set null,   -- null = base CV
  name          text,
  language      text,                           -- en | fr
  spec          jsonb not null default '{}',    -- CV spec (jh/cv/spec.py) — never holds phone / email
  page_count    integer,
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now()
);
create index if not exists cv_versions_job_idx on cv_versions (job_id, updated_at desc);

-- Same lock as every other table: RLS on, no policies → only the service-role key can read or write.
alter table cv_versions enable row level security;
