"""Fit scoring: rule-based score per profile, optional batched LLM refinement, soft filters."""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field

from rapidfuzz import fuzz

from . import filters as F
from .llm import Gateway, LLMUnavailable
from .models import Job, norm

PRIORITY_MULT = {"high": 1.0, "medium": 0.85, "low": 0.7}

# Until the CVs exist, profiles map to the 3 planned CV groups.
DEFAULT_CV_GROUPS = {
    "protein_ml": "cv_bio_research", "research_hpc": "cv_bio_research",
    "climate_ai": "cv_climate_mission", "mission_driven": "cv_climate_mission",
    "general_ml": "cv_ml_genai", "genai_cv": "cv_ml_genai",
}

LLM_THRESHOLD = float(os.getenv("LLM_SCORE_THRESHOLD", "35"))   # only promising jobs go to the LLM
LLM_BATCH = int(os.getenv("LLM_BATCH_SIZE", "8"))


@dataclass
class ScoreResult:
    job_id: str
    score: float
    base_score: float
    profile_id: str | None
    profile_scores: dict
    cv_id: str | None
    reasons: list = field(default_factory=list)
    gaps: list = field(default_factory=list)
    hard_filtered: bool = False
    filtered_by: list = field(default_factory=list)
    llm_used: str | None = None
    pre_filter_score: float | None = None

    def to_row(self) -> dict:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}


def _phrase_in(phrase: str, text: str) -> bool:
    p = norm(phrase)
    return bool(p) and re.search(rf"(?<!\w){re.escape(p)}(?!\w)", text) is not None


def rule_score(job: Job, profile: dict, global_exclude: list[str]) -> tuple[float, list[str], list[str]]:
    title = norm(job.title)
    body = norm(job.text)
    reasons, gaps = [], []
    s = 0.0

    # title (max 40)
    exact = [t for t in profile.get("titles", []) if _phrase_in(t, title)]
    if exact:
        s += 40
        reasons.append(f"title matches '{exact[0]}'")
    else:
        best = max((fuzz.token_set_ratio(norm(t), title) for t in profile.get("titles", [])), default=0)
        s += max(0.0, (best - 50) / 50) * 25
        if best >= 75:
            reasons.append("title close to target roles")

    # keywords (max 40)
    kw_hits = [k for k in profile.get("keywords", []) if _phrase_in(k, body)]
    kw_title = [k for k in kw_hits if _phrase_in(k, title)]
    s += min(40, 6 * len(kw_hits) + 4 * len(kw_title))
    if kw_hits:
        reasons.append("keywords: " + ", ".join(kw_hits[:6]))
    else:
        gaps.append("no profile keywords in posting")

    # contract (max 10)
    if job.contract in profile.get("contracts", []):
        s += 10
    elif job.contract == "unknown":
        s += 5
    else:
        gaps.append(f"contract '{job.contract}' not in profile")

    # seniority (max 10)
    s += {"entry": 10, "junior": 10, "unknown": 6, "mid": 2}.get(job.seniority, 0)
    if job.seniority == "mid":
        gaps.append("asks for ~3–4 years experience")

    # exclusions
    ex = [e for e in profile.get("exclude", []) if _phrase_in(e, body)]
    if ex:
        s -= 40
        gaps.append("excluded term: " + ", ".join(ex[:3]))
    gx = [e for e in global_exclude if _phrase_in(e, title) or (any(c.isdigit() for c in e) and _phrase_in(e, body))]
    if gx:
        s -= 30
        gaps.append("global exclude: " + ", ".join(gx[:3]))

    return max(0.0, min(100.0, s)), reasons, gaps


LLM_SYSTEM = """You screen job postings for one candidate. Be strict and factual.
Candidate: early-career ML engineer (5-year engineering degree, Tunisia, 2026). Experience: fine-tuning
protein language models (ESM2-15B) for cryptic binding site prediction on a EuroHPC supercomputer;
Stable Diffusion for home staging; RAG system on carbon-market standards; satellite forest
fragmentation analysis. Languages: Arabic, English, French. Open to relocation and remote.
Profiles: {profiles}
For EACH job return an object: {{"id": <job id>, "fit": 0-100, "profile": <best profile id>,
"reasons": [<=3 short strings], "gaps": [<=3 short strings: missing requirements]}}.
fit = how strong this candidate's application would be AND how well the job matches a profile.
Reply with a JSON array only."""


def _llm_payload(job: Job) -> dict:
    return {"id": job.fingerprint, "title": job.title, "company": job.company, "location": job.location,
            "contract": job.contract, "seniority": job.seniority, "description": job.description[:2500]}


