"""CV editor page — live preview on the left, editing panel on the right.

The working CV lives in st.session_state["cv_spec"] (a jh.cv.spec.CVSpec). Every widget writes into it
through a callback, the Typst source is rebuilt each run (a few ms) and compiled only when it changed
(st.cache_data keyed by the source). Text edits are CV-only overrides by default; "Save to database"
commits the change to career/career.yaml through the GitHub API.
"""
from __future__ import annotations

import re
from typing import Callable, Optional

import pandas as pd
import streamlit as st

from jh.cv import github as GH
from jh.cv import yamledit as YE
from jh.cv.career import CAREER_FILE, CAREER_PATH, Career, has_lang, tx
from jh.cv.defaults import new_spec
from jh.cv.render import cv_source, render_source
from jh.cv.resolve import (HEADINGS, bullet_text, category_label, entry_field, headline_text, skill_label,
                           summary_text)
from jh.cv.spec import SECTIONS, TEMPLATES, CVSpec, is_custom, move
from jh.store import cv_version_row, now_iso

TARGET_STATUSES = ("approved", "researched", "pack_ready", "form_filled", "submitted")
BASE = "__base__"
ENTRY_LABEL = {"experience": "experience", "projects": "project", "education": "education entry",
               "activities": "activity"}
SETUP_MSG = ("“Save to database” needs two secrets in Streamlit → app → Settings → Secrets: "
             "`GITHUB_TOKEN` (fine-grained token, Contents read/write on this repo only) and "
             "`GITHUB_REPO` (e.g. `elyesCH15/jobhunter`). See README → CV editor. "
             "Your edit is kept on this CV only.")


# ---------------- state helpers ----------------
def S() -> CVSpec:
    return st.session_state["cv_spec"]


def K(*parts) -> str:
    """Widget key; bumping cv_rev gives every widget a fresh key after the spec is replaced."""
    return f"cv{st.session_state.get('cv_rev', 0)}_" + "_".join(str(p) for p in parts)


def bump():
    st.session_state["cv_rev"] = st.session_state.get("cv_rev", 0) + 1


def flash(kind: str, msg: str):
    st.session_state["cv_flash"] = (kind, msg)


def short(text: str, n: int = 90) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def same(a: str, b: str) -> bool:
    return " ".join((a or "").split()) == " ".join((b or "").split())


# ---------------- career.yaml (GitHub first, local file as fallback) ----------------
@st.cache_data(ttl=60, show_spinner=False)
def _career_from_github(token: str, repo: str, branch: str) -> str:
    return GH.GitHubRepo(token, repo, branch).read(CAREER_PATH)[0]


@st.cache_data(show_spinner=False)
def _parse_career(text: str) -> Career:
    return Career.from_text(text)


def load_career(secret: Callable) -> tuple[Career, str]:
    token, repo = secret("GITHUB_TOKEN"), secret("GITHUB_REPO")
    if token and repo:
        try:
            return _parse_career(_career_from_github(token, repo, secret("GITHUB_BRANCH") or "main")), "GitHub"
        except Exception as e:  # noqa: BLE001 — fall back to the deployed file
            st.caption(f"Couldn't read career.yaml from GitHub ({str(e)[:120]}); using the deployed copy.")
    return _parse_career(CAREER_FILE.read_text(encoding="utf-8")), "deployed file"


def refresh_career():
    _career_from_github.clear()


@st.cache_data(max_entries=32, show_spinner=False)
def compile_preview(source: str) -> dict:
    r = render_source(source)
    return {"pdf": r.pdf, "png": r.png, "pages": r.pages, "seconds": r.seconds}


# ---------------- targets & versions ----------------
def targets(board: pd.DataFrame) -> dict[str, dict]:
    out = {BASE: {"label": "Base CV", "job": None}}
    if board is None or board.empty:
        return out
    rows = board[board["status"].isin(TARGET_STATUSES)].sort_values("seen", ascending=False)
    for r in rows.itertuples():
        job = {"id": r.id, "title": r.title, "company": r.company, "profile_id": r.profile_id,
               "language": r.language}
        out[r.id] = {"label": f"{r.title} — {r.company or '?'}  ·  {r.status}", "job": job}
    return out


