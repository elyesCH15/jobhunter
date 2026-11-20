from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config"


def read_yaml(name: str) -> dict:
    return yaml.safe_load((CONFIG / name).read_text(encoding="utf-8")) or {}


def profiles_from_yaml() -> tuple[list[dict], list[str]]:
    data = read_yaml("profiles.yaml")
    return data.get("profiles", []), data.get("defaults", {}).get("global_exclude", [])


def filter_id(name: str) -> str:
    """Stable UUID from the rule name so re-seeding updates instead of duplicating."""
    h = hashlib.md5(name.encode()).hexdigest()
    return f"{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}"


def filters_from_yaml() -> list[dict]:
    rows = []
    for f in read_yaml("filters.yaml").get("filters", []):
        rows.append({"id": filter_id(f["name"]), "name": f["name"], "kind": f["kind"], "field": f["field"],
                     "operator": f["operator"], "value": f["value"], "weight": f.get("weight", 0),
                     "active": f.get("active", True)})
    return rows


def profile_rows(profiles: list[dict]) -> list[dict]:
    keys = ("id", "name", "priority", "titles", "keywords", "exclude", "contracts", "queries", "cv_id", "active")
    return [{k: p.get(k, True if k == "active" else None) for k in keys} for p in profiles]


# One-time additions for databases seeded before a config change. The DB is the source of truth once
# seeded, so a later YAML change never reaches it on its own. Each step runs once per database
# (recorded as a `config_sync` event): it inserts the named filters from filters.yaml when no filter
# has that name, and appends missing items to existing profiles. It never changes or removes what's
# there, and a filter or query you delete afterwards stays deleted. Fresh databases get everything from
# the YAML seed, so the step finds nothing to add. Keep the same items in profiles.yaml / filters.yaml.
CONFIG_SYNCS = [
    {"id": "2026-11-internships",
     "filters": ["Internships: drop student-only", "Internships open to graduates",
                 "Internships with unclear eligibility"],
     "profiles": {
         "general_ml": {"contracts": ["internship"],
                        "queries": ["machine learning internship", "AI engineer intern",
                                    "graduate internship machine learning", "Praktikum Machine Learning",
                                    "stage data science"]},
         "research_hpc": {"queries": ["research internship deep learning"]},
     }},
]


def _norm_item(x) -> str:
    return " ".join(str(x).lower().split())


def sync_config_additions(store, syncs: list[dict] | None = None, log=print) -> dict:
    """Apply CONFIG_SYNCS steps not yet recorded on this database. Returns what was added."""
    syncs = CONFIG_SYNCS if syncs is None else syncs
    done = store.config_syncs_done()
    added: dict = {"filters": [], "profiles": {}}
    for step in syncs:
        if step["id"] in done:
            continue
        existing = store.load_filters()
        names = {_norm_item(f.get("name")) for f in existing}
        ids = {str(f.get("id")) for f in existing}
        yaml_rows = {f["name"]: f for f in filters_from_yaml()}
        new_filters = [yaml_rows[n] for n in step.get("filters", [])
                       if n in yaml_rows and _norm_item(n) not in names and yaml_rows[n]["id"] not in ids]
        if new_filters:
            store.upsert_filters(new_filters)
            added["filters"] += [f["name"] for f in new_filters]
        profiles = {p["id"]: p for p in store.load_profiles()}
        rows = []
        for pid, fields in (step.get("profiles") or {}).items():
            p = profiles.get(pid)
            if not p:
                continue                                   # profile deleted or renamed: leave it alone
            row, changed = dict(p), {}
            for fld, items in fields.items():
                cur = list(p.get(fld) or [])
                have = {_norm_item(x) for x in cur}
                missing = [x for x in items if _norm_item(x) not in have]
                if missing:
                    row[fld] = cur + missing
                    changed[fld] = missing
            if changed:
                row["updated_at"] = datetime.now(timezone.utc).isoformat()
                rows.append(row)
                added["profiles"][pid] = changed
        if rows:
            store.upsert_profiles(rows)
        store.log_event("config_sync", detail={"id": step["id"], "filters": [f["name"] for f in new_filters],
                                               "profiles": {r["id"]: added["profiles"][r["id"]] for r in rows}})
        if new_filters or rows:
            log(f"config sync {step['id']}: added filters {[f['name'] for f in new_filters]}, "
                f"profile items {added['profiles']}")
    return added


def load_config(store):
    """DB is the source of truth once seeded; YAML seeds an empty database. Then one-time additions
    (CONFIG_SYNCS) bring older databases up to date without touching your edits."""
    yaml_profiles, global_exclude = profiles_from_yaml()
    profiles = store.load_profiles()
    if not profiles:
        store.upsert_profiles(profile_rows(yaml_profiles))
        profiles = store.load_profiles()
    filters = store.load_filters()
    if not filters:
        store.upsert_filters(filters_from_yaml())
        filters = store.load_filters()
    try:
        added = sync_config_additions(store)
    except Exception as e:  # noqa: BLE001 — a failed sync never stops a scan; it retries next run
        print(f"warning: config sync failed: {type(e).__name__}: {str(e)[:200]}")
        added = None
    if added and (added["filters"] or added["profiles"]):
        profiles, filters = store.load_profiles(), store.load_filters()
    return profiles, global_exclude, filters, read_yaml("sources.yaml")
