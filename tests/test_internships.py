"""Internships open to graduates: country rules, text overrides, filters, config sync, store, rescore, dashboard."""
from pathlib import Path

import pytest

from jh import filters as F
from jh.classify import classify
from jh.config import CONFIG_SYNCS, filters_from_yaml, load_config, profiles_from_yaml, sync_config_additions
from jh.internships import STATUSES, classify_internship, country_rule, load_rules
from jh.llm import Gateway
from jh.models import Job
from jh.store import MemoryStore, SupabaseStore

from .test_pipeline import scan  # noqa: F401 — shared fixture

ROOT = Path(__file__).resolve().parent.parent


def intern(title="Machine Learning Intern", location="", description="Work on deep learning models."):
    return classify(Job("t", title + location + description[:20], title, location=location, description=description))


# ---------- country rules ----------
@pytest.mark.parametrize("country,status", [
    ("france", "student_only"), ("germany", "graduate_ok_conditions"), ("spain", "graduate_ok_conditions"),
    ("netherlands", "graduate_ok_conditions"), ("belgium", "graduate_ok_conditions"),
    ("switzerland", "graduate_ok_conditions"), ("tunisia", "graduate_ok"), ("italy", "graduate_ok_conditions"),
    ("luxembourg", "graduate_ok_conditions"), ("ireland", "unknown"), ("denmark", "unknown"),
    ("united kingdom", "unknown"), ("united states", "unknown"), ("canada", "unknown"), ("uae", "unknown"),
    ("morocco", "unknown"), ("", "unknown"), ("Atlantis", "unknown"),
])
def test_country_rule_lookup(country, status):
    assert country_rule(country)["status"] == status


def test_rules_file_is_complete():
    rules = load_rules()
    for name, r in rules["countries"].items():
        assert r["status"] in STATUSES and r.get("note"), name
        assert r.get("verified") == "2026-11", name
        assert not r.get("source") or r["source"].startswith("https://"), name
    for c in ("france", "germany", "spain", "netherlands", "belgium", "switzerland"):
        assert rules["countries"][c]["source"].startswith("https://"), c


def test_country_decides_without_text_signals():
    assert classify_internship(intern(location="Paris, France")) == ("student_only", "country rule: france")
    assert intern(location="Tunis, Tunisie").intern_eligibility == "graduate_ok"
    assert intern(location="Berlin, Germany").intern_eligibility == "graduate_ok_conditions"
    assert classify_internship(intern(location="Remote")) == ("unknown", "country unknown")
    assert intern(location="Casablanca, Maroc").intern_reason == "no rule for morocco"


# ---------- text overrides ----------
def test_convention_de_stage_in_switzerland_is_student_only():
    j = intern("Stage - Data Scientist", "Genève, Suisse",
               "Stage de 6 mois. Une convention de stage avec votre école est obligatoire.")
    assert j.country == "switzerland" and j.contract == "internship"
    assert (j.intern_eligibility, j.intern_reason) == ("student_only", "posting: 'convention de stage'")


def test_freiwilliges_praktikum_in_germany_is_graduate_ok():
    j = intern("Praktikum Machine Learning", "München, Germany",
               "Pflicht- oder freiwilliges Praktikum im Bereich Deep Learning, 6 Monate.")
    assert j.intern_eligibility == "graduate_ok" and "freiwilliges praktikum" in j.intern_reason


def test_recent_graduates_in_france_is_graduate_ok():
    j = intern("ML Intern", "Paris, France", "Recent graduates welcome. Deep learning with PyTorch.")
    assert j.intern_eligibility == "graduate_ok"


@pytest.mark.parametrize("desc", [
    "You must be currently enrolled in a Master's programme.", "Applicants must be enrolled in a university.",
    "Étudiant en dernière année d'école d'ingénieur.", "Stage de fin d'études de 6 mois.",
    "Dies ist ein Pflichtpraktikum.", "Du bist an einer Hochschule immatrikuliert.",
    "Interns returning to school in the fall.", "You are currently pursuing a degree in CS.",
])
def test_student_only_phrases(desc):
    assert intern(location="Tunis, Tunisia", description=desc).intern_eligibility == "student_only"


