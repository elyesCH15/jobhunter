"""CV editor back end: career loader, defaults, rendering (Typst), versions, GitHub save, personal-data guard."""
import base64
import difflib
import io
import json
import re
from pathlib import Path

import pytest
import yaml

from jh.cv import github as GH
from jh.cv import yamledit as YE
from jh.cv.career import CAREER_FILE, Career
from jh.cv.defaults import new_spec
from jh.cv.render import cv_source, lit, load_personal, pdf_page_count, render, render_source, typst_source
from jh.cv.resolve import cv_text_for_llm, fmt_date, fmt_range, resolve
from jh.cv.spec import CVSpec, move
from jh.store import MemoryStore, cv_version_row

ROOT = Path(__file__).resolve().parent.parent
PERSONAL = {"name": "Test Person", "location": {"en": "Tunis, Tunisia", "fr": "Tunis, Tunisie"},
            "phone": "+216 00 000 000", "email": "test@example.com",
            "links": {"linkedin": "linkedin.com/in/test", "github": "github.com/test"}}
TRICKY = ["C++", "AUPRC 0.36 → 0.488 (+35%)", "Python_3 #1 [draft]", "Élève à l'école — déjà été, ça",
          "$x$ *bold* _it_ `code` <tag> @ref \\ backslash \"quoted\" // not a comment = heading - list ~"]


@pytest.fixture(scope="module")
def career():
    return Career.load()


def pdf_text(pdf: bytes) -> str:
    pypdf = pytest.importorskip("pypdf")
    reader = pypdf.PdfReader(io.BytesIO(pdf))
    return "\n".join(p.extract_text() for p in reader.pages)


def flat(s: str) -> str:
    """Compare text regardless of line breaks / special spaces introduced by layout."""
    return re.sub(r"[\s  ]+", " ", s.replace("’", "'")).strip()


# ---------- career loader ----------
def test_career_loader_real_file(career):
    assert career.entry("euranova")["company"] == "Euranova"
    assert career.section_of("vcmi_rag") == "projects"
    assert career.bullet_entry("eur_sota")["id"] == "euranova"
    assert career.variants("eur_finetune_technical") == ["eur_finetune", "eur_finetune_technical"]
    courses = [b["id"] for b in career.bullets(career.entry("insat"))]
    assert "cw_machine_learning" in courses and len(courses) == len(set(courses))
    # comma-containing names are quoted in career.yaml, so they parse whole (no stray `PEFT: null` keys)
    assert career.skills["huggingface"]["name"] == "Hugging Face (Transformers, PEFT, Diffusers)"
    for s in career.skills.values():
        assert None not in s.values(), s
    assert set(career.languages) == {"ar", "en", "fr"}


def test_career_rejects_duplicate_ids():
    with pytest.raises(ValueError, match="duplicate id"):
        Career({"experiences": [{"id": "a", "bullets": [{"id": "x"}]}, {"id": "b", "bullets": [{"id": "x"}]}]})


def test_dates_per_language():
    assert fmt_date("2026-02", "en") == "Feb 2026" and fmt_date("2026-02", "fr") == "févr. 2026"
    assert fmt_range({"start": "2026-02", "end": "2026-07"}, "fr") == "févr. 2026 – juil. 2026"
    assert fmt_range({"start": 2020, "end": 2026}, "en") == "2020 – 2026"
    assert fmt_range({"start": "2025-06", "end": None}, "en") == "Jun 2025 – Present"
    assert fmt_range({"start": "2025-06", "end": None}, "fr") == "juin 2025 – présent"
    assert fmt_range({"end": 2020}, "en") == "2020"
    assert fmt_range({"date": "2026-10"}, "en") == "Oct 2026"


# ---------- defaults ----------
def test_base_cv_takes_default_true(career):
    spec = new_spec(career)
    assert spec.job_id is None and spec.language == "en" and spec.summary_id == "sum_general"
    for section in ("experience", "projects", "activities"):
        for it in spec.items[section]:
            for bid in it["bullets"]:
                assert career.bullet(bid).get("default") is True, bid
    defaults = {b["id"] for e in career.entries("experience") for b in career.bullets(e) if b.get("default")}
    assert {b for it in spec.items["experience"] for b in it["bullets"]} == defaults
    selected = {it["id"] for s in spec.items.values() for it in s}
    assert "bac" not in selected and "timun" not in selected and "jobhunter" not in selected   # default false / to_confirm
    assert all(c.startswith("cw_") and career.bullet(c)["default"] for c in spec.items["education"][0]["bullets"])
    assert "python" in spec.skills and len(spec.skills) == len(set(spec.skills))


