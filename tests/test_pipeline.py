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
        out = [{"id": j["id"], "fit": 95, "profile": "protein_ml", "level": "junior",
                "reasons": ["strong PLM match"], "gaps": []} for j in jobs]
        return Resp({"choices": [{"message": {"content": __import__("json").dumps(out)}}]})

    import tempfile, pathlib
    run(store=store, http=Http(get=fake_get), gateway=Gateway(store=store, chain=["groq"], post=post),
        summary_path=pathlib.Path(tempfile.mkdtemp()) / "s.json", log=lambda *_: None)
    scored = [r for r in store.board() if r.get("llm_used")]
    assert scored and all("strong PLM match" in r["reasons"] and r["llm_fit"] == 95 for r in scored)
    # rules couldn't tell the Cradle job's level → the LLM's "junior" is stored on the job and drives +10
    cradle = next(r for r in store.board() if "Protein Language" in r["title"])
    assert cradle["seniority"] == "junior" and cradle["level"] == "junior"
    assert "junior role: +10 pts" in cradle["reasons"]
    assert cradle["pre_filter_score"] == round(0.4 * cradle["base_score"] + 0.6 * 95 + 10, 1)
    # the Owkin internship was "entry" by rules — the LLM doesn't override it
    owkin = next(r for r in store.board() if r["title"].startswith("Stage - Data Scientist"))
    assert owkin["seniority"] == "entry" and owkin["level"] == "entry"


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


# ---------- seniority ----------
@pytest.mark.parametrize("desc,level", [
    ("You have 0-2 years of experience with PyTorch.", "junior"),
    ("0 to 2 years of experience.", "junior"),
    ("1-2 years experience in ML.", "junior"),
    ("Up to 2 years of experience.", "junior"),
    ("Less than 2 years of industry experience.", "junior"),
    ("1+ years of experience in Python.", "junior"),
    ("Recent graduates welcome.", "junior"),
    ("This is an entry-level position.", "junior"),
    ("Entry level role in our ML team.", "junior"),
    ("Early career researchers are encouraged to apply.", "junior"),
    ("No prior experience required.", "junior"),
    ("A first experience with deep learning is a plus.", "junior"),
    ("Jeune diplômé(e) d'une école d'ingénieur.", "junior"),
    ("Débutant accepté.", "junior"),
    ("Une première expérience en machine learning est un plus.", "junior"),
    ("Vous avez 0 à 2 ans d'expérience.", "junior"),
    ("Vous avez 1 à 2 ans d’expérience.", "junior"),
    ("Join our new grad cohort.", "entry"),
    ("Our Graduate Programme lasts two years.", "entry"),
    ("This internship lasts 6 months.", "entry"),
    ("Stage de fin d'études de 6 mois.", "entry"),
    ("2-4 years of experience.", "junior"),               # ranges count by their lower bound
    ("3-5 years of experience.", "mid"),
    ("3 to 5 years of experience.", "mid"),
    ("Vous avez 3 à 5 ans d'expérience.", "mid"),
    ("4+ years of experience.", "mid"),
    ("5-8 years of experience.", "senior"),
    ("5+ ans d'expérience.", "senior"),
    ("We need 7+ years of experience.", "senior"),
    ("0-2 years of experience; 5+ years with Kubernetes is a plus.", "junior"),   # explicit junior wins
    ("Prior internship a plus; 3+ years of experience.", "mid"),
    ("Work in an early-stage startup.", "unknown"),
    ("Build models with PyTorch.", "unknown"),
])
def test_seniority_from_description(desc, level):
    from jh.classify import classify_seniority
    assert classify_seniority(Job("t", "1", "Machine Learning Engineer", description=desc)) == level


def test_seniority_title_rules_come_first():
    from jh.classify import classify_seniority
    assert classify_seniority(Job("t", "1", "Senior ML Engineer", description="0-2 years of experience")) == "senior"
    assert classify_seniority(Job("t", "2", "ML Intern", description="5+ years of experience")) == "entry"
    assert classify_seniority(Job("t", "3", "Junior Data Scientist", description="3-5 years")) == "junior"


# ---------- junior-first scoring ----------
RANK_PROFILES = [
    {"id": "protein_ml", "name": "Protein ML", "priority": "high", "titles": ["machine learning engineer"],
     "keywords": ["protein", "esm", "pytorch", "hpc", "structure prediction", "alphafold"], "contracts": ["full_time"]},
    {"id": "general_ml", "name": "General ML", "priority": "medium", "titles": ["machine learning engineer"],
     "keywords": ["python", "llm", "rag", "docker", "mlops", "transformers"], "contracts": ["full_time"]},
]
PERFECT = "Protein language models (ESM, AlphaFold), structure prediction, PyTorch on HPC. Full-time."


