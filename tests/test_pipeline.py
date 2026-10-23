import json

import pytest

from jh import filters as F
from jh.classify import classify, detect_language
from jh.collectors.base import Http, RelevanceGate
from jh.config import profiles_from_yaml
from jh.llm import Gateway, LLMUnavailable, extract_json
from jh.models import Job
from jh.scan import run, rotate
from jh.store import MemoryStore

from . import fixtures as fx

SOURCES_CFG = {
    "aggregators": {"adzuna": {"active": True, "countries": ["gb"], "max_queries": 1},
                    "remoteok": {"active": True}, "arbeitnow": {"active": True, "pages": 1},
                    "jooble": {"active": True}},
    "company_boards": {"greenhouse": ["cradle", "doesnotexist"], "lever": ["owkin"], "ashby": ["sylvera"]},
}


class Resp:
    def __init__(self, data, status=200):
        self._d, self.status_code = data, status

    def json(self):
        return self._d

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def fake_get(url, params=None, headers=None, timeout=None):
    routes = {
        "boards-api.greenhouse.io/v1/boards/cradle": fx.GREENHOUSE_CRADLE,
        "api.lever.co/v0/postings/owkin": fx.LEVER_OWKIN,
        "api.ashbyhq.com/posting-api/job-board/sylvera": fx.ASHBY_SYLVERA,
        "remoteok.com/api": fx.REMOTEOK,
        "arbeitnow.com/api": fx.ARBEITNOW,
        "api.adzuna.com/v1/api/jobs/gb": fx.ADZUNA_GB,
    }
    for k, v in routes.items():
        if k in url:
            return Resp(v)
    return Resp({"error": "not found"}, 404)


