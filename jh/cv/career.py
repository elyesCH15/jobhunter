"""Career database loader (career/career.yaml) with id lookups.

Personal details live in career/personal.yaml and are NOT read here — only jh/cv/render.py reads them.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Optional

import yaml

ROOT = Path(__file__).resolve().parent.parent.parent
CAREER_FILE = ROOT / "career" / "career.yaml"
CAREER_PATH = "career/career.yaml"            # path inside the Git repo

# CV section key → top-level list in career.yaml
ENTRY_SECTIONS = {"experience": "experiences", "projects": "projects", "education": "education",
                  "activities": "activities"}
LANGS = ("en", "fr")


def tx(value, lang: str) -> str:
    """Text of a {en, fr} dict in `lang`, falling back to English; plain strings pass through."""
    if value is None:
        return ""
    if isinstance(value, dict):
        return str(value.get(lang) or value.get("en") or "").strip()
    return str(value).strip()


def has_lang(value, lang: str) -> bool:
    """True when `value` has its own text in `lang` (no English fallback needed)."""
    if isinstance(value, dict):
        return bool(value.get(lang))
    return lang == "en" and bool(value)


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")[:40]


class Career:
    def __init__(self, data: dict, text: Optional[str] = None):
        self.data = data or {}
        self.text = text
        self.digest = hashlib.sha1((text or repr(self.data)).encode("utf-8")).hexdigest()[:12]
        self._seen: set[str] = set()
        self._entries: dict[str, tuple[str, dict]] = {}       # id → (section, entry)
        self._bullets: dict[str, tuple[dict, dict]] = {}      # id → (bullet, entry)
        for section, key in ENTRY_SECTIONS.items():
            for e in self.data.get(key) or []:
                self._add_id(e["id"])
                self._entries[e["id"]] = (section, e)
                for b in self.bullets(e):
                    self._add_id(b["id"])
                    self._bullets[b["id"]] = (b, e)
        for key in ("headlines", "summaries"):
            for h in self.data.get(key) or []:
                self._add_id(h["id"])
        self.skills = {s["id"]: s for s in self.data.get("skills") or []}
        self.languages = {lg["id"]: lg for lg in self.data.get("languages") or []}

    def _add_id(self, i: str):
        if i in self._seen:
            raise ValueError(f"career.yaml: duplicate id {i!r}")
        self._seen.add(i)

    # ---- loading ----
    @classmethod
    def from_text(cls, text: str) -> "Career":
        return cls(yaml.safe_load(text) or {}, text)

    @classmethod
    def load(cls, path: Path = CAREER_FILE) -> "Career":
        return cls.from_text(Path(path).read_text(encoding="utf-8"))

    # ---- lookups ----
    def entries(self, section: str) -> list[dict]:
        return list(self.data.get(ENTRY_SECTIONS[section]) or [])

    def entry(self, entry_id: str) -> Optional[dict]:
        hit = self._entries.get(entry_id)
        return hit[1] if hit else None

    def section_of(self, entry_id: str) -> Optional[str]:
        hit = self._entries.get(entry_id)
        return hit[0] if hit else None

    def bullet(self, bullet_id: str) -> Optional[dict]:
        hit = self._bullets.get(bullet_id)
        return hit[0] if hit else None

    def bullet_entry(self, bullet_id: str) -> Optional[dict]:
        hit = self._bullets.get(bullet_id)
        return hit[1] if hit else None

    @staticmethod
    def bullets(entry: dict) -> list[dict]:
        """An entry's bullets. Education coursework is exposed as bullets with ids `cw_<slug>`."""
        if "coursework" in entry:
            out = []
            for group, courses in (entry.get("coursework") or {}).items():
                for c in courses or []:
                    out.append({**c, "id": f"cw_{slug(tx(c, 'en'))}", "group": group,
                                "default": bool(c.get("cv")), "kind": "course"})
            return out
        return list(entry.get("bullets") or [])

    def variants(self, bullet_id: str) -> list[str]:
        """Ids of the bullets worded as the same point (via `variant_of`), the root first."""
        b, e = self._bullets.get(bullet_id, (None, None))
        if not b:
            return [bullet_id]
        root = b.get("variant_of") or b["id"]
        return [x["id"] for x in self.bullets(e) if x["id"] == root or x.get("variant_of") == root]

    @property
    def headlines(self) -> list[dict]:
        return list(self.data.get("headlines") or [])

    @property
    def summaries(self) -> list[dict]:
        return list(self.data.get("summaries") or [])

    def headline(self, hid: Optional[str]) -> Optional[dict]:
        return next((h for h in self.headlines if h["id"] == hid), None)

    def summary(self, sid: Optional[str]) -> Optional[dict]:
        return next((s for s in self.summaries if s["id"] == sid), None)

    @staticmethod
    def degree_variants(entry: dict) -> list[dict]:
        return list(entry.get("degree_variants") or [])

    def skill_name(self, skill_id: str, lang: str) -> str:
        s = self.skills.get(skill_id) or {}
        return str(s.get(lang) if lang != "en" and s.get(lang) else s.get("name", skill_id))
