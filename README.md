# JobHunter — Phase 1

Finds jobs across free job APIs and company career boards every 2 hours, merges duplicates,
classifies and scores each one against your 6 search profiles, and puts them in a
password-protected dashboard where you approve or dismiss them. Supabase holds the live
data; Git keeps a daily history of everything. All free.

```
GitHub Actions (every 2 h)                      Streamlit Cloud (your phone/laptop)
  collectors → classify → dedupe → score  ──►  Supabase  ◄──  dashboard: triage · tracker
                                 free LLMs           │          filters · profiles · sources
GitHub Actions (daily) ── snapshots/*.jsonl ◄────────┘
```

## What's in Phase 1

| Part | Where |
|---|---|
| Sources: Adzuna, Jooble, Remotive, RemoteOK, Arbeitnow, Greenhouse / Lever / Ashby company boards | `jh/collectors/`, `config/sources.yaml` |
| Classification: language, country, region, remote, contract, seniority | `jh/classify.py` |
| Dedupe across sites and across runs | `jh/dedupe.py` |
| Scoring per profile + CV routing + free-LLM refinement with fallback | `jh/score.py`, `jh/llm.py` |
| Hard / soft filters, editable in the dashboard | `jh/filters.py`, `config/filters.yaml` |
| Dashboard with password | `dashboard/app.py` |
| Git history + restore | `scripts/snapshot.py`, `scripts/restore.py` |
| Schedules | `.github/workflows/` |

## Setup (about 30 minutes, once)

### 1. GitHub — private repo
1. Create a **private** repository (e.g. `jobhunter`) at github.com/new.
2. Upload this folder's contents (or `git push`). Private matters: it will hold your CVs and application history.

### 2. Supabase — database
1. Create a free project at supabase.com (pick the EU region closest to you).
2. **SQL Editor → New query** → paste all of `db/schema.sql` → **Run**.
3. **Project Settings → API**: copy the **Project URL** and the **service_role** key (not the anon key).
   The service key bypasses security — it only ever goes in GitHub Secrets, Streamlit Secrets and your local `.env`.

### 3. Free API keys
Sources and LLMs without a key are skipped, so you can start with just one LLM and add the rest later.
- **Gemini**: aistudio.google.com → Get API key
- **Groq**: console.groq.com → API Keys
- **OpenRouter**: openrouter.ai → Keys (free models end in `:free`)
- **Adzuna**: developer.adzuna.com → register → App ID + App Key
- **Jooble**: jooble.org/api/about → request a key by email

Free tiers and model names change; if a model is retired, set `GEMINI_MODEL` / `GROQ_MODEL` / `OPENROUTER_MODEL`.

### 4. GitHub Secrets
Repo → **Settings → Secrets and variables → Actions → New repository secret**, one per line of `.env.example`:
`SUPABASE_URL`, `SUPABASE_SERVICE_KEY`, and whichever of `GEMINI_API_KEY`, `GROQ_API_KEY`,
`OPENROUTER_API_KEY`, `ADZUNA_APP_ID`, `ADZUNA_APP_KEY`, `JOOBLE_KEY` you have.

### 5. First scan
Repo → **Actions** → enable workflows if asked → **Scan for jobs → Run workflow** (tick *force*).
The log ends with a line per source: a number (jobs found), `skipped` (no key yet) or `error`.
The first run also seeds your profiles and filters into Supabase.
Then run **Snapshot database to Git** once to check the history commit works.

### 6. Dashboard
1. share.streamlit.io → sign in with GitHub → **Create app** → your repo, branch `main`, file `dashboard/app.py`.
2. **Advanced settings → Secrets**: paste the three lines from `.streamlit/secrets.toml.example`
   with your values and a long password.
3. Deploy, open the URL, log in. Bookmark it on your phone.

## Daily use
- **Triage**: newest high-score jobs first, with how long ago they were posted. *Approve*, *Maybe later* or *Dismiss*.
- **Tracker**: everything past triage; change status and add notes as applications move.
- **Filters**: add, pause or delete rules; *Re-apply to all jobs* updates jobs already found.
- **Profiles**: edit titles, keywords, exclusions, queries, priority, or pause a profile.
- **Sources**: which sources work, which need a key, which board slugs are wrong.
- **Activity**: scans, status changes, and free-AI usage per provider.

## Adding company boards (hidden market)
In `config/sources.yaml`, add the slug from the company's careers URL:
`boards.greenhouse.io/<slug>`, `jobs.lever.co/<slug>`, `jobs.ashbyhq.com/<slug>`.
The starter slugs are guesses — any wrong one shows as an error on the Sources page; fix or delete it.

## Running locally (optional, Windows)
```powershell
py -m venv .venv; .venv\Scripts\activate
pip install -r dashboard/requirements.txt pytest
copy .env.example .env    # fill in values
python -m pytest -q        # offline tests
python -m jh.scan --dry-run --force   # real sources, nothing saved
$env:JH_DEMO=1; streamlit run dashboard/app.py   # dashboard with sample jobs
```

## History and restore
`snapshots/*.jsonl` is committed daily only when something changed, so `git log snapshots/`
is the timeline of jobs, statuses, filters and profiles. To rebuild after losing the database:
new Supabase project → run `db/schema.sql` → update the secrets → `python scripts/restore.py`.

## Free-tier budget
- Scans: ~12 runs/day × ~2–4 min ≈ 1,000–1,400 of the 2,000 free Actions minutes/month on a private repo.
  If you get close, change the cron in `scan.yml` to every 3 hours.
- GitHub pauses scheduled workflows after 60 days without repo activity; the daily snapshot commit prevents that.
- Supabase free projects pause after a week of no use; the 2-hourly scan keeps it awake.
- LLM: only jobs scoring ≥ 35 on the rules go to the AI, 8 per request, cached — a few dozen calls a day.

## Next phases
2 Speed: Telegram alerts, fast lane, Gmail job-alert parsing · 3 Research button, ATS keywords, cover-letter ideas ·
4 Form filling (stops before submit) · 5 More sources: Tanitjobs, Welcome to the Jungle, EURAXESS, research institutes, web sweeps.