def list_versions(store, job_id: Optional[str]) -> tuple[list[dict], Optional[str]]:
    try:
        return store.list_cv_versions(job_id), None
    except Exception as e:  # noqa: BLE001 — table missing before migration 003
        return [], f"Saved versions unavailable ({str(e)[:100]}). Run db/migrations/003_cv_versions.sql in Supabase."


def fresh_spec(target: str, tmap: dict, career: Career, profiles: list[dict]) -> CVSpec:
    """Defaults from the database: base CV, or tailored to the job's profile."""
    job = tmap[target]["job"]
    profile = next((p for p in profiles if job and p.get("id") == job.get("profile_id")), None)
    return new_spec(career, job, profile)


def start_spec(target: str, tmap: dict, career: Career, profiles: list[dict], store) -> CVSpec:
    """Latest saved version for the target, else fresh defaults."""
    job = tmap[target]["job"]
    versions, _ = list_versions(store, job["id"] if job else None)
    if versions:
        spec = CVSpec.from_dict(versions[0]["spec"])
        spec.version_id = versions[0]["id"]
        return spec
    return fresh_spec(target, tmap, career, profiles)


# ---------------- callbacks ----------------
def cb_toggle_entry(section, eid, career: Career, key):
    on = st.session_state[key]
    bullets = []
    if on and not is_custom(eid):
        e = career.entry(eid) or {}
        defaults = [b["id"] for b in career.bullets(e) if b.get("default")]
        bullets = defaults or [b["id"] for b in career.bullets(e)][:2]
    S().set_included(section, eid, on, bullets)


def cb_move(lst_getter, i, delta):
    move(lst_getter(), i, delta)


def cb_toggle_bullet(section, eid, bid, order, key):
    S().set_bullet(section, eid, bid, st.session_state[key], order)


def cb_text(key, override_key, base_text):
    v = st.session_state[key]
    S().set_override(override_key, None if same(v, base_text) else v.strip())


def cb_custom_text(key, cid, field="text"):
    S().custom.setdefault(cid, {})[field] = st.session_state[key].strip()


def cb_reset_override(override_key):
    S().set_override(override_key, None)
    bump()


def cb_variant(section, eid, old, key):
    S().replace_bullet(section, eid, old, st.session_state[key])
    bump()


def cb_set(attr, key, transform=None):
    v = st.session_state[key]
    setattr(S(), attr, transform(v) if transform else v)


def cb_language(key):
    S().language = st.session_state[key]
    bump()                          # text boxes must show the other language's text


def cb_template(key):
    t = st.session_state[key]
    S().template = t
    S().max_pages = TEMPLATES[t]["pages"]
    bump()


def cb_section_visible(sec, key):
    on, secs = st.session_state[key], S().sections
    if on and sec not in secs:
        secs.append(sec)
    elif not on and sec in secs:
        secs.remove(sec)


def save_to_db(secret, node_id: str, field: Optional[str], text_key: str, override_key: str):
    """“Save to database”: commit the text to career.yaml on GitHub, then drop the CV-only override."""
    gh = GH.from_settings(secret)
    if gh is None:
        flash("warning", SETUP_MSG)
        return
    lang, text = S().language, st.session_state.get(text_key, "")
    try:
        GH.save_career(gh, lambda t: YE.set_text(t, node_id, lang, text, field),
                       f"career: update {node_id}{'.' + field if field else ''} ({lang}) from the CV editor")
    except (GH.GitHubError, YE.EditError, Exception) as e:  # noqa: BLE001
        flash("error", f"Not saved to career.yaml: {e}. The edit stays on this CV.")
        return
    S().set_override(override_key, None)
    refresh_career()
    bump()
    flash("success", f"Saved to career.yaml ({node_id}, {lang}). Other CVs will use it too.")