@pytest.mark.parametrize("desc", [
    "Open to people who graduated within the last 12 months.", "Jeune diplômé(e) d'une école d'ingénieur.",
    "Stage SIVP pour jeunes diplômés.", "Our graduate programme starts in September.",
    "Ideal for a young professional.", "Absolvent (m/w/d) eines MINT-Studiums.",
])
def test_graduate_ok_phrases(desc):
    assert intern(location="Paris, France", description=desc).intern_eligibility == "graduate_ok"


def test_non_internships_untouched():
    for title, desc in (("Machine Learning Engineer", "Full-time. Recent graduates welcome. Must be currently enrolled."),
                        ("PhD position in machine learning", "Doctoral position, currently enrolled students.")):
        j = classify(Job("t", title, title, location="Paris, France", description=desc))
        assert j.contract != "internship" and j.intern_eligibility is None and j.intern_reason is None
        assert j.to_row()["intern_eligibility"] is None


# ---------- filters ----------
def yaml_rules(*names):
    rows = {r["name"]: r for r in filters_from_yaml()}
    return [F.Rule.from_dict(rows[n]) for n in names]


def test_hard_filter_drops_student_only_internships_only():
    [hard] = yaml_rules("Internships: drop student-only")
    student = intern(location="Lyon, France")
    others = [intern(location="Tunis"), intern(location="Berlin"), intern(location="Remote"),
              classify(Job("t", "ft", "ML Engineer", location="Paris", description="Full-time role."))]
    assert student.intern_eligibility == "student_only" and not F.matches(hard, student)
    assert F.apply([hard], student)[2] == ["Internships: drop student-only"]
    assert all(F.matches(hard, j) for j in others)


def test_soft_weights():
    rules = yaml_rules("Internships open to graduates", "Internships with unclear eligibility")
    deltas = {loc: F.apply(rules, intern(location=loc))[1] for loc in ("Tunis", "Berlin", "Remote")}
    assert deltas == {"Tunis": 5, "Berlin": 0, "Remote": -5}
    ft = classify(Job("t", "ft", "ML Engineer", location="Remote", description="Full-time role."))
    assert F.apply(rules, ft)[1] == 0


def test_eligibility_is_a_dashboard_filter_field():
    src = (ROOT / "dashboard" / "app.py").read_text(encoding="utf-8")
    assert '"intern_eligibility"]' in src


# ---------- seeding / one-time config sync ----------
def old_install() -> MemoryStore:
    """A database seeded before this change: old filters, general_ml without internships."""
    store = MemoryStore()
    new_names = set(CONFIG_SYNCS[0]["filters"])
    store.upsert_filters([f for f in filters_from_yaml() if f["name"] not in new_names])
    profiles = []
    for p in profiles_from_yaml()[0]:
        p = dict(p)
        for fld, items in CONFIG_SYNCS[0]["profiles"].get(p["id"], {}).items():
            p[fld] = [x for x in p[fld] if x not in items]
        profiles.append(p)
    store.upsert_profiles(profiles)
    return store


def test_sync_items_are_in_the_yaml():
    names = {f["name"] for f in filters_from_yaml()}
    profiles = {p["id"]: p for p in profiles_from_yaml()[0]}
    for step in CONFIG_SYNCS:
        assert set(step["filters"]) <= names
        for pid, fields in step["profiles"].items():
            for fld, items in fields.items():
                assert set(items) <= set(profiles[pid][fld]), (pid, fld)
    assert "internship" in profiles["general_ml"]["contracts"]


