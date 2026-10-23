"""One collector per source. Each returns a list[Job] and raises on hard failure
(the scan records the error on that source and moves on)."""
from __future__ import annotations

from typing import Iterable

from ..env import env
from ..models import Job
from .base import Http, RelevanceGate, strip_html, to_iso


class SkipSource(Exception):
    """Source not configured (e.g. missing free API key) — recorded as 'skipped', not 'error'."""


# ---------------- aggregators (query-based) ----------------
def adzuna(cfg: dict, queries: Iterable[str], http: Http, gate: RelevanceGate) -> list[Job]:
    app_id, app_key = env("ADZUNA_APP_ID"), env("ADZUNA_APP_KEY")
    if not (app_id and app_key):
        raise SkipSource("ADZUNA_APP_ID / ADZUNA_APP_KEY not set")
    jobs = []
    for country in cfg.get("countries", ["gb", "fr"]):
        for q in queries:
            data = http.get_json(f"https://api.adzuna.com/v1/api/jobs/{country}/search/1", {
                "app_id": app_id, "app_key": app_key, "what": q, "max_days_old": 7,
                "results_per_page": cfg.get("results_per_query", 50), "sort_by": "date",
                "content-type": "application/json"})
            for r in data.get("results", []):
                jobs.append(Job(
                    source_id="adzuna", external_id=str(r["id"]), title=r.get("title", ""),
                    company=(r.get("company") or {}).get("display_name", ""),
                    location=(r.get("location") or {}).get("display_name", ""),
                    description=strip_html(r.get("description")), url=r.get("redirect_url", ""),
                    posted_at=to_iso(r.get("created")), salary_min=r.get("salary_min"),
                    salary_max=r.get("salary_max"), contract_hint=" ".join(filter(None, [r.get("contract_time"), r.get("contract_type")])),
                    country_hint=country, raw=r))
    return jobs


def jooble(cfg: dict, queries: Iterable[str], http: Http, gate: RelevanceGate) -> list[Job]:
    """One request per location × query: keep locations × max_queries small (free key ≈ 500 requests)."""
    key = env("JOOBLE_KEY")
    if not key:
        raise SkipSource("JOOBLE_KEY not set")
    jobs = []
    for loc in cfg.get("locations", [""]):
        for q in queries:
            data = http.post_json(f"https://jooble.org/api/{key}", {"keywords": q, "location": loc, "page": "1"})
            for r in data.get("jobs", []):
                jobs.append(Job(
                    source_id="jooble", external_id=str(r.get("id") or r.get("link")), title=r.get("title", ""),
                    company=r.get("company", ""), location=r.get("location", ""),
                    description=strip_html(r.get("snippet")), url=r.get("link", ""),
                    posted_at=to_iso(r.get("updated")), contract_hint=r.get("type"), raw=r))
    return jobs


def remotive(cfg: dict, queries: Iterable[str], http: Http, gate: RelevanceGate) -> list[Job]:
    jobs, seen = [], set()
    for q in queries:
        data = http.get_json("https://remotive.com/api/remote-jobs", {"search": q, "limit": 100})
        for r in data.get("jobs", []):
            if r["id"] in seen:
                continue
            seen.add(r["id"])
            desc = strip_html(r.get("description"))
            jobs.append(Job(
                source_id="remotive", external_id=str(r["id"]), title=r.get("title", ""),
                company=r.get("company_name", ""), location=r.get("candidate_required_location") or "Remote",
                description=desc, url=r.get("url", ""), posted_at=to_iso(r.get("publication_date")),
                remote_hint=True, contract_hint=r.get("job_type"), raw={k: v for k, v in r.items() if k != "description"}))
    return jobs


# ---------------- feeds (everything, gated) ----------------
def remoteok(cfg: dict, queries: Iterable[str], http: Http, gate: RelevanceGate) -> list[Job]:
    data = http.get_json("https://remoteok.com/api")
    jobs = []
    for r in data:
        if not isinstance(r, dict) or "id" not in r or "position" not in r:
            continue                                   # first item is a legal notice
        desc = strip_html(r.get("description"))
        if not gate(r.get("position", ""), desc + " " + " ".join(r.get("tags") or [])):
            continue
        jobs.append(Job(
            source_id="remoteok", external_id=str(r["id"]), title=r["position"], company=r.get("company", ""),
            location=r.get("location") or "Remote", description=desc, url=r.get("url", ""),
            apply_url=r.get("apply_url", ""), posted_at=to_iso(r.get("date")),
            salary_min=r.get("salary_min") or None, salary_max=r.get("salary_max") or None,
            salary_currency="USD" if r.get("salary_min") else None, remote_hint=True,
            raw={k: v for k, v in r.items() if k != "description"}))
    return jobs