def test_protein_job_prefers_protein_bullets(career):
    base = new_spec(career)
    job = {"id": "j1", "title": "ML Engineer, Protein Language Models", "company": "Cradle",
           "profile_id": "protein_ml", "language": "en"}
    spec = new_spec(career, job)
    assert spec.job_id == "j1" and spec.profile_id == "protein_ml" and spec.name == "Cradle — ML Engineer, Protein Language Models"
    assert spec.summary_id == "sum_hpc_research"                       # first summary listing protein_ml
    eur = spec.item("experience", "euranova")["bullets"]
    extra = set(eur) - set(base.item("experience", "euranova")["bullets"])
    assert extra, "protein job should add non-default Euranova bullets"
    words = ("protein", "binding", "esm", "hpc", "fine-tun", "pytorch", "drug")
    assert all(any(w in career.bullet(b)["en"].lower() for w in words) for b in extra)
    assert len(spec.item("experience", "orion")["bullets"]) <= 2      # outside the profile: trimmed
    bio = [s for s in spec.skills if career.skills[s]["category"] == "bio"]
    ml = [s for s in spec.skills if career.skills[s]["category"] == "geo"]
    assert bio and (not ml or spec.skills.index(bio[0]) < spec.skills.index(ml[0]))


def test_job_language_and_profile_choices(career):
    fr = new_spec(career, {"id": "j2", "profile_id": "climate_ai", "language": "fr"})
    assert fr.language == "fr"
    assert [it["id"] for it in fr.items["projects"]][:2] == ["vcmi_rag", "kibale_forest"]   # climate projects first
    hpc = new_spec(career, {"id": "j3", "profile_id": "research_hpc", "language": "de"})
    assert hpc.language == "en" and hpc.headline_id == "hpc_molecular"


# ---------- spec ----------
def test_spec_round_trip_and_helpers(career):
    spec = new_spec(career)
    again = CVSpec.from_dict(json.loads(json.dumps(spec.to_dict())))
    assert again == spec and again.content_hash() == spec.content_hash()
    spec.set_bullet("experience", "euranova", "eur_sota", False)
    assert "eur_sota" not in spec.item("experience", "euranova")["bullets"]
    order = [b["id"] for b in career.bullets(career.entry("euranova"))]
    spec.set_bullet("experience", "euranova", "eur_sota", True, order)
    assert spec.item("experience", "euranova")["bullets"][1] == "eur_sota"      # back in its career position
    lst = [1, 2, 3]
    assert move(lst, 0, 1) and lst == [2, 1, 3] and not move(lst, 0, -1)


# ---------- rendering ----------
def test_overrides_and_custom_items_in_typst_source(career):
    spec = new_spec(career)
    spec.set_override("eur_sota", "Edited for this CV: AUPRC 0.488")
    spec.set_override("euranova.title", "Research Engineer Intern")
    cid = spec.add_custom("bullet", text="A custom bullet about JAX")
    spec.item("experience", "euranova")["bullets"].append(cid)
    eid = spec.add_custom("entry", section="projects", title="Side project", org="", location="", dates="2025", link="github.com/x/y")
    spec.set_included("projects", eid, True, [spec.add_custom("bullet", text="Did a thing")])
    spec.skills.append(spec.add_custom("skill", name="JAX", category="ml"))
    src = typst_source(resolve(spec, career), PERSONAL)
    assert lit("Edited for this CV: AUPRC 0.488") in src
    assert career.bullet("eur_sota")["en"].split(",")[0] not in src
    for text in ("Research Engineer Intern", "A custom bullet about JAX", "Side project", "Did a thing", "github.com/x/y"):
        assert lit(text) in src
    assert "JAX" in src.split("cv-section(\"Skills\")")[1]
    pdf = render(spec, career, PERSONAL, png=False).pdf
    assert pdf.startswith(b"%PDF")


def test_skill_levels_never_printed(career):
    spec = new_spec(career)
    text = cv_text_for_llm(spec, career)
    skills_part = text.split("SKILLS")[1].split("LANGUAGES")[0]
    assert not re.search(r"\b[1-5]\s*/\s*5\b|level", skills_part, re.I)


def test_lit_escapes_only_what_strings_need():
    assert lit('a"b\\c') == '"a\\"b\\\\c"'
    assert lit("line1\nline2") == '"line1\\nline2"'
    assert lit("#$*_`<>@[]") == '"#$*_`<>@[]"'          # markup characters stay literal inside a string


