"""JobHunter dashboard — triage, tracker, filters, profiles, sources, activity.

Run locally:   streamlit run dashboard/app.py
Demo mode (no Supabase, sample jobs):   JH_DEMO=1 streamlit run dashboard/app.py
On Streamlit Community Cloud, set SUPABASE_URL, SUPABASE_SERVICE_KEY and APP_PASSWORD in the app's Secrets.
"""
from __future__ import annotations

import hmac
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from jh import filters as F  # noqa: E402
from jh.config import filter_id, load_config  # noqa: E402
from jh.env import clean, env  # noqa: E402
from jh.score import reapply_filters  # noqa: E402
from jh.store import MemoryStore, SupabaseStore, now_iso  # noqa: E402

st.set_page_config(page_title="JobHunter", page_icon="🎯", layout="wide")

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Public+Sans:wght@400;500;600;700&display=swap');
html, body, [class*="css"], .stMarkdown, button, input, textarea { font-family: 'Public Sans', sans-serif; }
.block-container { padding-top: 1.6rem; max-width: 1180px; }
.jh-score { font-size: 2.1rem; font-weight: 700; line-height: 1; font-variant-numeric: tabular-nums; color: #0F5F58; }
.jh-score.low { color: #7A8A88; }
.jh-fresh { font-size: .8rem; color: #9A4A08; font-weight: 600; }
.jh-old { font-size: .8rem; color: #7A8A88; }
.jh-title { font-size: 1.08rem; font-weight: 650; margin: 0; }
.jh-title a { color: #14302D; text-decoration: none; }
.jh-title a:hover { text-decoration: underline; }
.jh-meta { color: #55615F; font-size: .86rem; margin: .15rem 0 .35rem; }
.jh-chip { display: inline-block; padding: .05rem .5rem; margin: 0 .3rem .25rem 0; border-radius: 4px;
           background: #E6EFED; color: #1D4A45; font-size: .76rem; }
.jh-gap { background: #F6EBDD; color: #7A3F08; }
.jh-drop { background: #F1E4E4; color: #7A2424; }
</style>
""", unsafe_allow_html=True)

STATUSES = ["new", "shortlisted", "approved", "researched", "pack_ready", "form_filled",
            "submitted", "interview", "offer", "rejected", "dismissed"]
STATUS_LABEL = {"new": "To triage", "shortlisted": "Maybe later", "approved": "Approved", "researched": "Researched",
                "pack_ready": "Pack ready", "form_filled": "Form filled", "submitted": "Submitted",
                "interview": "Interview", "offer": "Offer", "rejected": "Rejected by company", "dismissed": "Dismissed"}
FIELDS = ["text", "title", "description", "company", "country", "region", "remote", "contract", "seniority",
          "language", "salary_min", "source"]
OPERATORS = ["contains", "not_contains", "equals", "not_equals", "in", "not_in", "gte", "lte"]


def secret(name: str):
    """Streamlit secret or env var, trimmed (a pasted trailing tab/newline breaks keys and URLs)."""
    try:
        return clean(st.secrets[name])
    except Exception:  # noqa: BLE001 — no secrets file locally
        return env(name)


# ---------------- auth ----------------
def check_password() -> bool:
    if os.getenv("JH_DEMO"):
        return True
    expected = secret("APP_PASSWORD")
    if not expected:
        st.error("APP_PASSWORD is not set. Add it to the app's Secrets before using the dashboard.")
        return False
    if st.session_state.get("authed"):
        return True
    st.title("JobHunter")
    pw = st.text_input("Password", type="password")
    if pw:
        if hmac.compare_digest(pw, str(expected)):
            st.session_state.authed = True
            st.rerun()
        st.error("Wrong password.")
    return False


# ---------------- data ----------------
@st.cache_resource
def get_store():
    if os.getenv("JH_DEMO"):
        return demo_store()
    return SupabaseStore(secret("SUPABASE_URL"), secret("SUPABASE_SERVICE_KEY"))


def demo_store():
    import jh.config as cfg
    from jh.collectors.base import Http
    from jh.llm import Gateway
    from jh.scan import run
    from tests import test_pipeline as t
    store = MemoryStore()
    real = cfg.read_yaml
    cfg.read_yaml = lambda n: t.SOURCES_CFG if n == "sources.yaml" else real(n)
    os.environ.setdefault("ADZUNA_APP_ID", "demo"); os.environ.setdefault("ADZUNA_APP_KEY", "demo")
    run(store=store, http=Http(get=t.fake_get), gateway=Gateway(chain=[]), summary_path=None, log=lambda *a: None)
    cfg.read_yaml = real
    return store


@st.cache_data(ttl=60, show_spinner=False)
def load_board() -> pd.DataFrame:
    rows = get_store().board()
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    for col in ("score", "first_seen_at", "posted_at", "hard_filtered", "profile_id", "cv_id", "reasons", "gaps",
                "filtered_by", "status", "notes", "region", "contract", "seniority", "language", "remote"):
        if col not in df:
            df[col] = None
    df["score"] = pd.to_numeric(df["score"], errors="coerce").fillna(0)
    df["hard_filtered"] = df["hard_filtered"].fillna(False).astype(bool)
    df["status"] = df["status"].fillna("new")
    df["seen"] = pd.to_datetime(df["first_seen_at"], utc=True, errors="coerce")
    df["posted"] = pd.to_datetime(df["posted_at"], utc=True, errors="coerce")
    return df


@st.cache_data(ttl=60, show_spinner=False)
def load_cfg():
    profiles, global_exclude, filters, sources = load_config(get_store())
    return profiles, global_exclude, filters


def refresh():
    load_board.clear()
    load_cfg.clear()


def age_label(ts) -> tuple[str, bool]:
    if pd.isna(ts):
        return "date unknown", False
    hours = (datetime.now(timezone.utc) - ts.to_pydatetime()).total_seconds() / 3600
    if hours < 1:
        return "posted < 1 h ago", True
    if hours < 48:
        return f"posted {int(hours)} h ago", hours < 24
    return f"posted {int(hours // 24)} days ago", False


def as_list(v):
    if isinstance(v, list):
        return v
    if isinstance(v, str) and v.startswith("["):
        try:
            return json.loads(v)
        except json.JSONDecodeError:
            return [v]
    return [] if v in (None, "") or (isinstance(v, float) and pd.isna(v)) else [v]


def set_status(job_id: str, status: str):
    get_store().set_status(job_id, status)
    refresh()


# ---------------- pages ----------------
def job_card(row, profile_names: dict, actions: bool = True):
    with st.container(border=True):
        left, right = st.columns([1, 6], vertical_alignment="top")
        with left:
            cls = "jh-score" if row.score >= 50 else "jh-score low"
            st.markdown(f'<div class="{cls}">{row.score:.0f}</div>', unsafe_allow_html=True)
            label, fresh = age_label(row.posted if not pd.isna(row.posted) else row.seen)
            st.markdown(f'<div class="{"jh-fresh" if fresh else "jh-old"}">{label}</div>', unsafe_allow_html=True)
        with right:
            link = row.apply_url or row.url or "#"
            st.markdown(f'<p class="jh-title"><a href="{link}" target="_blank">{row.title}</a></p>', unsafe_allow_html=True)
            where = ", ".join(x for x in [row.company, row.location] if x)
            st.markdown(f'<p class="jh-meta">{where}</p>', unsafe_allow_html=True)
            chips = [profile_names.get(row.profile_id, row.profile_id or "no profile"),
                     f"CV: {row.cv_id or 'n/a'}", row.contract, row.seniority, row.remote, row.region, row.language]
            html = "".join(f'<span class="jh-chip">{c}</span>' for c in chips if c and c != "unknown")
            html += "".join(f'<span class="jh-chip jh-gap">{g}</span>' for g in as_list(row.gaps)[:3])
            html += "".join(f'<span class="jh-chip jh-drop">filtered: {f}</span>' for f in as_list(row.filtered_by))
            st.markdown(html, unsafe_allow_html=True)
            reasons = as_list(row.reasons)
            if reasons:
                st.caption("Why: " + "; ".join(str(r) for r in reasons[:5]))
            with st.expander("Description"):
                st.write((row.description or "")[:4000])
            if actions:
                b1, b2, b3, _ = st.columns([1, 1, 1, 3])
                b1.button("Approve", key=f"ap_{row.id}", type="primary", on_click=set_status, args=(row.id, "approved"))
                b2.button("Maybe later", key=f"sl_{row.id}", on_click=set_status, args=(row.id, "shortlisted"))
                b3.button("Dismiss", key=f"ds_{row.id}", on_click=set_status, args=(row.id, "dismissed"))


def page_triage(df: pd.DataFrame, profiles: list[dict]):
    st.header("Triage")
    if df.empty:
        st.info("No jobs yet. Run the “Scan for jobs” workflow in GitHub Actions, then reload this page.")
        return
    names = {p["id"]: p["name"] for p in profiles}
    with st.sidebar:
        st.subheader("Show")
        prof = st.multiselect("Profile", list(names), format_func=lambda x: names[x])
        region = st.multiselect("Region", sorted(df["region"].dropna().unique()))
        contract = st.multiselect("Contract", sorted(df["contract"].dropna().unique()))
        min_score = st.slider("Minimum score", 0, 100, 30)
        days = st.selectbox("Found in the last", [1, 3, 7, 30, 365], index=2, format_func=lambda d: f"{d} day{'s' if d > 1 else ''}")
        q = st.text_input("Search title, company, text")
        show_filtered = st.toggle("Include filtered-out jobs", False)

    v = df[df["status"] == "new"]
    if not show_filtered:
        v = v[~v["hard_filtered"]]
    if prof:
        v = v[v["profile_id"].isin(prof)]
    if region:
        v = v[v["region"].isin(region)]
    if contract:
        v = v[v["contract"].isin(contract)]
    v = v[v["score"] >= min_score]
    v = v[v["seen"] >= pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=days)]
    if q:
        ql = q.lower()
        v = v[(v["title"].fillna("") + " " + v["company"].fillna("") + " " + v["description"].fillna("")).str.lower().str.contains(ql, regex=False)]
    v = v.sort_values(["score", "seen"], ascending=[False, False])

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Waiting for you", len(v))
    c2.metric("Found in last 24 h", int((df["seen"] >= pd.Timestamp.now(tz="UTC") - pd.Timedelta(hours=24)).sum()))
    c3.metric("Approved", int((df["status"] == "approved").sum()))
    c4.metric("Filtered out", int(df["hard_filtered"].sum()))

    if v.empty:
        st.success("Nothing left to triage with these settings.")
        return
    per_page = 20
    pages = max(1, -(-len(v) // per_page))
    page = st.number_input("Page", 1, pages, 1) if pages > 1 else 1
    for row in v.iloc[(page - 1) * per_page: page * per_page].itertuples():
        job_card(row, names)


def page_tracker(df: pd.DataFrame, profiles: list[dict]):
    st.header("Tracker")
    if df.empty:
        st.info("No jobs yet.")
        return
    counts = df["status"].value_counts()
    pipeline = ["approved", "researched", "pack_ready", "form_filled", "submitted", "interview", "offer"]
    cols = st.columns(len(pipeline))
    for c, s in zip(cols, pipeline):
        c.metric(STATUS_LABEL[s], int(counts.get(s, 0)))

    chosen = st.multiselect("Status", STATUSES[1:], default=[s for s in STATUSES[1:] if s != "dismissed"],
                            format_func=STATUS_LABEL.get)
    v = df[df["status"].isin(chosen)].sort_values("seen", ascending=False)
    if v.empty:
        st.info("No jobs in these statuses yet. Approve jobs on the Triage page to start the pipeline.")
        return
    table = v[["title", "company", "location", "score", "status", "profile_id", "cv_id", "seen", "url"]].copy()
    table["status"] = table["status"].map(STATUS_LABEL)
    st.dataframe(table, hide_index=True, width="stretch",
                 column_config={"url": st.column_config.LinkColumn("Link"), "seen": st.column_config.DatetimeColumn("Found", format="D MMM, HH:mm"),
                                "score": st.column_config.NumberColumn(format="%.0f"), "profile_id": "Profile", "cv_id": "CV"})

    st.subheader("Update a job")
    opts = {r.id: f"{r.title} — {r.company}" for r in v.itertuples()}
    jid = st.selectbox("Job", list(opts), format_func=opts.get)
    if jid:
        cur = v.loc[v["id"] == jid].iloc[0]
        new_status = st.selectbox("Status", STATUSES, index=STATUSES.index(cur["status"]), format_func=STATUS_LABEL.get)
        notes = st.text_area("Notes", value=cur["notes"] if isinstance(cur["notes"], str) else "")
        if st.button("Save job", type="primary"):
            get_store().set_status(jid, new_status, notes=notes)
            refresh()
            st.toast("Job saved.")
            st.rerun()


def parse_value(raw: str, operator: str):
    raw = raw.strip()
    if operator in ("gte", "lte"):
        return float(raw)
    if operator in ("contains", "not_contains", "in", "not_in"):
        return [x.strip() for x in raw.split(",") if x.strip()]
    return raw


def page_filters(df: pd.DataFrame):
    st.header("Filters")
    st.caption("Hard filters hide a job completely. Soft filters raise or lower its score. "
               "Changes apply to new jobs automatically; use “Re-apply to all jobs” to update the ones already found.")
    _, _, rules = load_cfg()
    store = get_store()

    hard_counts, soft_counts = {}, {}
    if not df.empty:
        for fb in df["filtered_by"]:
            for name in as_list(fb):
                hard_counts[name] = hard_counts.get(name, 0) + 1
        for rs in df["reasons"]:
            for r in as_list(rs):
                if isinstance(r, str) and r.endswith(")") and " (" in r:
                    name = r.rsplit(" (", 1)[0]
                    soft_counts[name] = soft_counts.get(name, 0) + 1

    if not rules:
        st.info("No filters yet. Add one below.")
    for r in sorted(rules, key=lambda x: (x["kind"], x["name"])):
        with st.container(border=True):
            a, b, c = st.columns([5, 1.3, 1])
            val = ", ".join(map(str, r["value"])) if isinstance(r["value"], list) else str(r["value"])
            effect = (f"removed {hard_counts.get(r['name'], 0)} jobs" if r["kind"] == "hard"
                      else f"{r['weight']:+g} points · applied to {soft_counts.get(r['name'], 0)} jobs")
            a.markdown(f"**{r['name']}**  \n{r['kind'].capitalize()}: {r['field']} {r['operator'].replace('_', ' ')} *{val}*  \n"
                       f"<span style='color:#55615F;font-size:.85rem'>{effect}</span>", unsafe_allow_html=True)
            on = b.toggle("Active", value=bool(r.get("active", True)), key=f"on_{r['id']}")
            if on != bool(r.get("active", True)):
                store.upsert_filters([{**r, "active": on, "updated_at": now_iso()}])
                store.log_event("filter_change", detail={"filter": r["name"], "active": on})
                refresh(); st.rerun()
            if c.button("Delete", key=f"del_{r['id']}"):
                store.delete_filter(r["id"])
                store.log_event("filter_change", detail={"filter": r["name"], "deleted": True})
                refresh(); st.rerun()

    st.subheader("Add a filter")
    with st.form("add_filter", clear_on_submit=True):
        c1, c2 = st.columns(2)
        name = c1.text_input("Name", placeholder="e.g. Drop jobs requiring German")
        kind = c2.radio("Type", ["hard", "soft"], horizontal=True,
                        format_func=lambda k: "Hard — hide the job" if k == "hard" else "Soft — change the score")
        c3, c4, c5 = st.columns(3)
        field = c3.selectbox("Field", FIELDS)
        op = c4.selectbox("Condition", OPERATORS, format_func=lambda o: o.replace("_", " "))
        weight = c5.number_input("Score change (soft only)", -50, 50, 5)
        value = st.text_input("Value", placeholder="Comma-separated for contains / in, e.g. german, deutsch")
        st.caption("Hard filters keep jobs that match the condition. To drop jobs mentioning German, use "
                   "field = text, condition = not contains, value = german, deutsch.")
        if st.form_submit_button("Add filter", type="primary"):
            if not name or not value:
                st.error("Give the filter a name and a value.")
            else:
                try:
                    row = {"id": filter_id(name), "name": name, "kind": kind, "field": field, "operator": op,
                           "value": parse_value(value, op), "weight": weight if kind == "soft" else 0, "active": True}
                    store.upsert_filters([row])
                    store.log_event("filter_change", detail={"filter": name, "added": True})
                    refresh(); st.toast("Filter added."); st.rerun()
                except ValueError:
                    st.error("For ≥ / ≤ the value must be a number.")

    st.divider()
    if st.button("Re-apply to all jobs"):
        updates = reapply_filters(store.board(), [F.Rule.from_dict(r) for r in rules])
        store.upsert_scores(updates)
        store.log_event("filter_change", detail={"reapplied": len(updates)})
        refresh()
        st.toast(f"Filters re-applied to {len(updates)} jobs.")


def lines(v) -> str:
    return "\n".join(as_list(v))


def page_profiles():
    st.header("Search profiles")
    st.caption("One item per line. Queries are what gets sent to job sites; titles and keywords drive the score.")
    profiles, _, _ = load_cfg()
    store = get_store()
    for p in profiles:
        with st.expander(f"{p['name']} — {p.get('priority', 'medium')} priority{'' if p.get('active', True) else ' (paused)'}"):
            with st.form(f"prof_{p['id']}"):
                c1, c2, c3 = st.columns(3)
                name = c1.text_input("Name", p["name"])
                prio = c2.selectbox("Priority", ["high", "medium", "low"], index=["high", "medium", "low"].index(p.get("priority", "medium")))
                active = c3.toggle("Active", value=p.get("active", True))
                c4, c5 = st.columns(2)
                titles = c4.text_area("Target titles", lines(p.get("titles")), height=150)
                keywords = c5.text_area("Keywords", lines(p.get("keywords")), height=150)
                c6, c7 = st.columns(2)
                exclude = c6.text_area("Exclude terms", lines(p.get("exclude")), height=110)
                queries = c7.text_area("Search queries", lines(p.get("queries")), height=110)
                contracts = st.multiselect("Contract types", ["full_time", "fixed_term", "internship", "phd", "freelance"],
                                           default=[c for c in as_list(p.get("contracts")) if c])
                cv = st.text_input("CV id", p.get("cv_id") or "", placeholder="set when your CVs are added")
                if st.form_submit_button("Save profile", type="primary"):
                    split = lambda s: [x.strip() for x in s.splitlines() if x.strip()]  # noqa: E731
                    store.upsert_profiles([{"id": p["id"], "name": name, "priority": prio, "active": active,
                                            "titles": split(titles), "keywords": split(keywords), "exclude": split(exclude),
                                            "queries": split(queries), "contracts": contracts, "cv_id": cv or None,
                                            "updated_at": now_iso()}])
                    store.log_event("profile_change", detail={"profile": p["id"]})
                    refresh(); st.toast("Profile saved."); st.rerun()


def page_sources():
    st.header("Sources")
    src = get_store().load_sources()
    if not src:
        st.info("Sources appear here after the first scan.")
        return
    df = pd.DataFrame(src.values())
    for col in ("last_status", "last_found", "total_found", "last_run_at", "last_error", "frequency_min"):
        if col not in df:
            df[col] = None
    ok, err, skip = (df["last_status"] == s for s in ("ok", "error", "skipped"))
    c1, c2, c3 = st.columns(3)
    c1.metric("Working", int(ok.sum())); c2.metric("Errors", int(err.sum())); c3.metric("Waiting for a key", int(skip.sum()))
    if err.any():
        st.caption("Errors on company boards usually mean the slug is wrong — fix or remove it in config/sources.yaml.")
    show = df[["id", "kind", "last_status", "last_found", "total_found", "last_run_at", "frequency_min", "last_error"]]
    st.dataframe(show.sort_values(["last_status", "id"]), hide_index=True, width="stretch",
                 column_config={"id": "Source", "last_status": "Status", "last_found": "Last run",
                                "total_found": "Total", "last_run_at": "Ran at", "frequency_min": "Every (min)",
                                "last_error": "Message"})


def page_activity():
    st.header("Activity")
    store = get_store()
    ev = pd.DataFrame(store.recent("events", 300))
    if ev.empty:
        st.info("No activity yet.")
    else:
        scans = ev[ev["kind"] == "scan"]
        if not scans.empty:
            last = scans.iloc[0]["detail"]
            last = json.loads(last) if isinstance(last, str) else last
            c1, c2, c3 = st.columns(3)
            c1.metric("Last scan: new jobs", last.get("new", 0))
            c2.metric("Collected", last.get("collected", 0))
            c3.metric("Scored by AI", last.get("llm_scored", 0))
        ev["detail"] = ev["detail"].apply(lambda d: d if isinstance(d, str) else json.dumps(d, ensure_ascii=False))
        st.dataframe(ev[["at", "kind", "job_id", "source_id", "detail"]], hide_index=True, width="stretch")
    llm = pd.DataFrame(store.recent("llm_usage", 1000))
    if not llm.empty:
        st.subheader("Free AI usage")
        st.dataframe(llm.groupby(["provider", "ok"]).size().reset_index(name="calls"), hide_index=True)


# ---------------- main ----------------
if check_password():
    try:
        board = load_board()
        profiles, _, _ = load_cfg()
    except Exception as e:  # noqa: BLE001
        st.error(f"Can't reach the database: {e}. Check SUPABASE_URL and SUPABASE_SERVICE_KEY in the app's Secrets.")
        st.stop()
    with st.sidebar:
        st.title("JobHunter")
        page = st.radio("Page", ["Triage", "Tracker", "Filters", "Profiles", "Sources", "Activity"], label_visibility="collapsed")
        if st.button("Reload data"):
            refresh(); st.rerun()
        st.divider()
    {"Triage": lambda: page_triage(board, profiles), "Tracker": lambda: page_tracker(board, profiles),
     "Filters": lambda: page_filters(board), "Profiles": page_profiles, "Sources": page_sources,
     "Activity": page_activity}[page]()