def arbeitnow(cfg: dict, queries: Iterable[str], http: Http, gate: RelevanceGate) -> list[Job]:
    jobs = []
    for page in range(1, int(cfg.get("pages", 3)) + 1):
        data = http.get_json("https://www.arbeitnow.com/api/job-board-api", {"page": page})
        rows = data.get("data", [])
        for r in rows:
            desc = strip_html(r.get("description"))
            if not gate(r.get("title", ""), desc + " " + " ".join(r.get("tags") or [])):
                continue
            jobs.append(Job(
                source_id="arbeitnow", external_id=r["slug"], title=r.get("title", ""),
                company=r.get("company_name", ""), location=r.get("location", ""), description=desc,
                url=r.get("url", ""), posted_at=to_iso(r.get("created_at")), remote_hint=bool(r.get("remote")) or None,
                contract_hint=" ".join(r.get("job_types") or []), raw={k: v for k, v in r.items() if k != "description"}))
        if not rows:
            break
    return jobs


# ---------------- company job boards (hidden market) ----------------
GREENHOUSE_HOSTS = ("https://boards-api.greenhouse.io", "https://boards-api.eu.greenhouse.io")


def greenhouse(slug: str, http: Http, gate: RelevanceGate) -> list[Job]:
    """Greenhouse hosts boards in two regions (US and EU, e.g. job-boards.eu.greenhouse.io/owkin)
    and the slug doesn't say which: try the US API, then the EU one."""
    first_error = None
    for host in GREENHOUSE_HOSTS:
        try:
            data = http.get_json(f"{host}/v1/boards/{slug}/jobs", {"content": "true"})
            break
        except Exception as e:  # noqa: BLE001 — wrong region looks like a 404; report the US error
            first_error = first_error or e
    else:
        raise first_error
    jobs = []
    for r in data.get("jobs", []):
        desc = strip_html(r.get("content"))
        if not gate(r.get("title", ""), desc):
            continue
        jobs.append(Job(
            source_id=f"greenhouse:{slug}", external_id=str(r["id"]), title=r.get("title", ""),
            company=r.get("company_name") or slug, location=(r.get("location") or {}).get("name", ""),
            description=desc, url=r.get("absolute_url", ""), apply_url=r.get("absolute_url", ""),
            posted_at=to_iso(r.get("first_published") or r.get("updated_at")),
            raw={k: v for k, v in r.items() if k != "content"}))
    return jobs


def lever(slug: str, http: Http, gate: RelevanceGate) -> list[Job]:
    data = http.get_json(f"https://api.lever.co/v0/postings/{slug}", {"mode": "json"})
    jobs = []
    for r in data:
        cats = r.get("categories") or {}
        desc = r.get("descriptionPlain") or strip_html(r.get("description"))
        lists = "\n".join(strip_html(l.get("content")) for l in r.get("lists") or [])
        desc = f"{desc}\n{lists}".strip()
        if not gate(r.get("text", ""), desc):
            continue
        jobs.append(Job(
            source_id=f"lever:{slug}", external_id=r["id"], title=r.get("text", ""), company=slug,
            location=cats.get("location", "") or ", ".join(cats.get("allLocations") or []),
            description=desc, url=r.get("hostedUrl", ""), apply_url=r.get("applyUrl", ""),
            posted_at=to_iso(r.get("createdAt")), contract_hint=cats.get("commitment"),
            remote_hint=True if r.get("workplaceType") == "remote" else None,
            raw={k: v for k, v in r.items() if k not in ("description", "descriptionPlain", "lists")}))
    return jobs


def ashby(slug: str, http: Http, gate: RelevanceGate) -> list[Job]:
    data = http.get_json(f"https://api.ashbyhq.com/posting-api/job-board/{slug}", {"includeCompensation": "true"})
    jobs = []
    for r in data.get("jobs", []):
        if r.get("isListed") is False:
            continue
        desc = r.get("descriptionPlain") or strip_html(r.get("descriptionHtml"))
        if not gate(r.get("title", ""), desc):
            continue
        jobs.append(Job(
            source_id=f"ashby:{slug}", external_id=r["id"], title=r.get("title", ""), company=slug,
            location=r.get("location", ""), description=desc, url=r.get("jobUrl", ""), apply_url=r.get("applyUrl", ""),
            posted_at=to_iso(r.get("publishedAt")), contract_hint=r.get("employmentType"),
            remote_hint=True if r.get("isRemote") or r.get("workplaceType") == "Remote" else None,
            raw={k: v for k, v in r.items() if k not in ("descriptionHtml", "descriptionPlain")}))
    return jobs


AGGREGATORS = {"adzuna": adzuna, "jooble": jooble, "remotive": remotive, "remoteok": remoteok, "arbeitnow": arbeitnow}
BOARDS = {"greenhouse": greenhouse, "lever": lever, "ashby": ashby}
