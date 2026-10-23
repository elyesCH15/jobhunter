-- JobHunter Phase 1 schema — run once in Supabase: SQL Editor → New query → paste → Run.
-- Row Level Security is ON with no policies: only the service-role key (kept in
-- GitHub Secrets / Streamlit secrets) can read or write. The public anon key sees nothing.

create extension if not exists pgcrypto;

-- ---------- configuration ----------
create table if not exists search_profiles (
  id            text primary key,              -- e.g. 'protein_ml'
  name          text not null,
  priority      text not null default 'medium',-- high | medium | low
  titles        jsonb not null default '[]',
  keywords      jsonb not null default '[]',
  exclude       jsonb not null default '[]',
  contracts     jsonb not null default '[]',
  queries       jsonb not null default '[]',   -- search strings sent to sources
  cv_id         text,                           -- filled when CVs exist
  active        boolean not null default true,
  updated_at    timestamptz not null default now()
);

create table if not exists filters (
  id            uuid primary key default gen_random_uuid(),
  name          text not null,
  kind          text not null check (kind in ('hard','soft')),
  field         text not null,                  -- title | description | text | company | country | region | remote | contract | seniority | language | salary_min | source
  operator      text not null,                  -- contains | not_contains | equals | not_equals | in | not_in | gte | lte
  value         jsonb not null,
  weight        real not null default 0,        -- soft filters: score delta (+/-)
  active        boolean not null default true,
  removed_count integer not null default 0,     -- jobs this rule dropped (hard) / touched (soft)
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now()
);

create table if not exists sources (
  id            text primary key,               -- e.g. 'adzuna', 'greenhouse:deepmind'
  kind          text not null,                  -- api | board | rss | scrape | email | search
  config        jsonb not null default '{}',
  frequency_min integer not null default 120,
  active        boolean not null default true,
  last_run_at   timestamptz,
  last_status   text,                           -- ok | error | skipped
  last_error    text,
  last_found    integer default 0,
  total_found   integer default 0
);

create table if not exists cvs (
  id            text primary key,
  name          text not null,
  targets       jsonb not null default '[]',    -- profile ids
  file_path     text,                           -- path in the Git repo (cvs/…)
  version       text,
  updated_at    timestamptz not null default now()
);

-- ---------- jobs ----------
create table if not exists jobs_raw (
  id            bigserial primary key,
  source_id     text not null,
  external_id   text not null,
  payload       jsonb not null,
  fetched_at    timestamptz not null default now(),
  unique (source_id, external_id)
);

create table if not exists jobs (
  id            text primary key,               -- stable fingerprint hash
  title         text not null,
  company       text,
  location      text,
  country       text,
  region        text,                           -- tunisia | europe | mena | north_america | rest_of_world | remote_global | unknown
  remote        text,                           -- remote | hybrid | onsite | unknown
  contract      text,                           -- full_time | internship | phd | fixed_term | freelance | unknown
  seniority     text,                           -- entry | junior | mid | senior | unknown
  language      text,                           -- en | fr | ar | other
  salary_min    numeric,
  salary_max    numeric,
  salary_currency text,
  deadline      date,
  posted_at     timestamptz,
  url           text,
  apply_url     text,
  description   text,
  tags          jsonb not null default '[]',
  first_seen_at timestamptz not null default now(),
  last_seen_at  timestamptz not null default now()
);
create index if not exists jobs_first_seen_idx on jobs (first_seen_at desc);
create index if not exists jobs_company_idx on jobs (lower(company));

create table if not exists job_sources (
  job_id        text references jobs(id) on delete cascade,
  source_id     text not null,
  external_id   text not null,
  url           text,
  seen_at       timestamptz not null default now(),
  primary key (source_id, external_id)
);

create table if not exists scores (
  job_id        text primary key references jobs(id) on delete cascade,
  score         real not null,                  -- 0..100 after priority + soft filters
  base_score    real,                           -- rule score (priority-weighted)
  pre_filter_score real,                        -- after LLM blend, before soft filters
  profile_id    text,
  profile_scores jsonb not null default '{}',
  cv_id         text,
  reasons       jsonb not null default '[]',
  gaps          jsonb not null default '[]',
  hard_filtered boolean not null default false,
  filtered_by   jsonb not null default '[]',
  llm_used      text,                           -- provider/model or null (rules only)
  scored_at     timestamptz not null default now()
);
create index if not exists scores_score_idx on scores (score desc);

-- ---------- tracking ----------
create table if not exists applications (
  job_id        text primary key references jobs(id) on delete cascade,
  status        text not null default 'new',
  -- new → shortlisted → approved → researched → pack_ready → form_filled → submitted → interview → offer | rejected | dismissed
  notes         text,
  status_history jsonb not null default '[]',
  updated_at    timestamptz not null default now()
);
create index if not exists applications_status_idx on applications (status);

create table if not exists events (
  id            bigserial primary key,
  at            timestamptz not null default now(),
  kind          text not null,                  -- scan | job_new | job_seen | status_change | filter_change | error
  job_id        text,
  source_id     text,
  detail        jsonb not null default '{}'
);
create index if not exists events_at_idx on events (at desc);

create table if not exists llm_usage (
  id            bigserial primary key,
  at            timestamptz not null default now(),
  provider      text not null,
  model         text,
  task          text,
  ok            boolean not null,
  error         text,
  cached        boolean not null default false
);

create table if not exists llm_cache (
  key           text primary key,               -- hash of model+prompt
  response      jsonb not null,
  created_at    timestamptz not null default now()
);

-- Convenience view for the dashboard. security_invoker (Postgres 15+) makes the view run with
-- the caller's rights, so RLS on the tables still applies (without it, a view runs as its owner
-- and would bypass RLS). Existing databases: db/migrations/001_job_board_security_invoker.sql
create or replace view job_board with (security_invoker = true) as
select j.*, s.score, s.base_score, s.profile_id, s.cv_id, s.reasons, s.gaps,
       s.hard_filtered, s.filtered_by, s.llm_used,
       coalesce(a.status, 'new') as status, a.notes, a.updated_at as status_updated_at
from jobs j
left join scores s on s.job_id = j.id
left join applications a on a.job_id = j.id;

-- Lock everything to the service role
do $$
declare t text;
begin
  for t in select tablename from pg_tables where schemaname = 'public' loop
    execute format('alter table %I enable row level security', t);
  end loop;
end $$;
