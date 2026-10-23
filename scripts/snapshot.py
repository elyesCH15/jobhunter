"""Export Supabase to snapshots/*.jsonl so Git keeps the history.

Rows are sorted and keys ordered, so `git diff` shows exactly what changed between days.
The GitHub workflow commits only when a file actually changed.
Usage:  python scripts/snapshot.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402

from jh.store import CONFLICT_KEYS, OPTIONAL_TABLES, TABLES, SupabaseStore  # noqa: E402

OUT = Path(__file__).resolve().parent.parent / "snapshots"
VOLATILE = {"last_seen_at"}      # changes every scan; excluded so diffs show real changes only


def write_table(store, table: str, out_dir: Path = OUT) -> int:
    rows = store.dump(table)
    keys = CONFLICT_KEYS[table].split(",")
    rows.sort(key=lambda r: tuple(str(r.get(k)) for k in keys))
    out_dir.mkdir(exist_ok=True)
    with open(out_dir / f"{table}.jsonl", "w", encoding="utf-8", newline="\n") as f:
        for r in rows:
            clean = {k: v for k, v in r.items() if k not in VOLATILE}
            f.write(json.dumps(clean, ensure_ascii=False, sort_keys=True, default=str) + "\n")
    return len(rows)


def main():
    load_dotenv()
    store = SupabaseStore()
    for t in TABLES:
        try:
            print(f"{t:<16} {write_table(store, t):>6} rows")
        except Exception as e:  # noqa: BLE001
            if t not in OPTIONAL_TABLES:
                raise
            # table from a migration not run yet (e.g. 003_cv_versions.sql): skip, keep the rest
            print(f"{t:<16} skipped — {str(e)[:120]} (run db/migrations/ in Supabase)")


if __name__ == "__main__":
    main()