def test_sync_adds_missing_items_once_and_keeps_user_edits():
    store = old_install()
    # user edits made before the update: renamed a query, paused a filter, changed general_ml's priority
    gm = next(p for p in store.load_profiles() if p["id"] == "general_ml")
    store.upsert_profiles([{**gm, "priority": "high", "queries": gm["queries"] + ["my own query"]}])
    remote = next(f for f in store.load_filters() if f["name"] == "Prefer remote")
    store.upsert_filters([{**remote, "active": False, "weight": 9}])
    n_filters = len(store.load_filters())

    added = sync_config_additions(store, log=lambda *_: None)
    assert added["filters"] == CONFIG_SYNCS[0]["filters"]
    assert added["profiles"]["general_ml"]["contracts"] == ["internship"]
    assert added["profiles"]["research_hpc"] == {"queries": ["research internship deep learning"]}
    filters = {f["name"]: f for f in store.load_filters()}
    assert len(filters) == n_filters + 3
    assert filters["Prefer remote"]["active"] is False and filters["Prefer remote"]["weight"] == 9
    gm = next(p for p in store.load_profiles() if p["id"] == "general_ml")
    assert gm["priority"] == "high" and "my own query" in gm["queries"] and "internship" in gm["contracts"]
    assert gm["queries"].count("machine learning internship") == 1

    # second run adds nothing — and a filter deleted after the sync stays deleted
    store.delete_filter(filters["Internships with unclear eligibility"]["id"])
    snapshot = (sorted(f["name"] for f in store.load_filters()), [dict(p) for p in store.load_profiles()])
    assert sync_config_additions(store, log=lambda *_: None) == {"filters": [], "profiles": {}}
    load_config(store)
    assert (sorted(f["name"] for f in store.load_filters()), store.load_profiles()) == snapshot
    assert sum(e["kind"] == "config_sync" for e in store.events) == 1


def test_sync_never_duplicates_existing_names():
    store = old_install()
    hard = next(f for f in filters_from_yaml() if f["name"] == "Internships: drop student-only")
    store.upsert_filters([{**hard, "id": "user-made", "weight": 0, "active": False}])   # user already added it
    added = sync_config_additions(store, log=lambda *_: None)
    assert "Internships: drop student-only" not in added["filters"]
    assert [f["active"] for f in store.load_filters() if f["name"] == hard["name"]] == [False]


def test_fresh_database_seed_includes_internship_items():
    store = MemoryStore()
    profiles, _, filters, _ = load_config(store)
    assert {"Internships: drop student-only", "Internships open to graduates"} <= {f["name"] for f in filters}
    assert "internship" in next(p for p in profiles if p["id"] == "general_ml")["contracts"]
    assert [e["detail"]["filters"] for e in store.events if e["kind"] == "config_sync"] == [[]]


def test_llm_prompt_mentions_graduate_internships():
    from jh.score import LLM_SYSTEM
    assert "graduated in 2026" in LLM_SYSTEM and '"students only"' in LLM_SYSTEM


# ---------- store without migration 004 ----------
class Sess:
    def __init__(self):
        self.headers, self.sent, self.patches = {}, [], []

    def post(self, url, params=None, json=None, headers=None, timeout=None):
        self.sent.append(json)
        if "/jobs" in url and "intern_eligibility" in json[0]:
            return type("R", (), {"status_code": 400, "text": '{"code":"PGRST204","message":"Could not find the '
                                  '\'intern_eligibility\' column of \'jobs\' in the schema cache"}'})()
        return type("R", (), {"status_code": 201, "text": ""})()

    def patch(self, url, params=None, json=None, headers=None, timeout=None):
        self.patches.append(json)
        return type("R", (), {"status_code": 400, "text": '{"code":"PGRST204","message":"Could not find the '
                              '\'intern_eligibility\' column of \'jobs\' in the schema cache"}',
                              "raise_for_status": lambda self: (_ for _ in ()).throw(RuntimeError("400"))})()


def test_store_tolerates_missing_intern_columns(capsys):
    sess = Sess()
    s = SupabaseStore("https://p.supabase.co", "k", session=sess)
    s.upsert_jobs([{"id": "a", "title": "ML Intern", "intern_eligibility": "unknown", "intern_reason": "x"}])
    assert sess.sent[-1] == [{"id": "a", "title": "ML Intern"}]
    assert s.set_intern_eligibility({"a": ("unknown", "country unknown")}) is False
    assert "004_intern_eligibility.sql" in capsys.readouterr().out


def test_migration_004_keeps_view_columns():
    sql = (ROOT / "db" / "migrations" / "004_intern_eligibility.sql").read_text()
    m2 = (ROOT / "db" / "migrations" / "002_scores_llm_fit_level.sql").read_text()
    view = lambda t: t[t.index("create view"):].strip()  # noqa: E731
    assert view(sql) == view(m2) and "security_invoker = true" in sql
    assert "add column if not exists intern_eligibility text" in sql
    assert "intern_eligibility text" in (ROOT / "db" / "schema.sql").read_text()