def test_tricky_text_compiles_and_prints_verbatim(career):
    spec = new_spec(career)
    ids = [spec.add_custom("bullet", text=t) for t in TRICKY]
    spec.item("experience", "euranova")["bullets"] = ids
    spec.set_override("ml_engineer", "C++ & Python_3 #1 [draft]")
    r = render(spec, career, PERSONAL, png=False)
    text = flat(pdf_text(r.pdf))
    for t in TRICKY + ["C++ & Python_3 #1 [draft]"]:
        assert flat(t) in text, t


@pytest.mark.parametrize("lang,heading,date", [("en", "Experience", "Feb 2026 – Jul 2026"),
                                               ("fr", "Expérience professionnelle", "févr. 2026 – juil. 2026")])
@pytest.mark.parametrize("template", ["compact", "classic"])
def test_render_en_fr_to_pdf(career, lang, heading, date, template):
    spec = new_spec(career)
    spec.language, spec.template = lang, template
    r = render(spec, career, PERSONAL)
    assert r.pdf.startswith(b"%PDF") and r.pages >= 1 and len(r.png) == r.pages
    assert r.png[0][:8] == b"\x89PNG\r\n\x1a\n"
    assert r.seconds < 5
    text = flat(pdf_text(r.pdf))
    assert heading.upper() in text.upper() and date in text                  # classic prints headings in capitals
    assert "Test Person" in text and "test@example.com" in text
    if lang == "fr":
        assert "FORMATION" in text.upper() and "COMPÉTENCES" in text.upper() and "Ingénieur IA" in text


def test_page_count(career):
    spec = new_spec(career)
    one = render(spec, career, PERSONAL)
    assert one.pages == 1 == pdf_page_count(one.pdf)                     # base CV fits the compact template
    spec.item("experience", "euranova")["bullets"] += [spec.add_custom("bullet", text="Filler line " * 30)
                                                       for _ in range(12)]
    long = render(spec, career, PERSONAL)
    assert long.pages >= 2 and pdf_page_count(long.pdf) == long.pages == len(long.png)


def test_sources_differ_only_when_spec_changes(career):
    spec = new_spec(career)
    a, b = cv_source(spec, career, PERSONAL), cv_source(spec.copy(), career, PERSONAL)
    assert a == b
    spec.set_bullet("experience", "iga", "iga_sql", False)
    assert cv_source(spec, career, PERSONAL) != a
    assert render_source(a, png=False).pages >= 1


# ---------- versions ----------
def test_cv_versions_round_trip_memory_store(career):
    store = MemoryStore()
    store.upsert_jobs([{"id": "job1", "title": "ML Engineer"}])
    base = new_spec(career)
    row = store.save_cv_version(cv_version_row(base.to_dict(), 1))
    job_spec = new_spec(career, {"id": "job1", "profile_id": "protein_ml", "language": "fr"})
    store.save_cv_version(cv_version_row(job_spec.to_dict(), 2))
    assert [v["id"] for v in store.list_cv_versions(None)] == [row["id"]]
    (jv,) = store.list_cv_versions("job1")
    assert jv["language"] == "fr" and jv["page_count"] == 2
    assert CVSpec.from_dict(jv["spec"]) == job_spec
    created = row["created_at"]
    again = base.copy()
    again.version_id, again.name = row["id"], "Base CV v2"
    store.save_cv_version(cv_version_row(again.to_dict(), 1))
    (v,) = store.list_cv_versions(None)
    assert v["name"] == "Base CV v2" and v["created_at"] == created
    store.delete_cv_version(row["id"])
    assert store.list_cv_versions(None) == []


def test_cv_versions_in_snapshot_tables():
    from jh.store import CONFLICT_KEYS, TABLES
    assert "cv_versions" in TABLES and CONFLICT_KEYS["cv_versions"] == "id"
    assert TABLES.index("cv_versions") > TABLES.index("jobs")
    schema = (ROOT / "db" / "schema.sql").read_text()
    mig = (ROOT / "db" / "migrations" / "003_cv_versions.sql").read_text()
    for sql in (schema, mig):
        assert "create table if not exists cv_versions" in sql and "references jobs(id) on delete set null" in sql
    assert "enable row level security" in mig


# ---------- save to database (GitHub) ----------
class FakeResp:
    def __init__(self, status, body=None):
        self.status_code, self._b, self.text = status, body or {}, json.dumps(body or {})

    def json(self):
        return self._b


