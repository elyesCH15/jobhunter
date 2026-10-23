"""Starting point for a CV: the base CV, or a CV tailored to a job by its search profile.

Base CV  = every entry / bullet / course marked `default: true` (`cv: true` for courses).
Job CV   = the same, then tilted toward the job's profile:
  - entries whose `profiles` include it are kept even without default bullets, and get extra relevant bullets;
  - among bullet variants (`variant_of`), the wording that matches the profile best is used;
  - other entries keep at most their 2 most relevant default bullets; relevant projects come first;
  - headline, summary and degree wording are the first ones listing the profile;
  - language = the job's language when it is French, else English.
Entries / bullets with `status: to_confirm` are never switched on automatically unless `default: true`.
"""
from __future__ import annotations

from typing import Optional

from .career import Career, tx
from .resolve import CATEGORIES
from .spec import CVSpec, ENTRY_SECTIONS

LISTED_CATEGORIES = ["programming", "ml", "nlp", "cv", "genai", "bio", "hpc", "geo", "data", "mlops", "tools"]
PROFILE_CATEGORIES = {               # categories printed first for each profile (after programming)
    "protein_ml": ["bio", "ml", "hpc"], "research_hpc": ["hpc", "ml", "bio"], "climate_ai": ["geo", "ml", "data", "domain"],
    "genai_cv": ["cv", "genai", "ml", "nlp"], "general_ml": ["ml", "nlp", "mlops"], "mission_driven": ["ml", "geo", "domain", "nlp"],
}
COURSE_GROUPS = ["ai_vision", "data", "maths", "software", "systems_cloud", "signal", "networks_telecom", "management"]
CORE_SKILLS = ["python", "pytorch", "huggingface", "sql", "pandas", "numpy", "git", "linux"]
MIN_LEVEL = 3                         # skills below this level are not picked automatically
MAX_PER_CATEGORY = 4
MAX_COURSES = 6
MAX_EXTRA_BULLETS = 2                 # extra non-default bullets added to a relevant entry
MAX_BULLETS_OTHER = 2                 # default bullets kept on entries outside the job's profile


def load_profile(profile_id: Optional[str]) -> Optional[dict]:
    if not profile_id:
        return None
    from jh.config import profiles_from_yaml
    return next((p for p in profiles_from_yaml()[0] if p.get("id") == profile_id), None)


def _keywords(profile: Optional[dict]) -> list[str]:
    if not profile:
        return []
    return [k.lower() for k in (profile.get("keywords") or []) + (profile.get("titles") or []) if k]


def _haystack(career: Career, b: dict) -> str:
    parts = [tx(b, "en")]
    for sid in b.get("skills") or []:
        s = career.skills.get(sid) or {}
        parts += [str(s.get("name", sid))] + [str(a) for a in s.get("aliases") or []]
    return " ".join(parts).lower()


def bullet_score(career: Career, b: dict, pid: Optional[str], keywords: list[str]) -> int:
    if not pid:
        return 0
    hay = _haystack(career, b)
    return 3 * (pid in (b.get("profiles") or [])) + sum(1 for k in keywords if k in hay)


def _pick_bullets(career: Career, section: str, entry: dict, pid: Optional[str], keywords: list[str]) -> list[str]:
    bullets = career.bullets(entry)
    order = [b["id"] for b in bullets]
    if section == "education":
        return _pick_courses(career, entry, pid, keywords)
    chosen = [b for b in bullets if b.get("default")]
    if not pid:
        return [b["id"] for b in chosen]
    score = {b["id"]: bullet_score(career, b, pid, keywords) for b in bullets}
    relevant = pid in (entry.get("profiles") or [])
    if relevant:
        extras = [b for b in bullets if not b.get("default") and b.get("status") != "to_confirm"
                  and (score[b["id"]] >= 2 or pid in (b.get("profiles") or []))]
        extras.sort(key=lambda b: -score[b["id"]])
        chosen += extras[:MAX_EXTRA_BULLETS]
    elif len(chosen) > MAX_BULLETS_OTHER:
        chosen = sorted(chosen, key=lambda b: -score[b["id"]])[:MAX_BULLETS_OTHER]
    # one wording per point: keep the variant that matches the profile best
    ids, seen = [], set()
    for b in chosen:
        group = career.variants(b["id"])
        root = group[0]
        if root in seen:
            continue
        seen.add(root)
        best = max(group, key=lambda x: (score.get(x, 0), x == b["id"]))
        ids.append(best if (career.bullet(best) or {}).get("status") != "to_confirm" else b["id"])
    return sorted(ids, key=order.index)