def save_custom_bullet_to_db(secret, section: str, eid: str, cid: str):
    gh = GH.from_settings(secret)
    if gh is None:
        flash("warning", SETUP_MSG)
        return
    lang, text = S().language, (S().custom.get(cid) or {}).get("text", "")
    made = {}

    def edit(t):
        made["id"] = YE.new_bullet_id(t, eid, text)
        return YE.add_bullet(t, eid, {"id": made["id"], "status": "to_confirm", lang: YE.normalise(text)})
    try:
        GH.save_career(gh, edit, f"career: add bullet to {eid} from the CV editor")
    except Exception as e:  # noqa: BLE001
        flash("error", f"Not saved to career.yaml: {e}. The bullet stays on this CV.")
        return
    S().replace_bullet(section, eid, cid, made["id"])
    S().custom.pop(cid, None)
    refresh_career()
    bump()
    flash("success", f"Bullet added to career.yaml as {made['id']} (status: to_confirm).")


# ---------------- panels ----------------
def text_editor(label: str, key_parts: tuple, current: str, base: str, override_key: str, secret,
                db_node: Optional[str], db_field: Optional[str] = None, height: int = 90, missing_lang: bool = False):
    """text_area + 'edited for this CV' state + Reset + Save to database."""
    key = K(*key_parts)
    st.text_area(label or "Text", current, key=key, height=height, on_change=cb_text,
                 args=(key, override_key, base), label_visibility="visible" if label else "collapsed")
    edited = S().override(override_key) is not None
    c1, c2, c3 = st.columns([2.2, 1, 1.4])
    if missing_lang:
        c1.caption("No French text in the database yet — showing English.")
    elif edited:
        c1.caption("Edited for this CV only.")
    if edited:
        c2.button("Reset", key=K(*key_parts, "reset"), on_click=cb_reset_override, args=(override_key,))
    if db_node and (edited or missing_lang):
        c3.button("Save to database", key=K(*key_parts, "db"), on_click=save_to_db,
                  args=(secret, db_node, db_field, key, override_key),
                  help="Write this text into career/career.yaml (commit through the GitHub API).")


def panel_header(career: Career, secret):
    spec, lang = S(), S().language
    hl = {h["id"]: tx(h, lang) for h in career.headlines}
    if hl:
        ids = list(hl)
        st.selectbox("Headline", ids, index=ids.index(spec.headline_id) if spec.headline_id in ids else 0,
                     format_func=hl.get, key=K("headline"), on_change=cb_set, args=("headline_id", K("headline")))
        h = career.headline(spec.headline_id)
        if h:
            text_editor("", ("hl_text", spec.headline_id), headline_text(spec, career), tx(h, lang),
                        spec.headline_id, secret, spec.headline_id, height=68,
                        missing_lang=lang != "en" and not has_lang(h, lang))
    sm = {s["id"]: short(tx(s, lang), 80) for s in career.summaries}
    ids = [None] + list(sm)
    st.selectbox("Summary", ids, index=ids.index(spec.summary_id) if spec.summary_id in ids else 0,
                 format_func=lambda i: "No summary" if i is None else sm[i], key=K("summary"),
                 on_change=cb_set, args=("summary_id", K("summary")))
    s = career.summary(spec.summary_id)
    if s:
        text_editor("", ("sum_text", spec.summary_id), summary_text(spec, career), tx(s, lang), spec.summary_id,
                    secret, spec.summary_id, height=150, missing_lang=lang != "en" and not has_lang(s, lang))

    st.markdown("**Sections** — tick to show, arrows to reorder")
    order = spec.sections + [x for x in SECTIONS if x not in spec.sections]
    heads = HEADINGS.get(lang, HEADINGS["en"])
    for sec in order:
        c1, c2, c3 = st.columns([5, 1, 1])
        on = sec in spec.sections
        c1.checkbox(heads[sec], on, key=K("sec", sec), on_change=cb_section_visible, args=(sec, K("sec", sec)))
        if on:
            i = spec.sections.index(sec)
            c2.button("↑", key=K("sec_up", sec), disabled=i == 0, on_click=cb_move, args=(lambda: S().sections, i, -1))
            c3.button("↓", key=K("sec_dn", sec), disabled=i == len(spec.sections) - 1,
                      on_click=cb_move, args=(lambda: S().sections, i, 1))


