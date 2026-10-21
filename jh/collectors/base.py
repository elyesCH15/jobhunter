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


class RelevanceGate:
    """Feeds and company boards return *everything*; keep only postings that plausibly
    match at least one profile before they reach the database."""

    def __init__(self, profiles: list[dict]):
        self.titles = {norm(t) for p in profiles for t in p.get("titles", [])}
        self.keywords = {norm(k) for p in profiles for k in p.get("keywords", [])}
        self.title_words = {"machine learning", "ml", "ai", "data scientist", "deep learning", "research",
                            "computer vision", "nlp", "llm", "bioinformatics", "computational", "geospatial",
                            "remote sensing", "phd", "scientist", "mlops"}

    def __call__(self, title: str, text: str) -> bool:
        t = " " + norm(title) + " "
        if any(f" {x} " in t for x in self.titles | self.title_words):
            return True
        body = " " + norm(text[:6000]) + " "
        return sum(f" {k} " in body for k in self.keywords) >= 3
