"""Merge the same job seen on several sites (and across runs) into one record."""
from __future__ import annotations

from collections import defaultdict

from rapidfuzz import fuzz

from .models import Job, norm, norm_company, norm_title

TITLE_SIMILARITY = 90


def _loc_key(location: str) -> str:
    n = norm(location)
    return n.split(" ")[0] if n else ""


def dedupe(jobs: list[Job], existing: list[dict]) -> tuple[list[Job], dict[str, list[Job]]]:
    """Returns (unique_jobs, groups) where groups maps canonical id → every Job seen for it
    (so each source/URL can be recorded). Existing DB jobs win as canonical ids."""
    by_company: dict[str, list[tuple[str, str, str]]] = defaultdict(list)  # company → [(id, title, loc)]
    for row in existing:
        by_company[norm_company(row.get("company"))].append(
            (row["id"], norm_title(row.get("title")), _loc_key(row.get("location") or "")))

    groups: dict[str, list[Job]] = {}
    for job in jobs:
        fp = job.fingerprint
        if fp not in groups:
            comp, title, loc = norm_company(job.company), norm_title(job.title), _loc_key(job.location)
            for cid, ctitle, cloc in by_company.get(comp, []):
                if cid == fp:
                    break
                if comp and fuzz.ratio(title, ctitle) >= TITLE_SIMILARITY and (not loc or not cloc or loc == cloc):
                    job.canonical_id = cid
                    break
            fp = job.fingerprint
            if fp not in groups and not any(c[0] == fp for c in by_company.get(comp, [])):
                by_company[comp].append((fp, title, loc))
        groups.setdefault(fp, []).append(job)

    unique = []
    for fp, js in groups.items():
        # keep the version with the longest description; fill gaps from the others
        best = max(js, key=lambda j: len(j.description or ""))
        for other in js:
            for attr in ("apply_url", "salary_min", "salary_max", "salary_currency", "deadline", "posted_at"):
                if not getattr(best, attr) and getattr(other, attr):
                    setattr(best, attr, getattr(other, attr))
            if best.remote == "unknown" and other.remote != "unknown":
                best.remote = other.remote
        best.tags = sorted({j.source_id.split(":")[0] for j in js})
        unique.append(best)
    return unique, groups
