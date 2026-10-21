from __future__ import annotations

import hashlib
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


def load_config(store):
    """DB is the source of truth once seeded; YAML seeds an empty database."""
    yaml_profiles, global_exclude = profiles_from_yaml()
    profiles = store.load_profiles()
    if not profiles:
        store.upsert_profiles(profile_rows(yaml_profiles))
        profiles = store.load_profiles()
    filters = store.load_filters()
    if not filters:
        store.upsert_filters(filters_from_yaml())
        filters = store.load_filters()
    return profiles, global_exclude, filters, read_yaml("sources.yaml")