def rank_jobs():
    return [classify(Job("t", "jr", "Machine Learning Engineer", location="Paris",
                         description="0-2 years of experience. Python and LLM work. Full-time.")),
            classify(Job("t", "mid", "Machine Learning Engineer, Protein", location="Paris",
                         description="3-5 years of experience. " + PERFECT)),
            classify(Job("t", "sr", "Senior Machine Learning Engineer, Protein", location="Paris",
                         description="7+ years of experience. " + PERFECT))]


def test_junior_outranks_mid_perfect_match_and_senior_sinks():
    from jh.score import rule_score, score_jobs
    jr, mid, sr = rank_jobs()
    assert (jr.seniority, mid.seniority, sr.seniority) == ("junior", "mid", "senior")
    _, jr_reasons, _ = rule_score(jr, RANK_PROFILES[1], [])
    _, mid_reasons, mid_gaps = rule_score(mid, RANK_PROFILES[0], [])
    kw = lambda rs: len(next(x for x in rs if x.startswith("keywords")).split(","))  # noqa: E731
    assert kw(jr_reasons) < kw(mid_reasons) and "asks for ~3–4 years experience" in mid_gaps
    res = {r.job_id: r for r in score_jobs([jr, mid, sr], RANK_PROFILES, [], [])}
    a, b, c = (res[j.fingerprint] for j in (jr, mid, sr))
    assert a.score > b.score > c.score
    assert (a.level, b.level, c.level) == ("junior", "mid", "senior")
    assert "junior role: +10 pts" in a.reasons
    assert "mid-level role: −10 pts" in b.gaps and "senior role: −25 pts" in c.gaps
    assert c.score <= b.score - 15 and c.pre_filter_score == c.score   # no soft filters here


def test_rule_score_weights():
    from jh.score import rule_score
    p = {"id": "x", "titles": ["machine learning engineer"], "contracts": ["full_time"],
         "keywords": ["pytorch", "llm", "rag", "docker", "mlops", "python", "hpc"]}
    full = Job("t", "1", "Machine Learning Engineer", description="pytorch llm rag docker mlops python hpc")
    full.contract, full.seniority = "full_time", "junior"
    assert rule_score(full, p, [])[0] == 100                            # 30 + 30 + 10 + 30
    for level, pts in (("entry", 100), ("unknown", 82), ("mid", 73), ("senior", 70)):
        full.seniority = level
        assert rule_score(full, p, [])[0] == pts, level
    full.seniority = "senior"
    assert "senior role (5+ years experience)" in rule_score(full, p, [])[2]


def llm_gateway(monkeypatch, answer):
    monkeypatch.setenv("GROQ_API_KEY", "k")

    def post(url, headers=None, json=None, timeout=None):
        jobs = __import__("json").loads(json["messages"][1]["content"])
        out = [{"id": j["id"], "profile": "general_ml", "reasons": ["ok"], "gaps": [], **answer(j)} for j in jobs]
        return Resp({"choices": [{"message": {"content": __import__("json").dumps(out)}}]})
    return Gateway(chain=["groq"], post=post)


def test_llm_level_fills_unknown_seniority(monkeypatch):
    from jh.score import LLM_SYSTEM, score_jobs
    assert "most important criterion" in LLM_SYSTEM.lower() and '"level"' in LLM_SYSTEM
    a = classify(Job("t", "a", "Machine Learning Engineer", location="Paris", description="Python, LLM, RAG. Full-time."))
    b = classify(Job("t", "b", "ML Engineer, LLM", location="Paris", description="Python, LLM, RAG, Docker. Full-time."))
    known = classify(Job("t", "c", "Machine Learning Engineer", location="Lyon",
                         description="3-5 years of experience. Python, LLM, RAG. Full-time."))
    assert a.seniority == b.seniority == "unknown" and known.seniority == "mid"
    levels = {a.fingerprint: "junior", b.fingerprint: "senior", known.fingerprint: "junior"}
    gw = llm_gateway(monkeypatch, lambda j: {"fit": 70, "level": levels[j["id"]]})
    rules = [F.Rule("Drop senior roles", "hard", "seniority", "not_in", ["senior"])]
    res = {r.job_id: r for r in score_jobs([a, b, known], RANK_PROFILES, [], rules, gw)}
    ra, rb, rk = res[a.fingerprint], res[b.fingerprint], res[known.fingerprint]
    assert ra.llm_fit == 70 and ra.level == "junior" and a.seniority == "junior"
    assert ra.score == round(0.4 * ra.base_score + 0.6 * 70 + 10, 1) and "junior role: +10 pts" in ra.reasons
    # LLM says senior → job becomes senior, −25, and the senior hard filter now catches it
    assert rb.level == b.seniority == "senior" and rb.hard_filtered and rb.filtered_by == ["Drop senior roles"]
    assert rb.score == round(max(0, 0.4 * rb.base_score + 0.6 * 70 - 25), 1)
    # rule-based level wins when the rules know it
    assert rk.level == known.seniority == "mid" and "mid-level role: −10 pts" in rk.gaps
    assert ra.score > rk.score


