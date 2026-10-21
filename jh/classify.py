"""Rule-based classification: fast, free and deterministic. The LLM is only used later for fit."""
from __future__ import annotations

import re

from .models import Job, norm

# ---------- language ----------
_EN = set("the and to of for with you we our is are in on as will be this experience team work".split())
_FR = set("le la les et des pour avec vous nous notre est sont dans sur une un du au aux poste équipe".split())
_DE = set("und der die das mit für wir sie ist sind ihre unser erfahrung aufgaben".split())
_ES = set("el los las y con para nosotros es son una del experiencia equipo".split())
_ARABIC = re.compile(r"[؀-ۿ]")


def detect_language(text: str) -> str:
    if not text:
        return "other"
    sample = text[:3000]
    arabic_chars = len(_ARABIC.findall(sample))
    if arabic_chars > 0.2 * max(1, len(re.sub(r"\s", "", sample))):
        return "ar"
    words = re.findall(r"[a-zA-Zàâçéèêëîïôûùüÿœäöß]+", sample.lower())
    if not words:
        return "other"
    counts = {
        "en": sum(w in _EN for w in words),
        "fr": sum(w in _FR for w in words),
        "de": sum(w in _DE for w in words),
        "es": sum(w in _ES for w in words),
    }
    best = max(counts, key=counts.get)
    if counts[best] < 2:
        return "en" if len(words) < 15 else "other"
    return best if best in ("en", "fr") else "other"


# ---------- location ----------
COUNTRY_ALIASES = {
    "tunisia": ["tunisia", "tunisie", "tunis", "sfax", "sousse", "bizerte", "ariana", "monastir", "nabeul", "تونس"],
    "france": ["france", "paris", "lyon", "marseille", "toulouse", "montpellier", "grenoble", "nantes", "lille", "bordeaux", "saclay"],
    "germany": ["germany", "deutschland", "berlin", "munich", "münchen", "hamburg", "frankfurt", "heidelberg", "cologne"],
    "united kingdom": ["united kingdom", "uk", "england", "london", "cambridge", "oxford", "manchester", "edinburgh", "scotland"],
    "belgium": ["belgium", "belgique", "brussels", "bruxelles", "mons", "ghent", "leuven", "liège"],
    "netherlands": ["netherlands", "amsterdam", "rotterdam", "utrecht", "delft", "eindhoven"],
    "switzerland": ["switzerland", "suisse", "zurich", "zürich", "geneva", "genève", "lausanne", "basel"],
    "spain": ["spain", "españa", "madrid", "barcelona", "valencia"],
    "italy": ["italy", "italia", "milan", "rome", "turin"],
    "portugal": ["portugal", "lisbon", "porto"],
    "austria": ["austria", "vienna", "wien"],
    "poland": ["poland", "warsaw", "krakow"],
    "ireland": ["ireland", "dublin"],
    "sweden": ["sweden", "stockholm"],
    "denmark": ["denmark", "copenhagen"],
    "luxembourg": ["luxembourg"],
    "morocco": ["morocco", "maroc", "casablanca", "rabat"],
    "egypt": ["egypt", "cairo"],
    "uae": ["uae", "united arab emirates", "dubai", "abu dhabi"],
    "saudi arabia": ["saudi", "riyadh", "jeddah"],
    "qatar": ["qatar", "doha"],
    "united states": ["united states", "usa", "u s", "new york", "san francisco", "boston", "seattle", "california", "remote us"],
    "canada": ["canada", "toronto", "montreal", "montréal", "vancouver"],
}
EUROPE = {"france", "germany", "united kingdom", "belgium", "netherlands", "switzerland", "spain", "italy",
          "portugal", "austria", "poland", "ireland", "sweden", "denmark", "luxembourg"}
MENA = {"morocco", "egypt", "uae", "saudi arabia", "qatar"}
NORTH_AMERICA = {"united states", "canada"}
ISO = {"gb": "united kingdom", "fr": "france", "de": "germany", "nl": "netherlands", "be": "belgium",
       "es": "spain", "it": "italy", "ch": "switzerland", "at": "austria", "pl": "poland", "tn": "tunisia",
       "us": "united states", "ca": "canada"}