def entry_title(career: Career, eid: str) -> str:
    spec = S()
    title = entry_field(spec, career, eid, "degree" if career.section_of(eid) == "education" else "title")
    org = entry_field(spec, career, eid, "org")
    return short(" — ".join(x for x in [title, org] if x), 70) or eid


def panel_entries(section: str, career: Career, secret):
    spec = S()
    if section not in spec.sections:
        st.info(f"The {HEADINGS['en'][section]} section is hidden — show it in “Header & summary”.")
    selected = spec.selected(section)
    customs = [cid for cid, c in spec.custom.items() if c.get("kind") == "entry" and c.get("section") == section]
    others = [e["id"] for e in career.entries(section) if e["id"] not in selected] + \
             [c for c in customs if c not in selected]
    for i, eid in enumerate(selected + others):
        on = eid in selected
        c1, c2, c3 = st.columns([6, 1, 1])
        label = entry_title(career, eid) + ("  ·  custom" if is_custom(eid) else "")
        status = (career.entry(eid) or {}).get("status")
        c1.checkbox(label + ("  ·  to confirm" if status == "to_confirm" else ""), on, key=K("inc", eid),
                    on_change=cb_toggle_entry, args=(section, eid, career, K("inc", eid)))
        if on:
            c2.button("↑", key=K("up", eid), disabled=i == 0, on_click=cb_move,
                      args=(lambda s=section: S().items[s], i, -1))
            c3.button("↓", key=K("dn", eid), disabled=i == len(selected) - 1, on_click=cb_move,
                      args=(lambda s=section: S().items[s], i, 1))
    if selected:
        st.divider()
        labels = {eid: entry_title(career, eid) for eid in selected}
        eid = st.selectbox(f"Edit {ENTRY_LABEL[section]}", selected, format_func=labels.get, key=f"cv_edit_{section}")
        entry_editor(section, eid, career, secret)
    with st.expander(f"Add a custom {ENTRY_LABEL[section]}"):
        with st.form(K("add_entry", section), clear_on_submit=True):
            title = st.text_input("Title" if section != "education" else "Degree")
            c1, c2 = st.columns(2)
            org = c1.text_input("Organisation / school" if section != "projects" else "Context (optional)")
            location = c2.text_input("Location")
            c3, c4 = st.columns(2)
            dates = c3.text_input("Dates", placeholder="Mar 2025 – Jun 2025")
            link = c4.text_input("Link (optional)", placeholder="github.com/you/repo")
            first = st.text_area("First bullet (optional)", height=70)
            if st.form_submit_button("Add") and title.strip():
                cid = S().add_custom("entry", section=section, title=title.strip(), org=org.strip(),
                                     location=location.strip(), dates=dates.strip(),
                                     link=re.sub(r"^https?://", "", link.strip()))
                bullets = [S().add_custom("bullet", text=first.strip())] if first.strip() else []
                S().set_included(section, cid, True, bullets)
                st.rerun()


