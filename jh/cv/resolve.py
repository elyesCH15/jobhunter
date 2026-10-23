"""Spec + career → a plain document (sections, entries, bullet texts) in one language.

The document holds no personal details; render.py adds the header. `cv_text_for_llm` is the only
text a later AI step (ATS match, "Generate CV") may receive.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Optional

from .career import Career, tx
from .spec import CVSpec, is_custom

HEADINGS = {
    "en": {"summary": "Summary", "experience": "Experience", "projects": "Projects", "education": "Education",
           "skills": "Skills", "languages": "Languages", "activities": "Activities"},
    "fr": {"summary": "Profil", "experience": "Expérience professionnelle", "projects": "Projets",
           "education": "Formation", "skills": "Compétences", "languages": "Langues", "activities": "Activités"},
}
CATEGORIES = {
    "programming": ("Programming", "Programmation"), "ml": ("Machine learning", "Machine learning"),
    "nlp": ("LLMs & NLP", "LLM & NLP"), "cv": ("Computer vision", "Vision par ordinateur"),
    "genai": ("Generative AI", "IA générative"), "bio": ("Protein & bio ML", "ML pour la biologie"),
    "hpc": ("HPC", "Calcul haute performance"), "geo": ("Geospatial", "Géospatial"),
    "data": ("Data", "Données"), "mlops": ("MLOps & deployment", "MLOps & déploiement"),
    "tools": ("Tools", "Outils"), "research": ("Research methods", "Méthodes de recherche"),
    "soft": ("Communication", "Communication"), "domain": ("Domain knowledge", "Connaissances métier"),
    "maths": ("Mathematics", "Mathématiques"), "networks": ("Networks", "Réseaux"), "other": ("Other", "Autres"),
}
MONTHS = {"en": ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"],
          "fr": ["janv.", "févr.", "mars", "avr.", "mai", "juin", "juil.", "août", "sept.", "oct.", "nov.", "déc."]}
WORDS = {"en": {"present": "Present", "coursework": "Relevant coursework"},
         "fr": {"present": "présent", "coursework": "Cours suivis"}}


def category_label(cat: str, lang: str) -> str:
    en, fr = CATEGORIES.get(cat, (cat.replace("_", " ").capitalize(),) * 2)
    return fr if lang == "fr" else en


def fmt_date(v, lang: str) -> str:
    """2026-02 → 'Feb 2026' / 'févr. 2026'; 2020 → '2020'."""
    if v is None or v == "":
        return ""
    if isinstance(v, (dt.date, dt.datetime)):
        y, m = v.year, v.month
    else:
        s = str(v).strip()
        parts = s.split("-")
        if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
            y, m = int(parts[0]), int(parts[1])
        else:
            return s
    return f"{MONTHS.get(lang, MONTHS['en'])[m - 1]} {y}"


def fmt_range(entry: dict, lang: str) -> str:
    """start – end; `end: null` means ongoing; only one of them → that one."""
    if "date" in entry:
        return fmt_date(entry["date"], lang)
    start, end = fmt_date(entry.get("start"), lang), fmt_date(entry.get("end"), lang)
    if "end" in entry and entry["end"] is None and start:
        end = WORDS.get(lang, WORDS["en"])["present"]
    if start and end and start != end:
        return f"{start} – {end}"
    return start or end


@dataclass
class Entry:
    id: str
    title: str = ""
    org: str = ""
    location: str = ""
    dates: str = ""
    link: str = ""
    bullets: list = field(default_factory=list)
    line: str = ""                      # education: "Relevant coursework: …"


@dataclass
class Section:
    key: str
    heading: str
    text: str = ""                      # summary / languages
    entries: list = field(default_factory=list)
    lines: list = field(default_factory=list)     # skills: [(label, "a, b, c")]


@dataclass
class Doc:
    language: str
    template: str
    headline: str
    sections: list


def bullet_text(spec: CVSpec, career: Career, bid: str, lang: Optional[str] = None) -> str:
    lang = lang or spec.language
    o = spec.override(bid, lang)
    if o is not None:
        return o.strip()
    if is_custom(bid):
        return str((spec.custom.get(bid) or {}).get("text", "")).strip()
    return tx(career.bullet(bid) or {}, lang)


def entry_field(spec: CVSpec, career: Career, entry_id: str, name: str, lang: Optional[str] = None) -> str:
    """Printed value of an entry field (title, org, location, dates, degree, link), override first."""
    lang = lang or spec.language
    o = spec.override(f"{entry_id}.{name}", lang)
    if o is not None:
        return o.strip()
    if is_custom(entry_id):
        return str((spec.custom.get(entry_id) or {}).get(name, "") or "").strip()
    e = career.entry(entry_id) or {}
    if name == "title":
        return tx(e.get("title") or e.get("degree"), lang)
    if name == "degree":
        variants = career.degree_variants(e)
        vid = spec.degree_variants.get(entry_id)
        v = next((x for x in variants if x["id"] == vid), None) or next(
            (x for x in variants if x.get("default")), variants[0] if variants else None)
        return tx(v, lang) if v else tx(e.get("degree"), lang)
    if name == "org":
        return tx(e.get("company") or e.get("organisation") or e.get("school"), lang)
    if name == "location":
        return tx(e.get("location"), lang)
    if name == "dates":
        return fmt_range(e, lang)
    if name == "link":
        code = str((e.get("links") or {}).get("code") or "")
        return code if "." in code else ""          # "private" / true → nothing to print
    return ""


def headline_text(spec: CVSpec, career: Career) -> str:
    if not spec.headline_id:
        return ""
    o = spec.override(spec.headline_id)
    return o.strip() if o is not None else tx(career.headline(spec.headline_id), spec.language)


def summary_text(spec: CVSpec, career: Career) -> str:
    if not spec.summary_id:
        return ""
    o = spec.override(spec.summary_id)
    return o.strip() if o is not None else tx(career.summary(spec.summary_id), spec.language)


def skill_label(spec: CVSpec, career: Career, sid: str) -> tuple[str, str]:
    """(category, printed name) of a selected skill. Levels are never part of it."""
    if is_custom(sid):
        c = spec.custom.get(sid) or {}
        return c.get("category") or "other", str(c.get("name", "")).strip()
    o = spec.override(f"skill:{sid}")
    s = career.skills.get(sid) or {}
    return s.get("category") or "other", (o.strip() if o is not None else career.skill_name(sid, spec.language))


PRINT_GROUP = {"tools": "programming", "genai": "cv", "mlops": "data"}      # printed on one line together
MERGED_LABELS = {"programming": ("Programming & tools", "Programmation & outils"),
                 "cv": ("Computer vision & generative AI", "Vision par ordinateur & IA générative"),
                 "data": ("Data & MLOps", "Données & MLOps")}


def skill_lines(spec: CVSpec, career: Career) -> list[tuple[str, str]]:
    """Selected skills grouped by category (a few related categories share a line), in order of each
    line's first selected skill; within a line, in the order chosen."""
    groups: dict[str, list[str]] = {}
    members: dict[str, set] = {}
    for sid in spec.skills:
        cat, name = skill_label(spec, career, sid)
        if name:
            key = PRINT_GROUP.get(cat, cat)
            groups.setdefault(key, []).append(name)
            members.setdefault(key, set()).add(cat)
    out = []
    for key, names in groups.items():
        cats = members[key]
        if len(cats) > 1 and key in MERGED_LABELS:
            label = MERGED_LABELS[key][1 if spec.language == "fr" else 0]
        else:
            label = category_label(next(iter(cats)), spec.language)
        out.append((label, ", ".join(names)))
    return out