def test_reapply_filters_uses_llm_level():
    from jh.score import reapply_filters
    row = {"id": "j1", "title": "ML Engineer", "description": "x", "seniority": "unknown", "level": "senior",
           "score": 50, "pre_filter_score": 50, "reasons": ["junior role: +10 pts", "Prefer remote (+5)"]}
    rules = [F.Rule("Drop senior roles", "hard", "seniority", "not_in", ["senior"])]
    u = reapply_filters([row], rules)[0]
    assert u["hard_filtered"] and u["score"] == 50 and u["reasons"] == ["junior role: +10 pts"]


# ---------- rescore ----------
def test_rescore_all_jobs(scan):
    from jh.rescore import run as rescore
    store, go = scan
    go()
    before = {r["id"]: r for r in store.board()}
    cradle = next(r for r in before.values() if "Protein Language" in r["title"])
    assert cradle["seniority"] == "unknown"
    store.set_status(cradle["id"], "approved")
    # old scoring left a stale score and an old classification behind
    store.t["jobs"][(cradle["id"],)]["description"] += " You have 0-2 years of experience."
    store.t["scores"][(cradle["id"],)].update(score=1.0, level=None, pre_filter_score=1.0)
    geo = next(r for r in before.values() if r["title"] == "Junior Geospatial Data Scientist")
    store.t["jobs"][(geo["id"],)]["seniority"] = "senior"

    s = rescore(store=store, gateway=Gateway(store=store, chain=[]), log=lambda *_: None)
    after = {r["id"]: r for r in store.board()}
    assert s["rescored"] == len(before) and s["seniority_changed"] == 2
    assert after[cradle["id"]]["seniority"] == "junior" and after[cradle["id"]]["level"] == "junior"
    # seniority 12 → 30 pts (protein_ml is high priority, ×1.0) and +10 career adjustment
    assert after[cradle["id"]]["pre_filter_score"] == min(100, round(before[cradle["id"]]["pre_filter_score"] + 18 + 10, 1))
    assert after[cradle["id"]]["score"] > before[cradle["id"]]["score"]
    assert after[cradle["id"]]["status"] == "approved"                     # statuses untouched
    assert after[geo["id"]]["seniority"] == "junior" and after[geo["id"]]["score"] == geo["score"]
    for jid, r in before.items():                                          # nothing else moved
        if jid not in (cradle["id"], geo["id"]):
            assert after[jid]["score"] == r["score"] and after[jid]["hard_filtered"] == r["hard_filtered"]
    assert store.events[-1]["kind"] == "rescore"


def test_rescore_script_entry_point(monkeypatch):
    import scripts.rescore as script
    import jh.rescore as R
    seen = {}
    monkeypatch.setattr(R, "run", lambda store=None, gateway=None, log=print: seen.update(store=store, gw=gateway))
    assert script.main(["--dry-run", "--rules-only"]) == 0
    assert isinstance(seen["store"], MemoryStore) and seen["gw"].providers == []


def test_scores_upsert_without_migration_002():
    from jh.store import SupabaseStore
    sent = []

    class Sess:
        headers = {}

        def post(self, url, params=None, json=None, headers=None, timeout=None):
            sent.append(json)
            if "level" in json[0]:
                return type("R", (), {"status_code": 400, "text": '{"code":"PGRST204","message":"Could not find '
                                      'the \'level\' column of \'scores\' in the schema cache"}'})()
            return type("R", (), {"status_code": 201, "text": ""})()

    SupabaseStore("https://p.supabase.co", "k", session=Sess()).upsert_scores(
        [{"job_id": "a", "score": 50, "llm_fit": None, "level": "junior"}])
    assert sent[-1] == [{"job_id": "a", "score": 50}]