def entry_editor(section: str, eid: str, career: Career, secret):
    spec, lang = S(), S().language
    custom = is_custom(eid)
    entry = {} if custom else (career.entry(eid) or {})
    fields = [("title", "Title"), ("org", "Organisation"), ("location", "Location"), ("dates", "Dates")]
    if section == "education" and not custom:
        variants = career.degree_variants(entry)
        if variants:
            ids = [v["id"] for v in variants]
            cur = spec.degree_variants.get(eid, ids[0])
            st.selectbox("Degree wording", ids, index=ids.index(cur) if cur in ids else 0,
                         format_func=lambda v: short(tx(next(x for x in variants if x["id"] == v), lang), 95),
                         key=K("degvar", eid),
                         on_change=lambda: S().degree_variants.__setitem__(eid, st.session_state[K("degvar", eid)]))
        fields = [("degree", "Degree"), ("org", "School"), ("location", "Location"), ("dates", "Dates")]
    cols = st.columns(2)
    for n, (f, label) in enumerate(fields):
        key = K("field", eid, f)
        cur = entry_field(spec, career, eid, f)
        if custom:
            cols[n % 2].text_input(label, cur, key=key, on_change=cb_custom_text, args=(key, eid, f))
        else:
            base = entry_field(CVSpec(language=lang, degree_variants=spec.degree_variants), career, eid, f)
            cols[n % 2].text_input(label, cur, key=key, on_change=cb_text, args=(key, f"{eid}.{f}", base))
    if not custom and isinstance(entry.get("title"), dict) and spec.override(f"{eid}.title") is not None:
        st.button("Save title to database", key=K("title_db", eid), on_click=save_to_db,
                  args=(secret, eid, "title", K("field", eid, "title"), f"{eid}.title"))
    if section == "education" and not custom:
        courses = career.bullets(entry)
        names = {c["id"]: tx(c, lang) for c in courses}
        st.multiselect("Coursework (printed in this order)", list(names), default=spec.item(section, eid)["bullets"],
                       format_func=names.get, key=K("courses", eid),
                       on_change=lambda: S().item(section, eid).__setitem__("bullets", list(st.session_state[K("courses", eid)])))
        return
    bullets_editor(section, eid, career, secret)


def bullets_editor(section: str, eid: str, career: Career, secret):
    spec, lang = S(), S().language
    item = spec.item(section, eid)
    custom_entry = is_custom(eid)
    pool = [] if custom_entry else career.bullets(career.entry(eid) or {})
    order = [b["id"] for b in pool]
    chosen = list(item["bullets"])
    st.markdown("**Bullets on this CV**")
    if not chosen:
        st.caption("None yet — tick bullets below or add your own.")
    for i, bid in enumerate(chosen):
        with st.container(border=True):
            c1, c2, c3 = st.columns([6, 1, 1])
            c1.checkbox("On this CV", True, key=K("b", eid, bid), on_change=cb_toggle_bullet,
                        args=(section, eid, bid, order, K("b", eid, bid)))
            c2.button("↑", key=K("bup", eid, bid), disabled=i == 0, on_click=cb_move,
                      args=(lambda: S().item(section, eid)["bullets"], i, -1))
            c3.button("↓", key=K("bdn", eid, bid), disabled=i == len(chosen) - 1, on_click=cb_move,
                      args=(lambda: S().item(section, eid)["bullets"], i, 1))
            if is_custom(bid):
                key = K("ctext", bid)
                st.text_area("Custom bullet", (spec.custom.get(bid) or {}).get("text", ""), key=key, height=80,
                             on_change=cb_custom_text, args=(key, bid), label_visibility="collapsed")
                if not custom_entry:
                    st.button("Save to database", key=K("cdb", bid), on_click=save_custom_bullet_to_db,
                              args=(secret, section, eid, bid),
                              help="Add this bullet to career.yaml (status: to_confirm, default off).")
                continue
            variants = career.variants(bid)
            if len(variants) > 1:
                st.selectbox("Wording", variants, index=variants.index(bid), key=K("var", eid, bid),
                             format_func=lambda v: short(tx(career.bullet(v), lang), 80),
                             on_change=cb_variant, args=(section, eid, bid, K("var", eid, bid)))
            b = career.bullet(bid) or {}
            text_editor("", ("btext", bid), bullet_text(spec, career, bid), tx(b, lang), bid, secret, bid,
                        height=95, missing_lang=lang != "en" and not has_lang(b, lang))
            if b.get("status") == "to_confirm":
                st.caption("Status in the database: to confirm.")
    hidden_variants = {v for bid in chosen if not is_custom(bid) for v in career.variants(bid)}
    rest = [b for b in pool if b["id"] not in chosen and b["id"] not in hidden_variants]
    if rest:
        st.markdown("**Not on this CV**")
        for b in rest:
            st.checkbox(short(tx(b, lang), 140) + ("  ·  to confirm" if b.get("status") == "to_confirm" else ""),
                        False, key=K("b", eid, b["id"]), on_change=cb_toggle_bullet,
                        args=(section, eid, b["id"], order, K("b", eid, b["id"])))
    with st.form(K("addb", eid), clear_on_submit=True):
        text = st.text_area("Add a custom bullet", height=70, placeholder="Type a new bullet for this CV…")
        if st.form_submit_button("Add bullet") and text.strip():
            cid = S().add_custom("bullet", text=text.strip())
            S().item(section, eid)["bullets"].append(cid)
            st.rerun()


