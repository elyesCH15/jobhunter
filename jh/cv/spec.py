"""The CV spec — one JSON document describing exactly what a CV contains.

It never holds personal details (name, phone, email, links): those are added by jh/cv/render.py.
Everything here may later be shown to an LLM.

    {
      "schema": 1,
      "job_id": "abc123" | null,          # null = base CV
      "profile_id": "protein_ml" | null,  # profile the defaults were picked for
      "name": "Cradle — protein ML",      # version name
      "language": "en" | "fr",
      "template": "compact" | "classic",
      "max_pages": 1 | 2,                 # page budget (template default); the editor warns when the PDF is longer
      "headline_id": "ml_engineer",
      "summary_id": "sum_general" | null, # null = no summary text
      "sections": ["summary", "experience", "projects", "education", "skills", "languages"],
                                          # visible sections, in print order
      "items": {"experience": [{"id": "euranova", "bullets": ["eur_finetune", "c_1a2b3c"]}], ...},
                                          # selected entries per section, in order, with their selected bullets
                                          # (education bullets = coursework ids cw_<slug>)
      "skills": ["python", "pytorch", "c_9f8e7d"],   # selected skills in order (grouped by category on print)
      "languages": ["ar", "en", "fr"],
      "certificates": ["en"],             # languages whose certificate (e.g. TOEFL) is printed
      "degree_variants": {"insat": "ml_cv"},
      "overrides": {"en": {"eur_sota": "edited text", "euranova.title": "…"}, "fr": {…}},
                                          # CV-only text edits, per language. Keys: bullet / headline /
                                          # summary ids, "<entry>.<field>" (title, org, location, dates,
                                          # degree) or "skill:<id>"
      "custom": {"c_1a2b3c": {"kind": "bullet", "text": "…"},
                 "c_4d5e6f": {"kind": "entry", "section": "projects", "title": "…", "org": "…",
                              "location": "", "dates": "2025", "link": ""},
                 "c_9f8e7d": {"kind": "skill", "name": "JAX", "category": "ml"}},
      "version_id": "uuid" | null,        # cv_versions row this spec was loaded from / saved to
      "created_at": "…", "updated_at": "…"
    }
"""
from __future__ import annotations

import copy
import hashlib
import json
import uuid
from dataclasses import asdict, dataclass, field, fields
from typing import Optional

SCHEMA = 1
SECTIONS = ("summary", "experience", "projects", "education", "skills", "languages", "activities")
ENTRY_SECTIONS = ("experience", "projects", "education", "activities")
TEMPLATES = {"compact": {"label": "Compact — one page", "pages": 1},
             "classic": {"label": "Classic — roomy, up to 2 pages", "pages": 2}}
DEFAULT_TEMPLATE = "compact"
ITEM_FIELDS = ("title", "org", "location", "dates", "degree")
CUSTOM_PREFIX = "c_"


def new_custom_id() -> str:
    return CUSTOM_PREFIX + uuid.uuid4().hex[:8]


def is_custom(i: str) -> bool:
    return i.startswith(CUSTOM_PREFIX)


@dataclass
class CVSpec:
    job_id: Optional[str] = None
    profile_id: Optional[str] = None
    name: str = "Base CV"
    language: str = "en"
    template: str = DEFAULT_TEMPLATE
    max_pages: int = 1
    headline_id: Optional[str] = None
    summary_id: Optional[str] = None
    sections: list = field(default_factory=lambda: ["summary", "experience", "projects", "education",
                                                    "skills", "languages"])
    items: dict = field(default_factory=lambda: {s: [] for s in ENTRY_SECTIONS})
    skills: list = field(default_factory=list)
    languages: list = field(default_factory=list)
    certificates: list = field(default_factory=list)
    degree_variants: dict = field(default_factory=dict)
    overrides: dict = field(default_factory=dict)
    custom: dict = field(default_factory=dict)
    version_id: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    schema: int = SCHEMA

    # ---- (de)serialisation ----
    def to_dict(self) -> dict:
        return copy.deepcopy(asdict(self))

    @classmethod
    def from_dict(cls, d: dict) -> "CVSpec":
        known = {f.name for f in fields(cls)}
        spec = cls(**{k: copy.deepcopy(v) for k, v in (d or {}).items() if k in known})
        for s in ENTRY_SECTIONS:
            spec.items.setdefault(s, [])
        return spec

    def copy(self) -> "CVSpec":
        return CVSpec.from_dict(self.to_dict())

    def content_hash(self) -> str:
        """Hash of what's printed — ignores version bookkeeping (ids, timestamps, name)."""
        d = self.to_dict()
        for k in ("version_id", "created_at", "updated_at", "name"):
            d.pop(k, None)
        return hashlib.sha1(json.dumps(d, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]

    # ---- items ----
    def item(self, section: str, entry_id: str) -> Optional[dict]:
        return next((it for it in self.items.get(section, []) if it["id"] == entry_id), None)

    def selected(self, section: str) -> list[str]:
        return [it["id"] for it in self.items.get(section, [])]

    def set_included(self, section: str, entry_id: str, on: bool, bullets: Optional[list] = None,
                     position: Optional[int] = None):
        lst = self.items.setdefault(section, [])
        cur = self.item(section, entry_id)
        if on and not cur:
            it = {"id": entry_id, "bullets": list(bullets or [])}
            lst.insert(len(lst) if position is None else position, it)
        elif not on and cur:
            lst.remove(cur)

    def set_bullet(self, section: str, entry_id: str, bullet_id: str, on: bool, order: Optional[list] = None):
        """Toggle a bullet. `order` (all bullet ids of the entry, in career order) places a re-enabled
        bullet back where it belongs among the ones already selected."""
        it = self.item(section, entry_id)
        if not it:
            return
        b = it["bullets"]
        if on and bullet_id not in b:
            if order and bullet_id in order:
                rank = {x: i for i, x in enumerate(order)}
                pos = next((i for i, x in enumerate(b) if rank.get(x, 10**6) > rank[bullet_id]), len(b))
                b.insert(pos, bullet_id)
            else:
                b.append(bullet_id)
        elif not on and bullet_id in b:
            b.remove(bullet_id)

    def replace_bullet(self, section: str, entry_id: str, old: str, new: str):
        it = self.item(section, entry_id)
        if it and old in it["bullets"] and new not in it["bullets"]:
            it["bullets"][it["bullets"].index(old)] = new

    # ---- text overrides ----
    def override(self, key: str, lang: Optional[str] = None) -> Optional[str]:
        return (self.overrides.get(lang or self.language) or {}).get(key)

    def set_override(self, key: str, text: Optional[str], lang: Optional[str] = None):
        lang = lang or self.language
        bucket = self.overrides.setdefault(lang, {})
        if text is None:
            bucket.pop(key, None)
        else:
            bucket[key] = text
        if not bucket:
            self.overrides.pop(lang, None)

    # ---- custom content ----
    def add_custom(self, kind: str, **values) -> str:
        cid = new_custom_id()
        self.custom[cid] = {"kind": kind, **values}
        return cid


def move(lst: list, i: int, delta: int) -> bool:
    """Move lst[i] by delta (−1 up, +1 down) in place. Returns False at the edges."""
    j = i + delta
    if not (0 <= i < len(lst) and 0 <= j < len(lst)):
        return False
    lst[i], lst[j] = lst[j], lst[i]
    return True