# ---------- scan + rescore ----------
def test_scan_classifies_and_filters_internships(scan):
    store, go = scan
    go()
    board = {r["title"]: r for r in store.board()}
    owkin = board["Stage - Data Scientist Computational Biology (6 mois)"]          # Paris
    assert owkin["intern_eligibility"] == "student_only" and owkin["hard_filtered"]
    assert owkin["filtered_by"] == ["Internships: drop student-only"]
    ok = board["Machine Learning Intern, Protein Design"]                           # "recent graduates welcome"
    assert ok["intern_eligibility"] == "graduate_ok" and not ok["hard_filtered"]
    assert "Internships open to graduates (+5)" in ok["reasons"]
    ch = board["Research Intern, Structure Prediction"]                             # Zurich, rule only
    assert ch["intern_eligibility"] == "graduate_ok_conditions" and not ch["hard_filtered"]
    assert board["Junior Geospatial Data Scientist"]["intern_eligibility"] is None


def test_rescore_sets_eligibility(scan):
    from jh.rescore import run as rescore
    store, go = scan
    go()
    owkin_id = next(r["id"] for r in store.board() if r["title"].startswith("Stage - Data Scientist"))
    for row in store.t["jobs"].values():                 # jobs stored before this change: no eligibility
        row.pop("intern_eligibility", None); row.pop("intern_reason", None)
    store.t["scores"][(owkin_id,)].update(hard_filtered=False, filtered_by=[])
    s = rescore(store=store, gateway=Gateway(store=store, chain=[]), log=lambda *_: None)
    after = {r["id"]: r for r in store.board()}
    assert s["intern_changed"] == 3 and s["internships"] == {"student_only": 1, "graduate_ok": 1,
                                                             "graduate_ok_conditions": 1}
    assert after[owkin_id]["intern_eligibility"] == "student_only" and after[owkin_id]["hard_filtered"]
    assert after[owkin_id]["intern_reason"] == "country rule: france"
    assert sum(r.get("intern_eligibility") is not None for r in after.values()) == 3


def test_reapply_filters_uses_stored_eligibility():
    from jh.score import reapply_filters
    row = {"id": "j", "title": "ML Intern", "description": "x", "contract": "internship",
           "intern_eligibility": "student_only", "score": 60, "pre_filter_score": 60, "reasons": []}
    u = reapply_filters([row], yaml_rules("Internships: drop student-only"))[0]
    assert u["hard_filtered"] and u["filtered_by"] == ["Internships: drop student-only"]


# ---------- dashboard (Streamlit AppTest, demo mode) ----------
def test_dashboard_intern_chip_and_checkbox(monkeypatch):
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest
    monkeypatch.setenv("JH_DEMO", "1")
    at = AppTest.from_file(str(ROOT / "dashboard" / "app.py"), default_timeout=120)
    at.run()
    assert not at.exception, at.exception
    html = " ".join(m.value for m in at.markdown)
    assert "Intern: open to graduates" in html and "Intern: graduates with permit conditions" in html
    assert "Intern: students only" not in html                       # hidden by the hard filter
    assert any("Internship rule (Switzerland)" in c.value for c in at.caption)
    box = next(c for c in at.sidebar.checkbox if c.label == "Internships open to graduates only")
    box.check().run()
    assert not at.exception, at.exception
    titles = [m.value for m in at.markdown if 'class="jh-title"' in m.value]
    assert len(titles) == 2 and all("Intern" in t for t in titles)
    next(c for c in at.sidebar.checkbox if c.label == "Internships open to graduates only").uncheck().run()
    next(t for t in at.sidebar.toggle if t.label == "Include filtered-out jobs").set_value(True).run()
    assert "Intern: students only" in " ".join(m.value for m in at.markdown)
    at.sidebar.radio[0].set_value("Filters").run()
    assert not at.exception, at.exception
    assert any(e.label == "Internship rules" for e in at.expander)