def score_jobs(jobs: list[Job], profiles: list[dict], global_exclude: list[str], rules: list[F.Rule],
               gateway: Gateway | None = None) -> list[ScoreResult]:
    active = [p for p in profiles if p.get("active", True)]
    results: dict[str, ScoreResult] = {}

    for job in jobs:
        per = {}
        best = (None, -1.0, [], [])
        for p in active:
            raw, reasons, gaps = rule_score(job, p, global_exclude)
            weighted = raw * PRIORITY_MULT.get(p.get("priority", "medium"), 0.85)
            per[p["id"]] = round(weighted, 1)
            if weighted > best[1]:
                best = (p["id"], weighted, reasons, gaps)
        kept, delta, failures, hits = F.apply(rules, job)
        pid = best[0]
        cv = next((p.get("cv_id") for p in active if p["id"] == pid and p.get("cv_id")), None) or DEFAULT_CV_GROUPS.get(pid)
        results[job.fingerprint] = ScoreResult(
            job_id=job.fingerprint, score=round(best[1], 1), base_score=round(best[1], 1), profile_id=pid,
            profile_scores=per, cv_id=cv, reasons=best[2] + hits, gaps=best[3],
            hard_filtered=not kept, filtered_by=failures)
        results[job.fingerprint]._delta = delta  # type: ignore[attr-defined]

    # LLM refinement for kept, promising jobs
    if gateway and gateway.available:
        candidates = [j for j in jobs if not results[j.fingerprint].hard_filtered
                      and results[j.fingerprint].base_score >= LLM_THRESHOLD]
        prof_desc = json.dumps([{"id": p["id"], "name": p["name"]} for p in active])
        for i in range(0, len(candidates), LLM_BATCH):
            batch = candidates[i:i + LLM_BATCH]
            try:
                data, used = gateway.complete_json(LLM_SYSTEM.format(profiles=prof_desc),
                                                   json.dumps([_llm_payload(j) for j in batch], ensure_ascii=False),
                                                   task="score")
            except LLMUnavailable:
                break                                # keep rule scores for the rest
            for item in data if isinstance(data, list) else []:
                r = results.get(str(item.get("id")))
                if not r:
                    continue
                try:
                    fit = float(item.get("fit", r.base_score))
                except (TypeError, ValueError):
                    continue
                r.score = round(0.4 * r.base_score + 0.6 * max(0, min(100, fit)), 1)
                if item.get("profile") in r.profile_scores:
                    r.profile_id = item["profile"]
                    r.cv_id = next((p.get("cv_id") for p in active if p["id"] == r.profile_id and p.get("cv_id")),
                                   None) or DEFAULT_CV_GROUPS.get(r.profile_id)
                r.reasons = [str(x) for x in item.get("reasons", [])][:3] + [x for x in r.reasons if "(" in x and x.endswith(")")]
                r.gaps = [str(x) for x in item.get("gaps", [])][:3]
                r.llm_used = used

    for r in results.values():
        r.pre_filter_score = r.score
        r.score = round(max(0.0, min(100.0, r.score + getattr(r, "_delta", 0.0))), 1)
    return list(results.values())


def job_from_row(row: dict) -> Job:
    j = Job(source_id=(row.get("tags") or ["db"])[0], external_id=row["id"], title=row.get("title") or "",
            company=row.get("company") or "", location=row.get("location") or "",
            description=row.get("description") or "", url=row.get("url") or "")
    for k in ("country", "region", "remote", "contract", "seniority", "language", "salary_min", "salary_max"):
        setattr(j, k, row.get(k) if row.get(k) is not None else getattr(j, k))
    j.canonical_id = row["id"]
    return j


def reapply_filters(board_rows: list[dict], rules: list[F.Rule]) -> list[dict]:
    """Re-run hard/soft filters on stored jobs without calling the LLM again."""
    out = []
    for row in board_rows:
        if row.get("score") is None:
            continue
        job = job_from_row(row)
        kept, delta, failures, hits = F.apply(rules, job)
        pre = row.get("pre_filter_score")
        pre = row["score"] if pre is None else pre
        reasons = [x for x in (row.get("reasons") or []) if not (x.endswith(")") and "(" in x and x.split("(")[-1][:1] in "+-")]
        out.append({"job_id": row["id"], "hard_filtered": not kept, "filtered_by": failures,
                    "score": round(max(0.0, min(100.0, pre + delta)), 1), "pre_filter_score": pre,
                    "reasons": reasons + hits})
    return out