def language_line(spec: CVSpec, career: Career) -> str:
    lang, parts = spec.language, []
    for lid in spec.languages:
        lg = career.languages.get(lid)
        if not lg:
            continue
        name = str(lg.get(lang) or lg.get("en") or lid)
        level = tx(lg.get("level"), lang)
        if lid in spec.certificates and lg.get("certificate"):
            level = f"{level}, {lg['certificate']}" if level else str(lg["certificate"])
        parts.append(f"{name} ({level})" if level else name)
    return ", ".join(parts)


def resolve_entry(spec: CVSpec, career: Career, section: str, item: dict) -> Entry:
    eid, lang = item["id"], spec.language
    e = Entry(id=eid)
    e.org = entry_field(spec, career, eid, "org")
    e.location = entry_field(spec, career, eid, "location")
    e.dates = entry_field(spec, career, eid, "dates")
    e.link = entry_field(spec, career, eid, "link")
    texts = [t for t in (bullet_text(spec, career, b) for b in item.get("bullets", [])) if t]
    if section == "education" and not is_custom(eid):
        e.title = entry_field(spec, career, eid, "degree")
        if texts:
            e.line = f"{WORDS.get(lang, WORDS['en'])['coursework']}: {', '.join(texts)}"
    else:
        e.title = entry_field(spec, career, eid, "title")
        e.bullets = texts
    return e


def resolve(spec: CVSpec, career: Career) -> Doc:
    lang = spec.language if spec.language in HEADINGS else "en"
    heads = HEADINGS[lang]
    sections = []
    for key in spec.sections:
        sec = Section(key=key, heading=heads.get(key, key.capitalize()))
        if key == "summary":
            sec.text = summary_text(spec, career)
            if not sec.text:
                continue
        elif key == "skills":
            sec.lines = skill_lines(spec, career)
            if not sec.lines:
                continue
        elif key == "languages":
            sec.text = language_line(spec, career)
            if not sec.text:
                continue
        elif key in spec.items:
            sec.entries = [resolve_entry(spec, career, key, it) for it in spec.items[key]]
            if not sec.entries:
                continue
        else:
            continue
        sections.append(sec)
    return Doc(language=lang, template=spec.template, headline=headline_text(spec, career), sections=sections)


def doc_text(doc: Doc) -> str:
    """Plain text of a document — what an ATS would read, minus the personal header."""
    out = [doc.headline] if doc.headline else []
    for s in doc.sections:
        out += ["", s.heading.upper()]
        if s.text:
            out.append(s.text)
        for label, names in s.lines:
            out.append(f"{label}: {names}")
        for e in s.entries:
            head = " — ".join(x for x in [e.title, ", ".join(y for y in [e.org, e.location] if y)] if x)
            out.append(f"{head} | {e.dates}" if e.dates else head)
            if e.link:
                out.append(e.link)
            if e.line:
                out.append(e.line)
            out += [f"- {b}" for b in e.bullets]
    return "\n".join(out).strip() + "\n"


def cv_text_for_llm(spec: CVSpec, career: Career) -> str:
    """The CV's content for an AI step. Built from the spec and career.yaml only — personal.yaml
    (name, phone, email, links) is never read on this path."""
    return doc_text(resolve(spec, career))