def detect_country(location: str, hint: str | None = None) -> str:
    if hint:
        h = hint.lower()
        return ISO.get(h, h)
    loc = " " + norm(location) + " "
    for country, aliases in COUNTRY_ALIASES.items():
        for a in aliases:
            if f" {norm(a)} " in loc:
                return country
    return ""


def region_of(country: str, remote: str, location: str = "") -> str:
    if country == "tunisia":
        return "tunisia"
    if country in EUROPE:
        return "europe"
    if country in MENA:
        return "mena"
    if country in NORTH_AMERICA:
        return "north_america"
    loc = " " + norm(location) + " "
    if not country and re.search(r" (europe|eu|emea|european union) ", loc):
        return "europe"
    if not country and re.search(r" (mena|middle east|north africa|africa) ", loc):
        return "mena"
    if remote == "remote" and not country:
        return "remote_global"
    return "rest_of_world" if country else "unknown"


# ---------- remote / contract / seniority ----------
_REMOTE = re.compile(r"\b(fully remote|100% remote|remote[- ]first|work from anywhere|remote|télétravail complet|full remote|à distance)\b")
_HYBRID = re.compile(r"\b(hybrid|hybride|télétravail partiel|partially remote)\b")

CONTRACT_PATTERNS = [
    ("phd", r"\b(phd|ph d|doctoral|doctorate|thèse|these|doctorant|cifre)\b"),
    ("internship", r"\b(intern|internship|stage|stagiaire|praktikum|werkstudent|working student|pfe|alternance|apprenti)\b"),
    ("freelance", r"\b(freelance|contractor|consultant independent|indépendant|consultancy|b2b contract)\b"),
    ("fixed_term", r"\b(fixed[- ]term|temporary|cdd|contrat à durée déterminée|\d+[- ]month contract|befristet)\b"),
    ("full_time", r"\b(full[- ]?time|permanent|cdi|unbefristet|vollzeit)\b"),
]

_SENIOR = re.compile(r"\b(senior|sr|lead|principal|staff|head of|director|manager|architect|chief|vp)\b")
_JUNIOR = re.compile(r"\b(junior|jr|graduate|entry level|entry-level|débutant|jeune diplômé|new grad|early career|associate)\b")
_YEARS = re.compile(r"(\d{1,2})\s*\+?\s*(?:years|yrs|ans|jahre)")


def classify_contract(job: Job) -> str:
    if job.contract_hint:
        h = norm(re.sub(r"(?<=[a-z])(?=[A-Z])|_", " ", job.contract_hint))
        for name, pat in CONTRACT_PATTERNS:
            if re.search(pat, h):
                return name
    title = norm(job.title)
    for name, pat in CONTRACT_PATTERNS:          # title is the strongest signal
        if re.search(pat, title):
            return name
    desc = norm(job.description[:4000])
    for name, pat in CONTRACT_PATTERNS:
        if re.search(pat, desc):
            return name
    return "unknown"


def classify_seniority(job: Job) -> str:
    title = norm(job.title)
    if _SENIOR.search(title):
        return "senior"
    if _JUNIOR.search(title) or re.search(r"\b(intern|stage|phd|stagiaire|thèse)\b", title):
        return "entry" if re.search(r"\b(intern|stage|phd|graduate|stagiaire|thèse|entry)\b", title) else "junior"
    years = [int(y) for y in _YEARS.findall(job.description.lower()[:6000]) if int(y) < 30]
    if years:
        m = min(years)
        if m >= 5:
            return "senior"
        if m >= 3:
            return "mid"
        return "junior"
    return "unknown"


def classify_remote(job: Job) -> str:
    if job.remote_hint is True:
        return "remote"
    loc = norm(job.location)
    if _REMOTE.search(loc):
        return "remote"
    if _HYBRID.search(loc):
        return "hybrid"
    title = norm(job.title)
    if _REMOTE.search(title):
        return "remote"
    desc = norm(job.description[:5000])
    if _HYBRID.search(desc):
        return "hybrid"
    if re.search(r"\b(fully remote|100 remote|remote first|work from anywhere|full remote)\b", desc):
        return "remote"
    return "onsite" if job.location else "unknown"


def classify(job: Job) -> Job:
    job.language = detect_language(job.text)
    job.remote = classify_remote(job)
    job.country = detect_country(job.location, job.country_hint)
    job.region = region_of(job.country, job.remote, job.location)
    job.contract = classify_contract(job)
    job.seniority = classify_seniority(job)
    return job
