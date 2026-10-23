"""Document → Typst source → PDF / PNG, compiled in-process with the `typst` Python package.

This is the ONLY module that reads career/personal.yaml: name, phone, email and links are inserted
here, at render time, and never reach the spec or anything an AI step sees.

All CV text goes into the Typst source as string literals ("…"), never as markup, so characters
Typst treats specially in markup (# $ * _ ` < > @ [ ] \\ / = - + ~) print as-is: see `lit()`.
"""
from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml

from .career import ROOT, Career, tx
from .resolve import Doc, resolve
from .spec import DEFAULT_TEMPLATE, TEMPLATES, CVSpec

HERE = Path(__file__).resolve().parent
TEMPLATE_DIR = HERE / "templates"
FONT_DIR = HERE / "fonts"
PERSONAL_FILE = ROOT / "career" / "personal.yaml"
PREVIEW_PPI = 110

_lock = threading.Lock()
_compiler = None


def load_personal(path: Path = PERSONAL_FILE) -> dict:
    try:
        return yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    except FileNotFoundError:
        return {}


# ---------------- Typst source ----------------
_ESCAPES = {"\\": "\\\\", '"': '\\"', "\n": "\\n", "\r": "", "\t": " "}


def typo(s: str) -> str:
    """Small typographic fixes that don't change the words: apostrophe between letters (l'école → l’école),
    no line break inside "35 %" / "163 840" or before French ; : ! ? »."""
    s = re.sub(r"(?<=\w)'(?=\w)", "’", s)
    s = re.sub(r"(?<=\d) (?=%|\d{3}\b)", " ", s)
    return re.sub(r" (?=[;:!?»])", " ", s)


def lit(s, typographic: bool = True) -> str:
    """A Typst string literal for arbitrary text. Inside "…" only \\ and " (and line breaks) need
    escaping; markup characters stay literal text."""
    s = typo(str(s or "")) if typographic else str(s or "")
    return '"' + "".join(_ESCAPES.get(ch, ch) if ch in _ESCAPES or ord(ch) >= 32 else " " for ch in s) + '"'


def contacts(personal: dict, lang: str) -> list[tuple[str, Optional[str]]]:
    """(visible text, link target) pairs. Links are printed as their own text so an ATS reads them."""
    out: list[tuple[str, Optional[str]]] = []
    loc = tx(personal.get("location"), lang)
    if loc:
        out.append((loc, None))
    if personal.get("phone"):
        out.append((str(personal["phone"]), "tel:" + re.sub(r"[^\d+]", "", str(personal["phone"]))))
    if personal.get("email"):
        out.append((str(personal["email"]), "mailto:" + str(personal["email"])))
    for key in ("linkedin", "github", "website"):
        url = str((personal.get("links") or {}).get(key) or "").strip()
        if url:
            bare = re.sub(r"^https?://", "", url).rstrip("/")
            out.append((bare, "https://" + bare))
    return out


def typst_source(doc: Doc, personal: Optional[dict] = None) -> str:
    template = doc.template if doc.template in TEMPLATES else DEFAULT_TEMPLATE
    tpl = (TEMPLATE_DIR / f"{template}.typ").read_text(encoding="utf-8")
    personal = personal or {}
    name = str(personal.get("name") or "")
    title = f"{name} — CV" if name else "CV"
    pairs = ", ".join(f"({lit(t, False)}, {lit(u, False) if u else 'none'})" for t, u in contacts(personal, doc.language))
    body = [tpl, "",
            f"#show: cv-setup.with(lang: {lit(doc.language)}, title: {lit(title)}, author: {lit(name)})",
            f"#cv-header({lit(name)}, {lit(doc.headline)}, ({pairs}{',' if pairs else ''}))"]
    for s in doc.sections:
        body.append(f"#cv-section({lit(s.heading)})")
        if s.text:
            body.append(f"#cv-para({lit(s.text)})")
        for label, names in s.lines:
            body.append(f"#cv-kv({lit(label)}, {lit(names)})")
        for e in s.entries:
            body.append(f"#cv-entry(title: {lit(e.title)}, org: {lit(e.org)}, location: {lit(e.location)}, "
                        f"dates: {lit(e.dates)}, url: {lit(e.link)})")
            if e.line:
                body.append(f"#cv-line({lit(e.line)})")
            if e.bullets:
                body.append("#cv-bullets((" + "".join(f"{lit(b)}, " for b in e.bullets) + "))")
    return "\n".join(body) + "\n"


# ---------------- compile ----------------
def compiler():
    """One shared compiler with the bundled fonts only (system fonts ignored: same output locally and on
    Streamlit Cloud, and no multi-second system font scan)."""
    global _compiler
    if _compiler is None:
        import typst
        _compiler = typst.Compiler(ignore_system_fonts=True, font_paths=[str(FONT_DIR)])
    return _compiler


def compile_typst(source: str, fmt: str = "pdf", ppi: Optional[int] = None):
    """PDF → bytes; PNG → list of page images (bytes)."""
    with _lock:
        out = compiler().compile(source.encode("utf-8"), format=fmt, ppi=ppi) if fmt == "png" else \
            compiler().compile(source.encode("utf-8"), format=fmt)
    if fmt == "png" and isinstance(out, (bytes, bytearray)):
        return [bytes(out)]
    return out


def pdf_page_count(pdf: bytes) -> int:
    """Page count from the PDF's page tree (/Type /Pages … /Count n)."""
    counts = [int(m) for m in re.findall(rb"/Type\s*/Pages\b[^>]*?/Count\s+(\d+)", pdf)]
    counts += [int(m) for m in re.findall(rb"/Count\s+(\d+)[^>]*?/Type\s*/Pages\b", pdf)]
    if counts:
        return max(counts)
    return len(re.findall(rb"/Type\s*/Page\b", pdf))


@dataclass
class Rendered:
    source: str
    pdf: bytes
    pages: int
    png: list = field(default_factory=list)
    seconds: float = 0.0


def render_source(source: str, png: bool = True, ppi: int = PREVIEW_PPI) -> Rendered:
    t = time.perf_counter()
    pdf = compile_typst(source, "pdf")
    images = compile_typst(source, "png", ppi=ppi) if png else []
    pages = len(images) if png else pdf_page_count(pdf)
    return Rendered(source=source, pdf=pdf, pages=pages, png=images, seconds=time.perf_counter() - t)


def cv_source(spec: CVSpec, career: Career, personal: Optional[dict] = None) -> str:
    """Full Typst source of a CV, personal header included (read here, from career/personal.yaml)."""
    personal = load_personal() if personal is None else personal
    return typst_source(resolve(spec, career), personal)


def render(spec: CVSpec, career: Career, personal: Optional[dict] = None, png: bool = True,
           ppi: int = PREVIEW_PPI) -> Rendered:
    return render_source(cv_source(spec, career, personal), png=png, ppi=ppi)