class FakeGitHub:
    """Contents API stand-in: serves career.yaml, records PUTs; `conflicts` PUTs fail first."""

    def __init__(self, text, conflicts=0):
        self.text, self.sha, self.puts, self.conflicts, self.gets = text, "sha-1", [], conflicts, []

    def get(self, url, params=None, headers=None, timeout=None):
        self.gets.append((url, params))
        assert headers["Authorization"] == "Bearer tok"
        return FakeResp(200, {"content": base64.encodebytes(self.text.encode()).decode(), "sha": self.sha,
                              "encoding": "base64"})

    def put(self, url, json=None, headers=None, timeout=None):
        self.puts.append((url, json))
        if self.conflicts:
            self.conflicts -= 1
            self.sha = "sha-2"                     # someone else committed meanwhile
            return FakeResp(409, {"message": "sha mismatch"})
        assert json["sha"] == self.sha
        self.text = base64.b64decode(json["content"]).decode()
        return FakeResp(200, {"content": {"sha": "sha-new"}})


def changed_lines(old, new):
    return [ln for ln in difflib.unified_diff(old.splitlines(), new.splitlines(), lineterm="", n=0)
            if ln[:1] in "+-" and not ln.startswith(("+++", "---"))]


def test_github_save_changes_only_the_target_bullet():
    old = CAREER_FILE.read_text(encoding="utf-8")
    fake = FakeGitHub(old)
    repo = GH.GitHubRepo("tok", "elyesCH15/jobhunter", "main", session=fake)
    new_text = "Raised the CryptoBench state of the art from 0.36 to 0.488 AUPRC (+35%) — C++ #1 [draft]."
    text, sha = GH.save_career(repo, lambda t: YE.set_text(t, "eur_sota", "en", new_text), "career: update eur_sota (en)")
    assert sha == "sha-new" and len(fake.puts) == 1
    url, payload = fake.puts[0]
    assert url.endswith("/repos/elyesCH15/jobhunter/contents/career/career.yaml")
    assert payload["sha"] == "sha-1" and payload["branch"] == "main" and payload["message"] == "career: update eur_sota (en)"
    assert fake.gets[0][1] == {"ref": "main"}
    written = fake.text
    diff = changed_lines(old, written)
    assert diff and all(ln[1:].startswith(" " * 10) for ln in diff), diff          # only folded-text lines
    assert [ln[1:].strip() for ln in diff if ln.startswith("-")] == career_block(old, "eur_sota", "en")
    old_data, new_data = yaml.safe_load(old), yaml.safe_load(written)
    YE.find_node(old_data, "eur_sota")["en"] = new_text
    assert new_data == old_data                                           # nothing else changed
    old_comments = [ln for ln in old.splitlines() if ln.lstrip().startswith("#")]
    assert old_comments == [ln for ln in written.splitlines() if ln.lstrip().startswith("#")]


def career_block(text, bullet_id, lang):
    """Folded-block lines of a bullet's text in the original file."""
    lines = text.splitlines()
    i = next(n for n, ln in enumerate(lines) if ln.strip() == f"- id: {bullet_id}")
    j = next(n for n in range(i, len(lines)) if lines[n].strip().startswith(f"{lang}:"))
    out = []
    for ln in lines[j + 1:]:
        if len(ln) - len(ln.lstrip()) <= len(lines[j]) - len(lines[j].lstrip()):
            break
        out.append(ln.strip())
    return out


def test_github_save_retries_once_on_conflict():
    fake = FakeGitHub(CAREER_FILE.read_text(encoding="utf-8"), conflicts=1)
    repo = GH.GitHubRepo("tok", "elyesCH15/jobhunter", session=fake)
    GH.save_career(repo, lambda t: YE.set_text(t, "iga_sql", "fr", "Optimisation des bases SQL."), "msg")
    assert [p[1]["sha"] for p in fake.puts] == ["sha-1", "sha-2"]           # re-read, then the fresh sha
    assert yaml.safe_load(fake.text) and "Optimisation des bases SQL." in fake.text


def test_github_never_writes_personal_yaml():
    repo = GH.GitHubRepo("tok", "elyesCH15/jobhunter", session=FakeGitHub(""))
    with pytest.raises(GH.GitHubError, match="refusing"):
        repo.write("career/personal.yaml", "x", "sha", "msg")
    assert GH.from_settings(lambda k: None) is None