def _pick_courses(career: Career, entry: dict, pid: Optional[str], keywords: list[str]) -> list[str]:
    courses = [c for c in career.bullets(entry) if c.get("default")]
    groups = COURSE_GROUPS
    if pid == "climate_ai":
        groups = ["ai_vision", "data", "maths", "signal"] + [g for g in COURSE_GROUPS if g not in ("ai_vision", "data", "maths", "signal")]
    rank = {g: i for i, g in enumerate(groups)}
    courses.sort(key=lambda c: (-bullet_score(career, c, pid, keywords), rank.get(c.get("group"), 99)))
    return [c["id"] for c in courses[:MAX_COURSES]]


def _first_for(options: list[dict], pid: Optional[str]) -> Optional[dict]:
    if pid:
        hit = next((o for o in options if pid in (o.get("profiles") or [])), None)
        if hit:
            return hit
    return next((o for o in options if o.get("default")), options[0] if options else None)


def pick_skills(career: Career, spec: CVSpec, pid: Optional[str], keywords: list[str]) -> list[str]:
    """Skills shown by the selected bullets / courses (level ≥ 3) plus core tools, grouped by category."""
    linked: list[str] = []
    for section in ENTRY_SECTIONS:
        for it in spec.items.get(section, []):
            for bid in it["bullets"]:
                linked += (career.bullet(bid) or {}).get("skills") or []
    candidates = [s for s in dict.fromkeys(CORE_SKILLS + linked) if s in career.skills]
    allowed = set(LISTED_CATEGORIES) | set(PROFILE_CATEGORIES.get(pid or "", []))
    cats = ["programming"] + [c for c in PROFILE_CATEGORIES.get(pid or "", []) if c != "programming"]
    cats = list(dict.fromkeys(cats + LISTED_CATEGORIES + list(CATEGORIES)))
    position = {sid: i for i, sid in enumerate(career.skills)}

    def kscore(sid):
        s = career.skills[sid]
        hay = " ".join([str(s.get("name", ""))] + [str(a) for a in s.get("aliases") or []]).lower()
        return sum(1 for k in keywords if k in hay or hay in k)

    out = []
    for cat in cats:
        if cat not in allowed:
            continue
        in_cat = [s for s in candidates if career.skills[s].get("category") == cat
                  and (career.skills[s].get("level") or 0) >= MIN_LEVEL]
        in_cat.sort(key=lambda s: (-kscore(s), -(career.skills[s].get("level") or 0), position[s]))
        out += in_cat[:MAX_PER_CATEGORY]
    return out


def new_spec(career: Career, job: Optional[dict] = None, profile: Optional[dict] = None) -> CVSpec:
    """Base CV when `job` is None, else a CV tailored to the job's profile."""
    job = job or {}
    pid = (profile or {}).get("id") or job.get("profile_id")
    if pid and not profile:
        profile = load_profile(pid)
    keywords = _keywords(profile) if job else []
    pid = pid if job else None
    lang = "fr" if job.get("language") == "fr" else "en"

    spec = CVSpec(job_id=job.get("id"), profile_id=pid, language=lang,
                  name=" — ".join(x for x in [job.get("company"), job.get("title")] if x) or "Base CV")
    h = _first_for(career.headlines, pid)
    spec.headline_id = h["id"] if h else None
    s = _first_for(career.summaries, pid)
    spec.summary_id = s["id"] if s else None

    for section in ENTRY_SECTIONS:
        items, relevant_first = [], []
        for e in career.entries(section):
            if e.get("default") is False and not (pid and pid in (e.get("profiles") or [])):
                continue
            if e.get("status") == "to_confirm" and not any(b.get("default") for b in career.bullets(e)):
                continue
            bullets = _pick_bullets(career, section, e, pid, keywords)
            has_body = bullets or not career.bullets(e)
            if not has_body:
                continue
            item = {"id": e["id"], "bullets": bullets}
            (relevant_first if section == "projects" and pid in (e.get("profiles") or []) else items).append(item)
        spec.items[section] = relevant_first + items
        for e in career.entries(section):
            variants = career.degree_variants(e)
            if variants:
                v = _first_for(variants, pid)
                spec.degree_variants[e["id"]] = v["id"]

    spec.skills = pick_skills(career, spec, pid, keywords)
    spec.languages = [lid for lid in career.languages]
    spec.certificates = [lid for lid, lg in career.languages.items() if lg.get("show_certificate")]
    spec.sections = ["summary", "experience", "projects", "education", "skills", "languages"]
    if spec.items.get("activities"):
        spec.sections.append("activities")
    return spec
