from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass, field, asdict
from typing import Any, Optional


def norm(s: Optional[str]) -> str:
    """Lowercase, strip accents and punctuation, collapse spaces."""
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"[^\w\s]", " ", s.lower())
    return re.sub(r"\s+", " ", s).strip()


_COMPANY_SUFFIX = re.compile(r"\b(inc|ltd|llc|gmbh|sas|sarl|sa|ag|bv|plc|limited|corp|co)\b")
_TITLE_NOISE = re.compile(r"\b(m f d|f m d|h f|f h|m w d|w m d|all genders)\b")


def norm_company(s: Optional[str]) -> str:
    return re.sub(r"\s+", " ", _COMPANY_SUFFIX.sub("", norm((s or "").replace(".", "")))).strip()


def norm_title(s: Optional[str]) -> str:
    return re.sub(r"\s+", " ", _TITLE_NOISE.sub("", norm(s))).strip()


@dataclass
class Job:
    source_id: str
    external_id: str
    title: str
    company: str = ""
    location: str = ""
    description: str = ""
    url: str = ""
    apply_url: str = ""
    posted_at: Optional[str] = None          # ISO 8601
    deadline: Optional[str] = None           # YYYY-MM-DD
    salary_min: Optional[float] = None
    salary_max: Optional[float] = None
    salary_currency: Optional[str] = None
    remote_hint: Optional[bool] = None       # source says remote
    contract_hint: Optional[str] = None
    country_hint: Optional[str] = None
    raw: dict = field(default_factory=dict)

    # filled by classify()
    country: str = ""
    region: str = "unknown"
    remote: str = "unknown"
    contract: str = "unknown"
    seniority: str = "unknown"
    language: str = "other"
    tags: list = field(default_factory=list)
    canonical_id: Optional[str] = None       # set by dedupe when this matches an existing job

    @property
    def text(self) -> str:
        return f"{self.title}\n{self.description}"

    @property
    def fingerprint(self) -> str:
        if self.canonical_id:
            return self.canonical_id
        key = "|".join([norm_company(self.company), norm_title(self.title), norm(self.location).split(" ")[0] if self.location else ""])
        return hashlib.sha1(key.encode()).hexdigest()[:16]

    def to_row(self) -> dict[str, Any]:
        d = asdict(self)
        row = {k: d[k] for k in (
            "title", "company", "location", "country", "region", "remote", "contract", "seniority",
            "language", "salary_min", "salary_max", "salary_currency", "deadline", "posted_at",
            "url", "apply_url", "description", "tags")}
        row["id"] = self.fingerprint
        row["description"] = (row["description"] or "")[:20000]
        return row