def panel_skills(career: Career):
    spec, lang = S(), S().language
    st.caption("Skills print grouped by category, in this order. Levels are never printed.")
    groups: dict[str, list[str]] = {}
    for sid in spec.skills:
        groups.setdefault(skill_label(spec, career, sid)[0], []).append(sid)
    all_cats = list(dict.fromkeys([s.get("category") or "other" for s in career.skills.values()] + ["other"]))
    cat_order = list(groups) + [c for c in all_cats if c not in groups]

    def rebuild(new_groups):
        S().skills = [sid for c in new_groups for sid in new_groups[c]]

    def on_cat(cat, key):
        g = {c: list(v) for c, v in groups.items()}
        g[cat] = list(st.session_state[key])
        rebuild(g)

    def move_cat(cat, delta):
        cats = list(groups)
        i = cats.index(cat)
        if move(cats, i, delta):
            rebuild({c: groups[c] for c in cats})

    for cat in cat_order:
        opts = [sid for sid, s in career.skills.items() if (s.get("category") or "other") == cat]
        opts += [cid for cid, c in spec.custom.items() if c.get("kind") == "skill" and (c.get("category") or "other") == cat]
        if not opts:
            continue
        c1, c2, c3 = st.columns([8, 1, 1])
        c1.multiselect(category_label(cat, lang), opts, default=groups.get(cat, []),
                       format_func=lambda sid: skill_label(spec, career, sid)[1] or sid,
                       key=K("sk", cat), on_change=on_cat, args=(cat, K("sk", cat)))
        if cat in groups:
            i = list(groups).index(cat)
            c2.button("↑", key=K("skup", cat), disabled=i == 0, on_click=move_cat, args=(cat, -1))
            c3.button("↓", key=K("skdn", cat), disabled=i == len(groups) - 1, on_click=move_cat, args=(cat, 1))
    with st.form(K("addskill"), clear_on_submit=True):
        st.markdown("**Add a skill that isn't in the database**")
        c1, c2 = st.columns(2)
        name = c1.text_input("Skill")
        cat = c2.selectbox("Category", all_cats, format_func=lambda c: category_label(c, lang))
        if st.form_submit_button("Add skill") and name.strip():
            cid = S().add_custom("skill", name=name.strip(), category=cat)
            S().skills.append(cid)
            st.rerun()
    if spec.skills:
        with st.expander("Rename a skill on this CV"):
            sid = st.selectbox("Skill", [s for s in spec.skills if not is_custom(s)],
                               format_func=lambda s: skill_label(spec, career, s)[1], key=K("ren_sel"))
            if sid:
                key = K("ren", sid)
                st.text_input("Printed as", skill_label(spec, career, sid)[1], key=key, on_change=cb_text,
                              args=(key, f"skill:{sid}", career.skill_name(sid, lang)))


def panel_languages(career: Career, secret):
    spec, lang = S(), S().language
    names = {lid: f"{lg.get(lang) or lg.get('en')} ({tx(lg.get('level'), lang)})" for lid, lg in career.languages.items()}
    st.multiselect("Languages (printed in this order)", list(names), default=spec.languages, format_func=names.get,
                   key=K("langs"), on_change=cb_set, args=("languages", K("langs"), list))
    for lid, lg in career.languages.items():
        if lg.get("certificate") and lid in spec.languages:
            key = K("cert", lid)

            def on_cert(lid=lid, key=key):
                certs = S().certificates
                if st.session_state[key] and lid not in certs:
                    certs.append(lid)
                elif not st.session_state[key] and lid in certs:
                    certs.remove(lid)
            st.checkbox(f"Print {lg['certificate']} next to {lg.get(lang) or lg.get('en')}", lid in spec.certificates,
                        key=key, on_change=on_cert)
    st.divider()
    st.markdown(f"**{HEADINGS.get(lang, HEADINGS['en'])['activities']}**")
    panel_entries("activities", career, secret)


