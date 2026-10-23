from __future__ import annotations

import html
import re
from datetime import datetime, timezone
from typing import Any, Callable, Optional

import requests

from ..models import norm

UA = {"User-Agent": "JobHunter/1.0 (personal job search; contact via GitHub)", "Accept": "application/json"}


class Http:
    """Thin wrapper so tests can inject canned responses."""

    def __init__(self, get: Callable = requests.get, post: Callable = requests.post):
        self._get, self._post = get, post

    def get_json(self, url: str, params: Optional[dict] = None) -> Any:
        r = self._get(url, params=params, headers=UA, timeout=45)
        r.raise_for_status()
        return r.json()

    def post_json(self, url: str, payload: dict) -> Any:
        r = self._post(url, json=payload, headers=UA, timeout=45)
        r.raise_for_status()
        return r.json()


_TAG = re.compile(r"<[^>]+>")


def strip_html(s: Optional[str]) -> str:
    if not s:
        return ""
    s = html.unescape(html.unescape(s))                      # Greenhouse double-escapes
    s = re.sub(r"<\s*(br|/p|/li|/h\d|/div)\s*/?>", "\n", s, flags=re.I)
    s = _TAG.sub(" ", s)
    s = re.sub(r"[ \t\xa0]+", " ", s)
    return re.sub(r"\n\s*\n+", "\n", s).strip()


def to_iso(value: Any) -> Optional[str]:
    if value in (None, ""):
        return None
    try:
        if isinstance(value, (int, float)):
            ts = value / 1000 if value > 1e11 else value         # ms or s
            return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
        s = str(value).replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).isoformat()
    except (ValueError, OSError, OverflowError):
        return None


# Title phrases that indicate an ML / data / research-engineering role. Matched on whole words
# after norm(); a trailing "*" means "word starting with" (biolog* → biology, biologist).
ROLE_PHRASES = (
    "machine learning", "deep learning", "ml engineer*", "ml scientist*", "ml research*", "mlops",
    "ai engineer*", "ai scientist*", "ai research*", "applied ai", "generative ai", "data scientist*",
    "data science", "computer vision", "nlp", "natural language processing", "llm*",
    "bioinformatic*", "computational biolog*", "computational chemist*", "cheminformatic*", "protein design",
    "geospatial", "remote sensing", "earth observation", "research engineer*", "research scientist*",
    "applied scientist*", "phd", "doctoral",
)
# Early-career titles: kept on every board (strict ones too) when they look ML / research related.
EARLY_CAREER = ("intern", "interns", "internship*", "fellow", "fellows", "fellowship*", "residency", "resident*",
                "graduate", "new grad", "early career", "junior", "stage", "stagiaire", "alternance", "apprenti*")
EARLY_ROLE_WORDS = ("ai", "ml", "research*", "data", "scien*", "computational", "bioinformatic*", "vision",
                    "nlp", "llm*", "geospatial", "remote sensing", "climate", "phd", "doctoral")
# Strict boards drop these unless the title is early-career.
SENIOR_WORDS = ("senior", "sr", "staff", "lead", "principal", "manager", "head", "director", "architect",
                "chief", "vp")
# Body-keyword fallback: at least one matched keyword must be a technical ML term…
TECH_TERMS = {"machine learning", "deep learning", "pytorch", "tensorflow", "llm", "rag", "transformers",
              "fine tuning", "mlops", "nlp", "hugging face", "computer vision", "diffusion", "stable diffusion",
              "segmentation", "object detection", "vision transformer", "gan", "protein language model", "esm",
              "alphafold", "structure prediction", "bioinformatics", "computational biology", "cheminformatics",
              "remote sensing", "earth observation", "geospatial", "google earth engine", "distributed training",
              "cuda", "generative ai"}
# …and the title must not be an obviously non-technical job.
NON_ROLE_TITLE = ("account executive", "account manager", "sales*", "business development", "marketing",
                  "recruit*", "talent", "counsel", "legal", "paralegal", "finance", "financial", "accountant",
                  "assistant", "coordinator", "office", "customer success", "partnerships", "designer",
                  "communications", "people", "hr", "manager")


def _phrase_re(phrases) -> re.Pattern:
    parts = [re.escape(p[:-1]) + r"\w*" if p.endswith("*") else re.escape(p) for p in phrases if p]
    return re.compile(r"\b(?:" + "|".join(sorted(parts, key=len, reverse=True)) + r")\b")


class RelevanceGate:
    """Feeds and company boards return *everything*; keep only postings that plausibly
    match at least one profile before they reach the database.

    strict=True (big boards such as greenhouse:anthropic, listed under `strict:` in
    sources.yaml): the title alone must name a target role, and senior / manager titles are
    dropped unless they are early-career (intern, fellow, resident, graduate, junior…)."""

    def __init__(self, profiles: list[dict], strict: bool = False):
        self.strict = strict
        self.titles = {norm(t) for p in profiles for t in p.get("titles", [])} - {""}
        self.keywords = {norm(k) for p in profiles for k in p.get("keywords", [])} - {""}
        self.tech = {norm(k) for k in TECH_TERMS}
        self.role_re = _phrase_re(sorted(self.titles) + list(ROLE_PHRASES))
        self.early_re = _phrase_re(EARLY_CAREER)
        self.early_role_re = _phrase_re(EARLY_ROLE_WORDS)
        self.senior_re = _phrase_re(SENIOR_WORDS)
        self.non_role_re = _phrase_re(NON_ROLE_TITLE)

    def title_match(self, title: str) -> bool:
        t = norm(title)
        if self.role_re.search(t):
            return True
        return bool(self.early_re.search(t) and self.early_role_re.search(t))

    def __call__(self, title: str, text: str = "") -> bool:
        t = norm(title)
        if self.strict:
            if not self.title_match(t):
                return False
            return bool(self.early_re.search(t)) or not self.senior_re.search(t)
        if self.title_match(t):
            return True
        if self.non_role_re.search(t):
            return False
        body = " " + norm((text or "")[:6000]) + " "
        hits = {k for k in self.keywords if f" {k} " in body}
        return len(hits) >= 3 and bool(hits & self.tech)
