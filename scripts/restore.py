"""Rebuild Supabase from the snapshots in Git (e.g. after a paused/lost project).

1. Create a new Supabase project and run db/schema.sql in its SQL editor.
2. Put its URL and service key in .env (or GitHub Secrets).
3. python scripts/restore.py            (add --commit SHA to restore an older state:
   git checkout SHA -- snapshots/ first)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402

from jh.store import TABLES, SupabaseStore  # noqa: E402

SNAP = Path(__file__).resolve().parent.parent / "snapshots"
SKIP_IDS = {"events", "llm_usage"}     # bigserial ids: let Postgres assign new ones


def main():
    load_dotenv()
    store = SupabaseStore()
    for t in TABLES:
        f = SNAP / f"{t}.jsonl"
        if not f.exists():
            continue
        rows = [json.loads(line) for line in f.read_text(encoding="utf-8").splitlines() if line.strip()]
        if t in SKIP_IDS:
            for r in rows:
                r.pop("id", None)
            store.log_events(rows) if t == "events" else [store.log_llm(**r) for r in rows]
        else:
            store.load(t, rows)
        print(f"{t:<16} {len(rows):>6} rows restored")


if __name__ == "__main__":
    main()
