"""Can a graduate take this internship?  Rules: config/internship_rules.yaml (country) + phrases (posting).

Only jobs with contract == "internship" get an `intern_eligibility`:
  graduate_ok · graduate_ok_conditions · student_only · unknown
Other jobs keep None. Order: posting text first (graduate-ok wording beats student-only wording, so
"Pflicht- oder freiwilliges Praktikum" stays open), then the country rule, else unknown.
A guide, not legal advice — the dashboard links each country's official source.
"""
from __future__ import annotations

import re
from functools import lru_cache

from .models import Job, norm

STATUSES = ("graduate_ok", "graduate_ok_conditions", "student_only", "unknown")
LABELS = {"graduate_ok": "Intern: open to graduates",
          "graduate_ok_conditions": "Intern: graduates with permit conditions",
          "student_only": "Intern: students only",
          "unknown": "Intern: eligibility unclear"}

# Matched on norm()-ed text (lowercase, no accents / punctuation), as whole words.
STUDENT_ONLY_PHRASES = [
    "convention de stage", "currently enrolled", "must be enrolled", "enrolled in a", "current student",
    "currently pursuing", "pursuing a degree", "as part of your studies", "in your final year",
    "stage de fin d etudes", "etudiant en", "etudiante en", "etudiant e en", "etudiants en",
    "pflichtpraktikum", "immatrikuliert", "eingeschrieben", "returning to school",
    "werkstudent", "werkstudentin", "working student",      # German student jobs: enrolment required
]
GRADUATE_OK_PHRASES = [
    "recent graduate", "recent graduates", "recently graduated", "graduated within", "graduate internship",
    "post graduate internship", "jeune diplome", "jeunes diplomes", "jeune diplomee", "diplome depuis",
    "diplomee depuis", "absolvent", "absolventin", "absolventen", "nach dem studium", "freiwilliges praktikum",
    "sivp", "traineeship", "graduate programme", "graduate program", "young professional", "young professionals",
]


def _compile(phrases: list[str]) -> list[tuple[str, re.Pattern]]:
    return [(p, re.compile(rf"(?<!\w){re.escape(norm(p))}(?!\w)")) for p in phrases]


_STUDENT = _compile(STUDENT_ONLY_PHRASES)
_GRADUATE = _compile(GRADUATE_OK_PHRASES)


@lru_cache(maxsize=1)
def load_rules() -> dict:
    """{"default": {...}, "countries": {name: {status, note, source, verified}}} from the repo's YAML."""
    from .config import read_yaml                       # late import: config imports nothing from here
    data = read_yaml("internship_rules.yaml")
    return {"default": data.get("default") or {"status": "unknown", "note": "", "source": ""},
            "countries": {str(k).lower(): v for k, v in (data.get("countries") or {}).items()}}


def country_rule(country: str | None, rules: dict | None = None) -> dict:
    """The rule for a country (lowercase name as classify.detect_country returns it), else the default."""
    rules = rules or load_rules()
    rule = rules["countries"].get((country or "").strip().lower())
    if rule and rule.get("status") in STATUSES:
        return rule
    return rules["default"]


def _first_hit(patterns, text: str) -> str | None:
    return next((p for p, rx in patterns if rx.search(text)), None)


def classify_internship(job: Job, rules: dict | None = None) -> tuple[str | None, str | None]:
    """(intern_eligibility, intern_reason) — (None, None) for jobs that aren't internships."""
    if job.contract != "internship":
        return None, None
    text = norm(f"{job.title}\n{job.description[:8000]}")
    grad = _first_hit(_GRADUATE, text)
    if grad:
        return "graduate_ok", f"posting: '{grad}'"
    student = _first_hit(_STUDENT, text)
    if student:
        return "student_only", f"posting: '{student}'"
    if not job.country:
        return "unknown", "country unknown"
    rules = rules or load_rules()
    rule = rules["countries"].get(job.country.strip().lower())
    if not rule or rule.get("status") not in STATUSES:
        return rules["default"].get("status") or "unknown", f"no rule for {job.country}"
    return rule["status"], f"country rule: {job.country}"


def apply_eligibility(job: Job, rules: dict | None = None) -> Job:
    job.intern_eligibility, job.intern_reason = classify_internship(job, rules)
    return job
