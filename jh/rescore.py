"""Re-score every stored job:  python -m jh.rescore [--dry-run] [--rules-only]

Run after a scoring change (weights, LLM prompt, profiles, filters). For each job in the
database: rebuild it, re-classify its seniority from the stored description, score it again
with the current profiles / filters / LLM, and save the new score (and seniority).
Application statuses live in `applications` and are never touched.
"""
from __future__ import annotations

import argparse
import sys

from dotenv import load_dotenv

from . import filters as F
from .classify import classify_seniority
from .config import load_config
from .llm import Gateway
from .score import job_from_row, score_jobs
from .store import get_store


def run(store=None, gateway: Gateway | None = None, log=print) -> dict:
    store = store or get_store()
    gateway = gateway if gateway is not None else Gateway(store=store)
    profiles, global_exclude, filter_rows, _ = load_config(store)
    active_profiles = [p for p in profiles if p.get("active", True)]
    rules = [F.Rule.from_dict(r) for r in filter_rows]

    rows = store.dump("jobs")
    jobs, stored = [], {}
    for row in rows:
        job = job_from_row(row)
        job.seniority = classify_seniority(job)
        stored[job.fingerprint] = row.get("seniority")
        jobs.append(job)

    results = score_jobs(jobs, active_profiles, global_exclude, rules, gateway)
    store.upsert_scores([r.to_row() for r in results])
    changed = {j.fingerprint: j.seniority for j in jobs if j.seniority != stored[j.fingerprint]}
    store.set_seniority(changed)

    levels: dict[str, int] = {}
    for r in results:
        levels[r.level or "unknown"] = levels.get(r.level or "unknown", 0) + 1
    summary = {"rescored": len(results), "seniority_changed": len(changed),
               "llm_scored": sum(1 for r in results if r.llm_used), "llm_providers": gateway.available,
               "hard_filtered": sum(1 for r in results if r.hard_filtered), "levels": levels}
    store.log_event("rescore", detail=summary)
    log(f"rescored {summary['rescored']} jobs · seniority changed on {summary['seniority_changed']} · "
        f"LLM-scored {summary['llm_scored']} ({summary['llm_providers'] or 'off'}) · levels {levels}")
    return summary


def main(argv=None):
    load_dotenv()
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="use an in-memory store (nothing saved)")
    ap.add_argument("--rules-only", action="store_true", help="don't call the LLM (fast, no free-tier quota used)")
    a = ap.parse_args(argv)
    store = get_store(dry_run=a.dry_run)
    run(store=store, gateway=Gateway(store=store, chain=[]) if a.rules_only else None)
    return 0


if __name__ == "__main__":
    sys.exit(main())