@pytest.fixture
def scan(monkeypatch, tmp_path):
    monkeypatch.setenv("ADZUNA_APP_ID", "x")
    monkeypatch.setenv("ADZUNA_APP_KEY", "y")
    monkeypatch.delenv("JOOBLE_KEY", raising=False)
    for k in ("GEMINI_API_KEY", "GROQ_API_KEY", "OPENROUTER_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    import jh.config as cfg
    real = cfg.read_yaml
    monkeypatch.setattr(cfg, "read_yaml", lambda n: SOURCES_CFG if n == "sources.yaml" else real(n))
    store = MemoryStore()

    def go(**kw):
        return run(store=store, http=Http(get=fake_get), gateway=Gateway(store=store, chain=[]),
                   summary_path=tmp_path / "s.json", log=lambda *_: None, **kw)
    return store, go


# ---------- classification ----------
def test_language_detection():
    assert detect_language("We are looking for an engineer to join our team and work with you") == "en"
    assert detect_language("Nous recherchons un stagiaire pour rejoindre notre équipe dans le poste") == "fr"
    assert detect_language("نبحث عن مهندس تعلم آلي للانضمام إلى فريقنا في تونس") == "ar"
    assert detect_language("Wir suchen einen Engineer und bieten ein Team mit flexible Arbeitszeiten für Sie") == "other"


def test_classify_fields():
    j = classify(Job("t", "1", "Stage PFE - Data Scientist", location="Tunis, Tunisie",
                     description="Nous recherchons un stagiaire pour notre équipe à Tunis."))
    assert (j.contract, j.seniority, j.country, j.region, j.language) == ("internship", "entry", "tunisia", "tunisia", "fr")
    s = classify(Job("t", "2", "Senior ML Engineer", location="Remote", description="We need 7+ years of experience."))
    assert s.seniority == "senior" and s.remote == "remote" and s.region == "remote_global"
    m = classify(Job("t", "3", "ML Engineer", location="Lyon", description="You have 3+ years experience with the team."))
    assert m.seniority == "mid" and m.country == "france"


# ---------- filters ----------
def test_hard_and_soft_filters():
    rules = [F.Rule("langs", "hard", "language", "in", ["en", "fr", "ar"]),
             F.Rule("no unpaid", "hard", "text", "not_contains", ["unpaid"]),
             F.Rule("remote+", "soft", "remote", "equals", "remote", 5),
             F.Rule("min salary", "hard", "salary_min", "gte", 30000)]
    good = classify(Job("t", "1", "ML Engineer", location="Remote", description="We are a team and you will work with us"))
    kept, delta, fails, _ = F.apply(rules, good)
    assert kept and delta == 5 and not fails                       # unknown salary is not dropped
    bad = classify(Job("t", "2", "ML Intern (unpaid)", description="This is an unpaid role for you and the team"))
    kept, _, fails, _ = F.apply(rules, bad)
    assert not kept and fails == ["no unpaid"]


# ---------- full scan ----------
def test_scan_end_to_end(scan):
    store, go = scan
    s = go()
    board = {r["title"]: r for r in store.board()}

    # gate: office manager / marketing never stored; jooble skipped (no key); bad slug = error, not crash
    assert "Office Manager" not in board and "Marketing Coordinator" not in board
    assert s["sources"]["jooble"].startswith("skipped")
    assert s["sources"]["greenhouse:doesnotexist"].startswith("error")
    assert store.load_sources()["greenhouse:doesnotexist"]["last_status"] == "error"

    # dedupe: Cradle job from Greenhouse + Arbeitnow → one record, two sources
    cradle = [r for r in board.values() if "Protein Language" in r["title"]]
    assert len(cradle) == 1
    cid = cradle[0]["id"]
    assert {k[0] for k in store.t["job_sources"] if store.t["job_sources"][k]["job_id"] == cid} == {"greenhouse:cradle", "arbeitnow"}
    assert set(cradle[0]["tags"]) == {"greenhouse", "arbeitnow"}

    # routing
    assert cradle[0]["profile_id"] == "protein_ml" and cradle[0]["cv_id"] == "cv_bio_research"
    assert board["Junior Geospatial Data Scientist"]["profile_id"] == "climate_ai"
    assert board["Graduate AI Engineer - Computer Vision"]["profile_id"] == "genai_cv"
    assert board["Junior Machine Learning Engineer (LLM / RAG)"]["profile_id"] == "general_ml"
    owkin = board["Stage - Data Scientist Computational Biology (6 mois)"]
    assert owkin["profile_id"] == "protein_ml" and owkin["contract"] == "internship" and owkin["language"] == "fr"

    # hard filters: senior + German posting kept in DB but flagged
    assert board["Senior Machine Learning Engineer, Platform"]["hard_filtered"]
    assert board["Machine Learning Engineer (m/w/d)"]["hard_filtered"]
    assert "Languages I can apply in" in board["Machine Learning Engineer (m/w/d)"]["filtered_by"]

    # protein job beats the generic ones
    assert cradle[0]["score"] > board["Junior Machine Learning Engineer (LLM / RAG)"]["score"] - 15
    assert s["top_new"][0]["score"] >= s["top_new"][-1]["score"]
    assert all(r["status"] == "new" for r in board.values())


def test_second_scan_finds_nothing_new(scan):
    store, go = scan
    first = go()
    second = go(force=True)
    assert first["new"] > 0 and second["new"] == 0 and second["seen_again"] == first["unique"]
    assert go()["sources"]["remoteok"] == "not due"                   # frequency respected


def test_status_change_is_logged(scan):
    store, go = scan
    go()
    jid = store.board()[0]["id"]
    store.set_status(jid, "approved")
    assert store.get_application(jid)["status"] == "approved"
    assert store.events[-1]["kind"] == "status_change"


# ---------- LLM gateway ----------
def test_llm_fallback_and_cache(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "a")
    monkeypatch.setenv("GROQ_API_KEY", "b")
    calls = []

    def post(url, headers=None, json=None, timeout=None):
        calls.append(url)
        if "googleapis" in url:
            return Resp({}, 429)
        return Resp({"choices": [{"message": {"content": "```json\n[{\"id\": \"x\", \"fit\": 80}]\n```"}}]})

    store = MemoryStore()
    g = Gateway(store=store, chain=["gemini", "groq"], post=post, sleep=lambda s: None)
    data, used = g.complete_json("sys", "user", task="score")
    assert data == [{"id": "x", "fit": 80}] and used.startswith("groq")
    data2, used2 = g.complete_json("sys", "user", task="score")
    assert used2 == "cache" and len(calls) == 2


def test_llm_all_down():
    with pytest.raises(LLMUnavailable):
        Gateway(chain=[]).complete_json("s", "u")


def test_llm_refines_scores(scan, monkeypatch):
    store, _ = scan
    monkeypatch.setenv("GROQ_API_KEY", "b")

    def post(url, headers=None, json=None, timeout=None):
        jobs = __import__("json").loads(json["messages"][1]["content"])
        out = [{"id": j["id"], "fit": 95, "profile": "protein_ml", "reasons": ["strong PLM match"], "gaps": []} for j in jobs]
        return Resp({"choices": [{"message": {"content": __import__("json").dumps(out)}}]})

    import tempfile, pathlib
    run(store=store, http=Http(get=fake_get), gateway=Gateway(store=store, chain=["groq"], post=post),
        summary_path=pathlib.Path(tempfile.mkdtemp()) / "s.json", log=lambda *_: None)
    scored = [r for r in store.board() if r.get("llm_used")]
    assert scored and all("strong PLM match" in r["reasons"] for r in scored)


def test_helpers():
    assert extract_json('Sure! {"a": 1}') == {"a": 1}
    assert rotate([1, 2, 3, 4], 2, 1) == [3, 4] and rotate([1, 2], 5, 0) == [1, 2]


def test_contract_hints_and_regions():
    a = classify(Job("t", "1", "ML Engineer", location="Remote - Europe", contract_hint="FullTime", remote_hint=True))
    assert a.contract == "full_time" and a.region == "europe" and a.remote == "remote"
    b = classify(Job("t", "2", "ML Engineer", location="London", contract_hint="full_time"))
    assert b.contract == "full_time" and b.region == "europe"


def test_reapply_filters_after_edit(scan):
    from jh.score import reapply_filters
    store, go = scan
    go()
    rows = store.board()
    geo = next(r for r in rows if r["title"] == "Junior Geospatial Data Scientist")
    assert not geo["hard_filtered"]
    rules = [F.Rule("No remote", "hard", "remote", "not_equals", "remote"),
             F.Rule("Boost protein", "soft", "text", "contains", ["protein"], 10)]
    updates = {u["job_id"]: u for u in reapply_filters(rows, rules)}
    assert updates[geo["id"]]["hard_filtered"] and updates[geo["id"]]["filtered_by"] == ["No remote"]
    cradle = next(r for r in rows if "Protein Language" in r["title"])
    assert updates[cradle["id"]]["score"] == min(100, round(cradle["pre_filter_score"] + 10, 1))


def test_snapshot_is_stable(scan, tmp_path):
    import sys, pathlib
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
    from scripts.snapshot import write_table
    store, go = scan
    go()
    write_table(store, "jobs", tmp_path)
    first = (tmp_path / "jobs.jsonl").read_text()
    go(force=True)                                   # rescan: only last_seen_at changes
    write_table(store, "jobs", tmp_path)
    assert (tmp_path / "jobs.jsonl").read_text() == first
    assert "last_seen_at" not in first


# ---------- relevance gate ----------
PROFILES = profiles_from_yaml()[0]
AI_SALES_BODY = ("Sell our machine learning platform: LLM, PyTorch, deep learning, fine-tuning and MLOps "
                 "to enterprise customers. Python a plus.")


def test_gate_rejects_generic_titles():
    gate = RelevanceGate(PROFILES)
    for title in ("Office Manager", "Account Executive, AI Platform", "Research Program Manager"):
        assert not gate(title, AI_SALES_BODY), title
    for title in ("ML Engineer", "Research Scientist, Protein Design", "PhD Intern - Machine Learning",
                  "Stage - Data Scientist Computational Biology (6 mois)", "Junior Geospatial Data Scientist"):
        assert gate(title, ""), title


def test_gate_body_fallback_needs_strong_evidence():
    gate = RelevanceGate(PROFILES)
    assert gate("Platform Engineer", "Train and serve models with PyTorch and Hugging Face; Docker, RAG pipelines.")
    assert not gate("Platform Engineer", "Python services, Docker deployment, research team.")   # no ML term
    assert not gate("Platform Engineer", "We use PyTorch.")                                     # too few keywords


def test_strict_gate():
    strict = RelevanceGate(PROFILES, strict=True)
    for title in ("Senior Research Scientist", "Staff ML Engineer", "Sr. Data Scientist",
                  "Engineering Manager, Machine Learning", "Office Manager"):
        assert not strict(title, AI_SALES_BODY), title
    for title in ("Machine Learning Intern", "AI Resident", "Research Engineer, Interpretability",
                  "Senior Research Intern - ML"):
        assert strict(title, ""), title
    assert not strict("Platform Engineer", "PyTorch, Hugging Face, Docker, RAG, LLM")       # title only


GREENHOUSE_BIG = {"jobs": [
    {"id": i, "title": t, "location": {"name": "London, UK"}, "absolute_url": f"https://example.com/{i}",
     "updated_at": "2026-10-21T08:00:00Z", "company_name": "BigLab",
     "content": "Deep learning research with PyTorch, LLM fine-tuning and machine learning at scale."}
    for i, t in enumerate(["Senior Research Scientist", "Staff ML Engineer", "Machine Learning Intern",
                           "AI Resident", "Research Engineer", "Account Executive, AI Platform"], start=500)]}


def test_strict_board_in_scan(monkeypatch, tmp_path):
    import jh.config as cfg
    real = cfg.read_yaml
    sources = {"aggregators": {}, "strict": ["greenhouse:biglab"],
               "company_boards": {"greenhouse": ["biglab", "smalllab"]}}
    monkeypatch.setattr(cfg, "read_yaml", lambda n: sources if n == "sources.yaml" else real(n))

    def get(url, params=None, headers=None, timeout=None):
        return Resp(GREENHOUSE_BIG) if "/boards/biglab/" in url or "/boards/smalllab/" in url else Resp({}, 404)

    store = MemoryStore()
    s = run(store=store, http=Http(get=get), gateway=Gateway(store=store, chain=[]),
            summary_path=tmp_path / "s.json", log=lambda *_: None)
    assert s["sources"]["greenhouse:biglab"] == 3            # intern, resident, research engineer
    assert s["sources"]["greenhouse:smalllab"] == 5          # normal gate keeps seniors (hard filters flag them)
    assert store.load_sources()["greenhouse:biglab"]["config"]["strict"] is True


def test_greenhouse_eu_fallback():
    from jh.collectors.sources import greenhouse
    urls = []

    def get(url, params=None, headers=None, timeout=None):
        urls.append(url)
        return Resp(fx.GREENHOUSE_CRADLE) if "boards-api.eu.greenhouse.io/v1/boards/owkin" in url else Resp({}, 404)

    jobs = greenhouse("owkin", Http(get=get), RelevanceGate(PROFILES))
    assert jobs and urls[0].startswith("https://boards-api.greenhouse.io") and "eu.greenhouse" in urls[1]
    with pytest.raises(RuntimeError, match="404"):
        greenhouse("nope", Http(get=get), RelevanceGate(PROFILES))


def test_removed_source_becomes_inactive(scan):
    store, go = scan
    store.update_source("greenhouse:deepmind", "board", last_status="error", last_error="HTTP 404")
    go()
    row = store.load_sources()["greenhouse:deepmind"]
    assert row["active"] is False and row["last_status"] == "inactive"
    assert store.load_sources()["greenhouse:cradle"]["active"] is True


# ---------- secrets ----------
def test_env_strips_whitespace(monkeypatch):
    from jh.env import env
    from jh.llm import PROVIDERS
    from jh.store import SupabaseStore
    monkeypatch.setenv("JOOBLE_KEY", "abc123\t")
    monkeypatch.setenv("GROQ_API_KEY", "  gsk_x\n")
    monkeypatch.setenv("SUPABASE_URL", "https://p.supabase.co/\t\n")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "\tsvc ")
    monkeypatch.setenv("EMPTY_KEY", " \t ")
    assert env("JOOBLE_KEY") == "abc123" and env("EMPTY_KEY") is None and env("EMPTY_KEY", "d") == "d"
    assert PROVIDERS["groq"].key == "gsk_x"
    s = SupabaseStore()
    assert s.url == "https://p.supabase.co/rest/v1" and s.s.headers["apikey"] == "svc"
    assert SupabaseStore("https://q.supabase.co\t", "k\t").s.headers["Authorization"] == "Bearer k"


def test_jooble_trimmed_key_and_query_cap(monkeypatch, tmp_path):
    import jh.config as cfg
    real = cfg.read_yaml
    sources = {"aggregators": {"jooble": {"active": True, "max_queries": 2, "locations": ["Tunisia", "France", "Remote"]}}}
    monkeypatch.setattr(cfg, "read_yaml", lambda n: sources if n == "sources.yaml" else real(n))
    monkeypatch.setenv("JOOBLE_KEY", "secret-key\t")
    posts = []

    def post(url, json=None, headers=None, timeout=None):
        posts.append((url, json["keywords"], json["location"]))
        return Resp({"jobs": []})

    store = MemoryStore()
    s = run(store=store, http=Http(get=fake_get, post=post), gateway=Gateway(store=store, chain=[]),
            summary_path=tmp_path / "s.json", log=lambda *_: None)
    assert s["sources"]["jooble"] == 0
    assert len(posts) == 3 * 2                                    # locations × max_queries
    assert {u for u, _, _ in posts} == {"https://jooble.org/api/secret-key"}