def test_yaml_edit_fallback_without_ruamel(monkeypatch):
    old = CAREER_FILE.read_text(encoding="utf-8")
    monkeypatch.setattr(YE, "_ruamel", lambda: None)
    new = YE.set_text(old, "ori_batch_runner", "fr", "Pipeline de tests parallèles : photos réelles, 4 étapes.")
    diff = changed_lines(old, new)
    assert all(ln.startswith("+") for ln in diff) and len(diff) == 2      # fr block inserted, nothing removed
    assert YE.find_node(yaml.safe_load(new), "ori_batch_runner")["fr"].startswith("Pipeline de tests")
    new2 = YE.set_text(old, "eur_sota", "en", "Short: text # with hash")
    assert YE.find_node(yaml.safe_load(new2), "eur_sota")["en"] == "Short: text # with hash"
    assert len(changed_lines(old, new2)) == 3


def test_yaml_add_bullet_and_nested_title():
    old = CAREER_FILE.read_text(encoding="utf-8")
    bid = YE.new_bullet_id(old, "iga", "Wrote reports: weekly")
    new = YE.add_bullet(old, "iga", {"id": bid, "status": "to_confirm", "en": "Wrote reports: weekly #1"})
    entry = YE.find_node(yaml.safe_load(new), "iga")
    assert entry["bullets"][-1] == {"id": bid, "status": "to_confirm", "en": "Wrote reports: weekly #1"}
    assert all(ln.startswith("+") for ln in changed_lines(old, new))
    titled = YE.set_text(old, "orion", "fr", "Stagiaire Vision", field="title")
    assert YE.find_node(yaml.safe_load(titled), "orion")["title"] == {"en": "Computer Vision Intern", "fr": "Stagiaire Vision"}
    with pytest.raises(YE.EditError):
        YE.set_text(old, "does_not_exist", "en", "x")


# ---------- personal data guard ----------
def test_personal_data_never_reaches_spec_or_llm_text(career):
    personal = load_personal()
    # (the GitHub handle is public and also appears in career.yaml project links, so it isn't checked)
    secrets = [personal.get("phone"), personal.get("email"), (personal.get("links") or {}).get("linkedin")]
    secrets = [str(s) for s in secrets if s]
    assert secrets, "career/personal.yaml should have contact details"
    for job in (None, {"id": "j", "profile_id": "protein_ml", "language": "fr"}):
        spec = new_spec(career, job)
        blob = json.dumps(spec.to_dict(), ensure_ascii=False) + cv_text_for_llm(spec, career)
        digits = re.sub(r"\D", "", personal["phone"])
        assert digits not in re.sub(r"\D", "", blob)
        for s in secrets:
            assert s not in blob, s
    # …but the rendered CV does carry them
    assert personal["email"] in cv_source(new_spec(career), career)


def test_only_render_module_reads_personal_yaml():
    offenders = []
    for path in list((ROOT / "jh").rglob("*.py")) + list((ROOT / "dashboard").rglob("*.py")) + list((ROOT / "scripts").rglob("*.py")):
        code = path.read_text(encoding="utf-8")
        if path.name == "render.py" and path.parent.name == "cv":
            continue
        if re.search(r"""["']personal\.yaml["']|load_personal|PERSONAL_FILE""", code):
            offenders.append(str(path.relative_to(ROOT)))
    assert offenders == []


# ---------- dashboard page (Streamlit AppTest, demo mode) ----------
def test_cv_editor_page_smoke(monkeypatch):
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest
    monkeypatch.setenv("JH_DEMO", "1")
    at = AppTest.from_file(str(ROOT / "dashboard" / "app.py"), default_timeout=120)
    at.run()
    at.sidebar.radio[0].set_value("CV editor").run()
    assert not at.exception, at.exception
    preview = at.session_state["cv_preview"]
    assert preview["pages"] >= 1
    spec = at.session_state["cv_spec"]
    assert spec.job_id is None and "iga" in spec.selected("experience")
    before = spec.content_hash()
    box = next(c for c in at.checkbox if c.key and c.key.endswith("_inc_iga"))
    box.uncheck().run()
    assert not at.exception, at.exception
    assert "iga" not in at.session_state["cv_spec"].selected("experience")
    assert at.session_state["cv_preview"]["hash"] != before
    # a demo job further down the pipeline is a target and gets its own tailored CV
    target = next(s for s in at.selectbox if s.key == "cv_target_sel")
    assert len(target.options) > 1
    target.set_value(target.options[1]).run()
    assert not at.exception, at.exception
    assert at.session_state["cv_spec"].job_id is not None
