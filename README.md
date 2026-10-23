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
| Sources: Jooble, Remotive, RemoteOK, Arbeitnow, Greenhouse / Lever / Ashby company boards (Adzuna: off, paid) | `jh/collectors/`, `config/sources.yaml` |
| Classification: language, country, region, remote, contract, seniority | `jh/classify.py` |
| Dedupe across sites and across runs | `jh/dedupe.py` |
| Scoring per profile + CV routing + free-LLM refinement with fallback | `jh/score.py`, `jh/llm.py` |
| Rescore every stored job after a scoring change | `jh/rescore.py`, `.github/workflows/rescore.yml` |
| Hard / soft filters, editable in the dashboard | `jh/filters.py`, `config/filters.yaml` |
| Dashboard with password | `dashboard/app.py` |
| Git history + restore | `scripts/snapshot.py`, `scripts/restore.py` |
| CV editor: career database → tailored CV → PDF (Typst) | `career/`, `jh/cv/`, `dashboard/cv_editor.py` — see [CV editor](#cv-editor) |
| Schedules | `.github/workflows/` |

## Setup (about 30 minutes, once)

### 1. GitHub — private repo
1. Create a **private** repository (e.g. `jobhunter`) at github.com/new.
2. Upload this folder's contents (or `git push`). Private matters: it will hold your CVs and application history.

### 2. Supabase — database
1. Create a free project at supabase.com (pick the EU region closest to you).
2. **SQL Editor → New query** → paste all of `db/schema.sql` → **Run**.
   Already have a database from an earlier version? Run the files in `db/migrations/` in order instead
   (`001` makes the `job_board` view respect Row Level Security; `002` adds the `llm_fit` and `level`
   score columns for junior-first scoring — see [Updating to junior-first scoring](#updating-to-junior-first-scoring);
   `003` adds the `cv_versions` table for the [CV editor](#cv-editor)).
3. **Project Settings → API**: copy the **Project URL** and the **service_role** key (not the anon key).
   The service key bypasses security — it only ever goes in GitHub Secrets, Streamlit Secrets and your local `.env`.

### 3. Free API keys
Sources and LLMs without a key are skipped, so you can start with just one LLM and add the rest later.
- **Gemini**: aistudio.google.com → Get API key
- **Groq**: console.groq.com → API Keys
- **OpenRouter**: openrouter.ai → Keys (free models end in `:free`)
- **Jooble**: jooble.org/api/about → request a key by email. The free key has a 500-request limit, so
  Jooble runs once a day with 3 locations × 4 queries (≈ 360 requests a month) — see `config/sources.yaml`.
- **Adzuna**: real access now costs £8/month, so it is `active: false` in `config/sources.yaml`.
  To use it: developer.adzuna.com → App ID + App Key → secrets → set `active: true`.

Stray spaces, tabs or line breaks around a pasted key or URL are trimmed automatically (`jh/env.py`).

Free tiers and model names change; if a model is retired, set `GEMINI_MODEL` / `GROQ_MODEL` / `OPENROUTER_MODEL`.

### 4. GitHub Secrets
Repo → **Settings → Secrets and variables → Actions → New repository secret**, one per line of `.env.example`:
`SUPABASE_URL`, `SUPABASE_SERVICE_KEY`, and whichever of `GEMINI_API_KEY`, `GROQ_API_KEY`,
`OPENROUTER_API_KEY`, `JOOBLE_KEY` (and `ADZUNA_APP_ID`, `ADZUNA_APP_KEY` if you pay for Adzuna) you have.

### 5. First scan
Repo → **Actions** → enable workflows if asked → **Scan for jobs → Run workflow** (tick *force*).
The log ends with a line per source: a number (jobs found), `skipped` (no key yet) or `error`.
The first run also seeds your profiles and filters into Supabase.
Then run **Snapshot database to Git** once to check the history commit works.

### 6. Dashboard
1. share.streamlit.io → sign in with GitHub → **Create app** → your repo, branch `main`, file `dashboard/app.py`.
2. **Advanced settings → Secrets**: paste the lines from `.streamlit/secrets.toml.example`
   with your values and a long password (`GITHUB_TOKEN` / `GITHUB_REPO` are optional — see [CV editor](#cv-editor)).
3. Deploy, open the URL, log in. Bookmark it on your phone.

## Daily use
- **Triage**: newest high-score jobs first, with how long ago they were posted. *Approve*, *Maybe later* or *Dismiss*.
  Entry-level and junior jobs carry a green **Junior-friendly** chip; the sidebar toggle *Junior-friendly only*
  hides everything else (tick *Include unknown level* to keep jobs whose level couldn't be told).
- **Tracker**: everything past triage; change status and add notes as applications move.
- **Filters**: add, pause or delete rules; *Re-apply to all jobs* updates jobs already found.
- **Profiles**: edit titles, keywords, exclusions, queries, priority, or pause a profile.
- **Sources**: which sources work, which need a key, which board slugs are wrong. Sources you remove from
  `config/sources.yaml` show as `inactive` after the next scan.
- **Activity**: scans, status changes, and free-AI usage per provider.

## CV editor
Sidebar → **CV editor**. One page, FlowCV-style: the **live preview is on the left**, the **editing panel on the
right**. Everything comes from your career database, `career/career.yaml` (experiences with their bullet banks,
projects, education with coursework and degree wordings, skills, languages, headlines, summaries).

**Top bar**
- **Target**: *Base CV*, or any job you moved to *approved / researched / pack ready / form filled / submitted*.
  Switching opens that target's latest saved version, or starts one from the database defaults:
  - *Base CV* = every entry, bullet and course marked `default: true` (`cv: true` for courses).
  - *A job* = the same, tilted toward the job's profile: entries whose `profiles` list it get up to 2 extra
    relevant bullets, other entries keep their 2 most relevant default bullets, the best-matching bullet wording
    (`variant_of`) is used, projects for that profile come first, headline / summary / degree wording are the
    first ones listing the profile, and the CV is in French when the job is. Entries marked `to_confirm` are
    never switched on automatically.
- **Language** EN / FR (headings, dates like “févr. 2026 – juil. 2026”; a bullet without French text falls back
  to English and says so), **Template** (*Compact — one page* or *Classic — roomy, up to 2 pages*),
  **Page budget** (1 or 2 — the page shows a warning when the PDF is longer).
- **Pages** (counted from the compiled PDF), **Version name**, **Save version**, **Export PDF**.
- Two reserved slots: **ATS match %** and **Generate CV** — built in the next steps; nothing calls an AI yet.
- **Saved versions for this target**: load, duplicate or delete earlier versions.

**Editing panel** (tabs): *Header & summary* (headline, summary, which sections show and in what order) ·
*Experience* · *Projects* · *Education* (degree wording, coursework) · *Skills* · *Languages & other*
(languages, TOEFL on/off, activities). For each entry: tick to include, ↑ ↓ to reorder, then pick it under
*Edit …* to change its title / organisation / dates and its bullets — tick, reorder, choose a wording when
several exist, edit the text, or *Add a custom bullet*. You can also add custom entries and skills.
Skill levels are never printed; skills print grouped by category in the order you choose.

**Two save scopes for text**
1. **This CV only** (default): typing in a box stores the change in this CV's spec as an override.
   *Reset* drops it. The database is untouched.
2. **Save to database**: writes the text into `career/career.yaml` so every future CV uses it. The dashboard
   commits through the GitHub API (Streamlit Cloud can't run `git`): it reads the file and its `sha`, changes
   only that text — comments, order and layout stay as they are (ruamel.yaml round-trip; the result is re-parsed
   and refused if anything else changed) — and commits it as *“career: update eur_sota (en) from the CV editor”*.
   A custom bullet saved this way is added to its entry with `status: to_confirm`. `career/personal.yaml` can
   never be written this way.

The editor reads `career.yaml` from GitHub when the secrets below are set (refreshed every 60 s, so saved edits
show up without a redeploy), otherwise from the deployed copy.

**Set up “Save to database”** (once):
1. github.com → your avatar → **Settings → Developer settings → Personal access tokens → Fine-grained tokens →
   Generate new token**.
2. Name `jobhunter-cv-editor`; expiration e.g. 1 year; **Resource owner**: your account.
3. **Repository access → Only select repositories →** `elyesCH15/jobhunter`.
4. **Permissions → Repository permissions → Contents: Read and write**. Leave everything else at *No access*
   (*Metadata: Read-only* is added automatically).
5. **Generate token** and copy it (shown once).
6. share.streamlit.io → your app → **⋮ → Settings → Secrets**, add:
   ```toml
   GITHUB_TOKEN = "github_pat_..."
   GITHUB_REPO = "elyesCH15/jobhunter"
   # GITHUB_BRANCH = "main"     # optional, default main
   ```
Without these secrets the button explains what to set up and keeps your edit on the CV only.
Each database save is a commit to `main`, so Streamlit Cloud may restart the app to pick it up — save your
CV version first. It also triggers the *Tests* workflow (about a minute of Actions time).

**Storage**: CV versions are rows of the `cv_versions` table (`spec` = the CV as JSON, see `jh/cv/spec.py`).
Existing databases: Supabase → **SQL Editor** → run `db/migrations/003_cv_versions.sql` once (safe to re-run).
Until then the editor works but can't save versions, and the daily snapshot skips the table. After it,
`snapshots/cv_versions.jsonl` puts every saved CV in Git history.

**Personal details** (name, phone, email, links) live only in `career/personal.yaml` and are read only by
`jh/cv/render.py` when the PDF is drawn. Specs, saved versions and `cv_text_for_llm()` (what a future AI step
will receive) never contain them — a test checks this.

**Output**: rendered with [Typst](https://typst.app) through the `typst` Python package (compiled in-process,
no external program, ~80 ms for a first render, ~45 ms for PDF + preview after that; unchanged CVs aren't
recompiled). ATS-safe: one column, standard headings (Summary / Experience / Projects / Education / Skills /
Languages, or Profil / Expérience professionnelle / Projets / Formation / Compétences / Langues), real selectable
text, no tables, icons or columns, links printed as plain text. Fonts are bundled in `jh/cv/fonts/` (Carlito,
metric-compatible with Calibri, SIL OFL) plus Typst's built-in Libertinus Serif, so the PDF looks the same
locally and on Streamlit Cloud.

## How the score works
Junior roles come first: a decent junior or entry-level match ranks above a perfect-match mid-level role,
and senior roles sink.

1. **Rule score (0–100)**, per profile, then × profile priority (high 1.0, medium 0.85, low 0.7):

   | Part | Points |
   |---|---|
   | Title | 30 if it contains one of the profile's titles, else up to 20 for a close title |
   | Keywords | 5 per profile keyword in the posting, +3 more if it's in the title, max 30 |
   | Contract | 10 if it's one of the profile's contract types, 5 if unknown, 0 otherwise |
   | Seniority | entry 30 · junior 30 · unknown 12 · mid 3 · senior 0 |
   | Exclusions | −40 for a profile exclude term, −30 for a global exclude term |

   Seniority comes from the title first (senior / lead / staff… → senior; junior / graduate → junior;
   intern / stage / PhD → entry), then the description: explicit early-career wording ("0-2 years", "1+ years",
   "recent graduate", "entry level", "early career", "no prior experience", "jeune diplômé",
   "débutant accepté", "première expérience", "0 à 2 ans"…) → junior, or entry for internships, new-grad and
   graduate programmes. Otherwise the smallest "N years" wins, a range counting by its lower bound
   ("3-5 years" = 3): ≤ 2 junior, 3–4 mid, 5+ senior.
2. **Free-LLM refinement** for jobs scoring ≥ 35: the AI is told junior suitability matters most and must
   give fit ≤ 40 to anything asking for more than 2 years or senior / lead / staff level. Score =
   0.4 × rule score + 0.6 × AI fit. The AI also reports the level; when the rules said *unknown*, the AI's
   level is used (and saved on the job).
3. **Career adjustment**: entry / junior +10 · unknown 0 · mid −10 · senior −25, shown on the card
   ("junior role: +10 pts", "mid-level role: −10 pts").
4. **Soft filters** (`Prefer remote +5`…) are added last. Hard filters (e.g. *Drop senior roles*) hide a job.

Example (same profiles, rules only): a junior "Machine Learning Engineer" with no profile keywords ends at
**80**; a mid-level (3–5 years) protein ML engineer matching every keyword ends at **63**; the senior
version of that job ends at **45** — and is hidden anyway by the *Drop senior roles* filter.

In the database: `scores.base_score` = rule score, `llm_fit` = raw AI fit, `level` = level used for the
adjustment, `pre_filter_score` = after blend + adjustment, `score` = final.

## Updating to junior-first scoring
1. Supabase → **SQL Editor** → paste `db/migrations/002_scores_llm_fit_level.sql` → **Run**
   (adds `scores.llm_fit` / `scores.level` and exposes them on the `job_board` view; safe to re-run).
   Scans keep working before you do this — the two new columns are just skipped, with a warning in the log.
2. Push the new code.
3. Repo → **Actions → Rescore all jobs → Run workflow**. It re-classifies the seniority of every stored job
   and scores it again with the current profiles, filters and AI. Your statuses and notes are not touched.
   It shares the scan's queue, so it never runs at the same time as a scan. Tick *Rules only* to skip
   the AI (fast, saves free-tier quota — but levels the AI filled in before go back to *unknown*).
   Locally: `python -m jh.rescore` (or `python scripts/rescore.py`), add `--dry-run` to save nothing.

Run **Rescore all jobs** again whenever you change the scoring code or edit profiles heavily.

## Adding company boards (hidden market)
Company boards are listed in `config/sources.yaml` under `company_boards`, one slug per line:

1. Open the company's careers page and click a job. The link tells you the platform and the slug:
   `job-boards.greenhouse.io/<slug>` (or `boards.greenhouse.io/`, `job-boards.eu.greenhouse.io/`),
   `jobs.lever.co/<slug>`, `jobs.ashbyhq.com/<slug>`.
2. Check it by opening the API URL in a browser — JSON with jobs means it works, 404 means wrong slug or platform:
   - Greenhouse `https://boards-api.greenhouse.io/v1/boards/<slug>/jobs` (EU boards: `boards-api.eu.greenhouse.io`;
     the scanner tries both automatically)
   - Lever `https://api.lever.co/v0/postings/<slug>?mode=json`
   - Ashby `https://api.ashbyhq.com/posting-api/job-board/<slug>`
3. Add the slug under the right platform, commit, push. A wrong slug only shows as an error on the Sources page.

Companies on other systems (Workable, Teamtailor, Welcome to the Jungle, their own site) aren't supported yet;
they're listed at the bottom of `sources.yaml` as Phase 5 candidates.

**Relevance gate.** Boards return every posting, so before anything is stored the title must name an
ML / data / research-engineering role ("machine learning", "ML engineer", "data scientist", "research scientist",
"bioinformatics", "remote sensing", "PhD"… or one of your profile titles), or be an early-career title
(intern, fellow, resident, graduate, junior) about AI / ML / research / data. Otherwise the description needs
at least 3 of your profile keywords including a technical ML term, and the title must not be a sales,
recruiting, legal, finance, office or manager job.

**Strict boards.** Big boards (Anthropic, xAI, Planet… 100+ postings, mostly not ML) are listed under
`strict:` in `sources.yaml`. On those, only the title counts, and senior / sr / staff / lead / principal /
manager / head / director / architect / chief / VP titles are dropped — except intern, fellowship, residency,
graduate, new grad, early career and junior titles, which are always kept when role-relevant.
Add any source id (`greenhouse:<slug>`, `ashby:<slug>`, `lever:<slug>`, or an aggregator like `remoteok`)
to make it strict.

## Running locally (optional, Windows)
```powershell
py -m venv .venv; .venv\Scripts\activate
pip install -r dashboard/requirements.txt pytest pypdf
copy .env.example .env    # fill in values
python -m pytest -q        # offline tests
python -m jh.scan --dry-run --force   # real sources, nothing saved
$env:JH_DEMO=1; streamlit run dashboard/app.py   # dashboard with sample jobs (CV editor included)
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
  A full rescore sends every stored job above 35 again (one call per 8 jobs); if the free tiers run out
  midway, the rest keep their rule-only scores — run it again later or use *Rules only*.

## Next phases
2 Speed: Telegram alerts, fast lane, Gmail job-alert parsing · 3 Research button, ATS keywords, cover-letter ideas ·
4 Form filling (stops before submit) · 5 More sources: Tanitjobs, Welcome to the Jungle, EURAXESS, research institutes, web sweeps.
