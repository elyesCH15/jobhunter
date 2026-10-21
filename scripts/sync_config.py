"""Push config/profiles.yaml and config/filters.yaml into Supabase.

The first scan seeds an empty database automatically. Run this when you've edited the YAML
by hand and want the database to match (the dashboard edits the database directly).
Usage:  python scripts/sync_config.py [--profiles] [--filters]   (no flag = both)
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402

from jh.config import filters_from_yaml, profile_rows, profiles_from_yaml  # noqa: E402
from jh.store import SupabaseStore  # noqa: E402


def main(argv):
    load_dotenv()
    store = SupabaseStore()
    both = not ({"--profiles", "--filters"} & set(argv))
    if both or "--profiles" in argv:
        rows = profile_rows(profiles_from_yaml()[0])
        store.upsert_profiles(rows)
        print(f"profiles: {len(rows)} pushed")
    if both or "--filters" in argv:
        rows = filters_from_yaml()
        store.upsert_filters(rows)
        print(f"filters: {len(rows)} pushed")


if __name__ == "__main__":
    main(sys.argv[1:])