def panel_versions(store, job_id: Optional[str]):
    versions, err = list_versions(store, job_id)
    with st.expander(f"Saved versions for this target ({len(versions)})"):
        if err:
            st.warning(err)
        if not versions:
            st.caption("Nothing saved yet. “Save version” stores the CV as it is now (Supabase, and Git via the daily snapshot).")
        for v in versions:
            c1, c2, c3, c4 = st.columns([4, 1, 1, 1])
            when = str(v.get("updated_at") or "")[:16].replace("T", " ")
            current = v["id"] == S().version_id
            c1.markdown(f"**{v.get('name') or 'CV'}**{'  ·  open' if current else ''}  \n"
                        f"<span style='color:#55615F;font-size:.85rem'>{(v.get('language') or '').upper()} · "
                        f"{v.get('page_count') or '?'} page(s) · {when}</span>", unsafe_allow_html=True)

            def load(v=v, dup=False):
                spec = CVSpec.from_dict(v["spec"])
                spec.version_id = None if dup else v["id"]
                if dup:
                    spec.name = f"{spec.name} (copy)"
                    spec.created_at = None
                st.session_state["cv_spec"] = spec
                bump()
            c2.button("Load", key=K("load", v["id"]), on_click=load, disabled=current)
            c3.button("Duplicate", key=K("dup", v["id"]), on_click=load, kwargs={"dup": True})

            def delete(vid=v["id"]):
                store.delete_cv_version(vid)
                if S().version_id == vid:
                    S().version_id = None
            c4.button("Delete", key=K("del", v["id"]), on_click=delete)


