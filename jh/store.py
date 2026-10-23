"""Storage layer. SupabaseStore talks to Supabase's REST API (PostgREST) with plain `requests`,
so there's no heavy SDK. MemoryStore implements the same interface for tests and dry runs."""
from __future__ import annotations

import copy
from datetime import datetime, timezone, timedelta
from typing import Any, Iterable, Optional

import requests

from .env import clean, env

NEW_SCORE_COLUMNS = ("llm_fit", "level")      # added by db/migrations/002_scores_llm_fit_level.sql
TABLES = ["search_profiles", "filters", "sources", "cvs", "jobs", "job_sources", "scores",
          "applications", "events", "llm_usage"]          # snapshot order (parents first)
CONFLICT_KEYS = {"search_profiles": "id", "filters": "id", "sources": "id", "cvs": "id", "jobs": "id",
                 "job_sources": "source_id,external_id", "scores": "job_id", "applications": "job_id",
                 "events": "id", "llm_usage": "id", "llm_cache": "key", "jobs_raw": "source_id,external_id"}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class SupabaseStore:
    def __init__(self, url: Optional[str] = None, key: Optional[str] = None, session=None):
        url, key = clean(url) or env("SUPABASE_URL"), clean(key) or env("SUPABASE_SERVICE_KEY")
        if not (url and key):
            raise RuntimeError("SUPABASE_URL / SUPABASE_SERVICE_KEY not set")
        self.url = url.rstrip("/") + "/rest/v1"
        self.s = session or requests.Session()
        self.s.headers.update({"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json"})

    # ---- low level ----
    def _get(self, table: str, params: dict | None = None, page: int = 1000) -> list[dict]:
        out, offset = [], 0
        while True:
            r = self.s.get(f"{self.url}/{table}", params=params or {},
                           headers={"Range-Unit": "items", "Range": f"{offset}-{offset + page - 1}"}, timeout=60)
            r.raise_for_status()
            rows = r.json()
            out.extend(rows)
            if len(rows) < page:
                return out
            offset += page

    def _upsert(self, table: str, rows: list[dict], ignore_duplicates: bool = False, chunk: int = 500):
        if not rows:
            return
        pref = "resolution=ignore-duplicates" if ignore_duplicates else "resolution=merge-duplicates"
        for i in range(0, len(rows), chunk):
            r = self.s.post(f"{self.url}/{table}", params={"on_conflict": CONFLICT_KEYS[table]},
                            json=rows[i:i + chunk], headers={"Prefer": f"{pref},return=minimal"}, timeout=60)
            if r.status_code >= 400:
                raise RuntimeError(f"Supabase upsert {table} failed: {r.status_code} {r.text[:300]}")

    def _patch(self, table: str, match: dict, values: dict):
        params = {k: f"eq.{v}" for k, v in match.items()}
        r = self.s.patch(f"{self.url}/{table}", params=params, json=values,
                         headers={"Prefer": "return=minimal"}, timeout=30)
        r.raise_for_status()

    def _delete(self, table: str, match: dict):
        r = self.s.delete(f"{self.url}/{table}", params={k: f"eq.{v}" for k, v in match.items()}, timeout=30)
        r.raise_for_status()

    # ---- config ----
    def load_profiles(self): return self._get("search_profiles")
    def load_filters(self): return self._get("filters")
    def load_sources(self): return {r["id"]: r for r in self._get("sources")}
    def upsert_profiles(self, rows): self._upsert("search_profiles", rows)
    def upsert_filters(self, rows): self._upsert("filters", rows)
    def delete_filter(self, fid): self._delete("filters", {"id": fid})

    def update_source(self, sid: str, kind: str, **values):
        self._upsert("sources", [{"id": sid, "kind": kind, **values}])

    # ---- jobs ----
    def recent_jobs_index(self, days: int = 60) -> list[dict]:
        since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        return self._get("jobs", {"select": "id,title,company,location", "last_seen_at": f"gte.{since}"})

    def upsert_jobs(self, rows): self._upsert("jobs", rows)
    def add_job_sources(self, rows): self._upsert("job_sources", rows)
    def add_raw(self, rows): self._upsert("jobs_raw", rows, ignore_duplicates=True)
    def upsert_scores(self, rows):
        try:
            self._upsert("scores", rows)
        except RuntimeError as e:
            # Database without migration 002: keep scanning, just without the new columns.
            if "PGRST204" not in str(e) or not any(c in str(e) for c in NEW_SCORE_COLUMNS):
                raise
            print("warning: scores.llm_fit / scores.level missing — run db/migrations/002_scores_llm_fit_level.sql")
            self._upsert("scores", [{k: v for k, v in r.items() if k not in NEW_SCORE_COLUMNS} for r in rows])

    def ensure_applications(self, job_ids: Iterable[str]):
        self._upsert("applications", [{"job_id": j, "status": "new"} for j in job_ids], ignore_duplicates=True)

    def set_seniority(self, levels: dict[str, str]):
        """{job_id: seniority} — a PATCH per level, since an upsert would need every NOT NULL column."""
        by_level: dict[str, list[str]] = {}
        for jid, lvl in levels.items():
            by_level.setdefault(lvl, []).append(jid)
        for lvl, ids in by_level.items():
            for i in range(0, len(ids), 100):
                r = self.s.patch(f"{self.url}/jobs", params={"id": f"in.({','.join(ids[i:i + 100])})"},
                                 json={"seniority": lvl}, headers={"Prefer": "return=minimal"}, timeout=30)
                r.raise_for_status()

    def touch_jobs(self, job_ids: list[str]):
        ts = now_iso()
        for i in range(0, len(job_ids), 100):
            ids = ",".join(job_ids[i:i + 100])
            r = self.s.patch(f"{self.url}/jobs", params={"id": f"in.({ids})"}, json={"last_seen_at": ts},
                             headers={"Prefer": "return=minimal"}, timeout=30)
            r.raise_for_status()

    def get_application(self, job_id: str) -> Optional[dict]:
        rows = self._get("applications", {"job_id": f"eq.{job_id}"})
        return rows[0] if rows else None

    def set_status(self, job_id: str, status: str, notes: Optional[str] = None):
        app = self.get_application(job_id) or {"status_history": []}
        hist = (app.get("status_history") or []) + [{"status": status, "at": now_iso()}]
        values = {"job_id": job_id, "status": status, "status_history": hist, "updated_at": now_iso()}
        if notes is not None:
            values["notes"] = notes
        self._upsert("applications", [values])
        self.log_event("status_change", job_id=job_id, detail={"from": app.get("status"), "to": status})

    def board(self, limit: int = 2000) -> list[dict]:
        return self._get("job_board", {"order": "first_seen_at.desc", "limit": str(limit)})

    # ---- logs & cache ----
    def log_event(self, kind: str, job_id=None, source_id=None, detail=None):
        self.s.post(f"{self.url}/events", json={"kind": kind, "job_id": job_id, "source_id": source_id,
                                                "detail": detail or {}}, timeout=30)

    def log_events(self, rows: list[dict]):
        for i in range(0, len(rows), 500):
            self.s.post(f"{self.url}/events", json=rows[i:i + 500], headers={"Prefer": "return=minimal"}, timeout=60)

    def log_llm(self, **row):
        try:
            self.s.post(f"{self.url}/llm_usage", json=row, headers={"Prefer": "return=minimal"}, timeout=15)
        except requests.RequestException:
            pass

    def cache_get(self, key: str):
        rows = self._get("llm_cache", {"key": f"eq.{key}", "select": "response"})
        return rows[0]["response"] if rows else None

    def cache_put(self, key: str, response):
        self._upsert("llm_cache", [{"key": key, "response": response}])

    def recent(self, table: str, n: int = 200) -> list[dict]:
        col = "at" if table in ("events", "llm_usage") else "id"
        return self._get(table, {"order": f"{col}.desc", "limit": str(n)}, page=n)

    # ---- snapshot / restore ----
    def dump(self, table: str) -> list[dict]:
        order = CONFLICT_KEYS[table].split(",")[0]
        return self._get(table, {"order": f"{order}.asc"})

    def load(self, table: str, rows: list[dict]):
        self._upsert(table, rows)


class MemoryStore:
    """In-memory store with the same interface — used by tests and `--dry-run`."""

    def __init__(self):
        self.t: dict[str, dict[Any, dict]] = {name: {} for name in CONFLICT_KEYS}
        self.events: list[dict] = []
        self.llm_log: list[dict] = []

    def _key(self, table, row):
        return tuple(row[k] for k in CONFLICT_KEYS[table].split(","))

    def _upsert(self, table, rows, ignore=False):
        for row in rows:
            k = self._key(table, row)
            if k in self.t[table]:
                if not ignore:
                    self.t[table][k].update(copy.deepcopy(row))
            else:
                r = copy.deepcopy(row)
                if table == "jobs":
                    r.setdefault("first_seen_at", now_iso())
                    r.setdefault("last_seen_at", now_iso())
                self.t[table][k] = r

    def load_profiles(self): return list(self.t["search_profiles"].values())
    def load_filters(self): return list(self.t["filters"].values())
    def load_sources(self): return {r["id"]: r for r in self.t["sources"].values()}
    def upsert_profiles(self, rows): self._upsert("search_profiles", rows)
    def upsert_filters(self, rows): self._upsert("filters", rows)
    def delete_filter(self, fid): self.t["filters"].pop((fid,), None)
    def update_source(self, sid, kind, **values): self._upsert("sources", [{"id": sid, "kind": kind, **values}])
    def recent_jobs_index(self, days=60):
        return [{k: r.get(k) for k in ("id", "title", "company", "location")} for r in self.t["jobs"].values()]
    def upsert_jobs(self, rows): self._upsert("jobs", rows)
    def add_job_sources(self, rows): self._upsert("job_sources", rows)
    def add_raw(self, rows): self._upsert("jobs_raw", rows, ignore=True)
    def upsert_scores(self, rows): self._upsert("scores", rows)
    def ensure_applications(self, job_ids):
        self._upsert("applications", [{"job_id": j, "status": "new", "status_history": []} for j in job_ids], ignore=True)
    def set_seniority(self, levels):
        for jid, lvl in levels.items():
            if (jid,) in self.t["jobs"]:
                self.t["jobs"][(jid,)]["seniority"] = lvl
    def touch_jobs(self, job_ids):
        for j in job_ids:
            if (j,) in self.t["jobs"]:
                self.t["jobs"][(j,)]["last_seen_at"] = now_iso()
    def get_application(self, job_id): return self.t["applications"].get((job_id,))
    def set_status(self, job_id, status, notes=None):
        app = self.t["applications"].setdefault((job_id,), {"job_id": job_id, "status_history": []})
        old = app.get("status")
        app["status"] = status
        app["status_history"] = app.get("status_history", []) + [{"status": status, "at": now_iso()}]
        if notes is not None:
            app["notes"] = notes
        self.log_event("status_change", job_id=job_id, detail={"from": old, "to": status})
    def board(self, limit=2000):
        out = []
        for (jid,), j in self.t["jobs"].items():
            row = dict(j)
            row.update({k: v for k, v in self.t["scores"].get((jid,), {}).items() if k != "job_id"})
            app = self.t["applications"].get((jid,), {})
            row["status"] = app.get("status", "new")
            row["notes"] = app.get("notes")
            out.append(row)
        return sorted(out, key=lambda r: r.get("first_seen_at", ""), reverse=True)[:limit]
    def log_event(self, kind, job_id=None, source_id=None, detail=None):
        self.events.append({"kind": kind, "job_id": job_id, "source_id": source_id, "detail": detail or {}, "at": now_iso()})
    def log_events(self, rows): self.events.extend(rows)
    def log_llm(self, **row): self.llm_log.append(row)
    def cache_get(self, key):
        r = self.t["llm_cache"].get((key,))
        return r["response"] if r else None
    def cache_put(self, key, response): self._upsert("llm_cache", [{"key": key, "response": response}])
    def recent(self, table, n=200):
        rows = self.events if table == "events" else self.llm_log if table == "llm_usage" else list(self.t[table].values())
        return list(reversed(rows))[:n]
    def dump(self, table): return list(self.t[table].values()) if table != "events" else self.events
    def load(self, table, rows): self._upsert(table, rows)


def get_store(dry_run: bool = False):
    if dry_run or not env("SUPABASE_URL"):
        return MemoryStore()
    return SupabaseStore()