# ---------------- page ----------------
def page_cv_editor(board: pd.DataFrame, profiles: list[dict], store, secret: Callable):
    st.markdown("""<style>
.block-container { max-width: 1500px; }
/* keep the preview in view while scrolling the editing panel */
div[data-testid="stHorizontalBlock"]:has(.jh-cv-preview) > div[data-testid="stColumn"]:first-child {
  position: sticky; top: 3.4rem; align-self: flex-start; max-height: calc(100vh - 4rem); overflow-y: auto; }
</style>""", unsafe_allow_html=True)
    st.header("CV editor")
    career, career_src = load_career(secret)
    tmap = targets(board)

    # ---- target: switching loads the latest saved version, else defaults ----
    if st.session_state.get("cv_target") not in tmap:
        st.session_state["cv_target"] = BASE
    target = st.session_state["cv_target"]
    if "cv_spec" not in st.session_state or st.session_state.get("cv_spec_target") != target:
        st.session_state["cv_spec"] = start_spec(target, tmap, career, profiles, store)
        st.session_state["cv_spec_target"] = target
        bump()
    spec = S()

    def on_target():
        st.session_state["cv_target"] = st.session_state["cv_target_sel"]

    top = st.columns([3.2, 1.1, 1.6, 1.1])
    keys = list(tmap)
    top[0].selectbox("Target", keys, index=keys.index(target), format_func=lambda k: tmap[k]["label"],
                     key="cv_target_sel", on_change=on_target,
                     help="Base CV, or a job you approved (statuses approved → submitted).")
    top[1].radio("Language", ["en", "fr"], index=["en", "fr"].index(spec.language), horizontal=True,
                 format_func=str.upper, key=K("lang"), on_change=cb_language, args=(K("lang"),))
    tpls = list(TEMPLATES)
    top[2].selectbox("Template", tpls, index=tpls.index(spec.template) if spec.template in tpls else 0,
                     format_func=lambda t: TEMPLATES[t]["label"], key=K("tpl"), on_change=cb_template, args=(K("tpl"),))
    top[3].selectbox("Page budget", [1, 2], index=0 if spec.max_pages == 1 else 1, key=K("maxp"),
                     format_func=lambda n: f"{n} page{'s' if n > 1 else ''}", on_change=cb_set, args=("max_pages", K("maxp")))

    # ---- compile (only when the Typst source changed) ----
    source = cv_source(spec, career)
    try:
        out = compile_preview(source)
        st.session_state["cv_preview"] = {"pages": out["pages"], "seconds": out["seconds"], "hash": spec.content_hash()}
    except Exception as e:  # noqa: BLE001
        out = None
        st.error(f"Typst couldn't compile this CV: {e}")

    bar = st.columns([1, 2.2, 1.1, 1.1, 1.3, 1.3], vertical_alignment="bottom")
    if out:
        over = out["pages"] > spec.max_pages
        bar[0].metric("Pages", f"{out['pages']} / {spec.max_pages}", delta="too long" if over else None,
                      delta_color="inverse", help=f"Compiled in {out['seconds'] * 1000:.0f} ms (cached when unchanged).")
    bar[1].text_input("Version name", spec.name, key=K("name"), on_change=cb_set, args=("name", K("name")))

    def save_version():
        s = S()
        s.created_at = s.created_at or now_iso()
        s.updated_at = now_iso()
        row = cv_version_row(s.to_dict(), (st.session_state.get("cv_preview") or {}).get("pages"))
        row["spec"]["version_id"] = row["id"]
        try:
            store.save_cv_version(row)
        except Exception as e:  # noqa: BLE001
            flash("error", f"Not saved: {str(e)[:160]}. Run db/migrations/003_cv_versions.sql in Supabase.")
            return
        s.version_id = row["id"]
        flash("success", f"Saved “{s.name}”.")
    bar[2].button("Save version", type="primary", on_click=save_version)
    if out:
        fname = re.sub(r"[^A-Za-z0-9]+", "_", f"CV_{spec.name}_{spec.language}").strip("_") + ".pdf"
        bar[3].download_button("Export PDF", out["pdf"], file_name=fname, mime="application/pdf")
    # ---- reserved for the next steps (not implemented yet) ----
    with bar[4].container(border=True):
        st.caption("ATS match % — next step")
    bar[5].button("Generate CV", disabled=True, help="AI tailoring — coming in the next step.")

    if "cv_flash" in st.session_state:
        kind, msg = st.session_state.pop("cv_flash")
        getattr(st, kind)(msg)
    panel_versions(store, tmap[target]["job"]["id"] if tmap[target]["job"] else None)

    left, right = st.columns([1.05, 1], gap="medium")
    with left:
        st.markdown('<span class="jh-cv-preview"></span>', unsafe_allow_html=True)
        if out:
            if out["pages"] > spec.max_pages:
                st.warning(f"This CV is {out['pages']} pages; the budget is {spec.max_pages}. "
                           "Untick a few bullets, or pick a 2-page budget for research roles.")
            for i, png in enumerate(out["png"]):
                st.image(png, caption=f"Page {i + 1} of {out['pages']}", width="stretch")
        st.caption(f"career.yaml from the {career_src}. Personal details come from career/personal.yaml "
                   "and are added only here, at render time.")
    with right:
        tabs = st.tabs(["Header & summary", "Experience", "Projects", "Education", "Skills", "Languages & other"])
        with tabs[0]:
            panel_header(career, secret)
        with tabs[1]:
            panel_entries("experience", career, secret)
        with tabs[2]:
            panel_entries("projects", career, secret)
        with tabs[3]:
            panel_entries("education", career, secret)
        with tabs[4]:
            panel_skills(career)
        with tabs[5]:
            panel_languages(career, secret)
        st.divider()

        def reset():
            st.session_state["cv_spec"] = fresh_spec(target, tmap, career, profiles)
            bump()
        st.button("Reset to defaults", on_click=reset,
                  help="Start this target again from the database defaults (saved versions are kept).")
