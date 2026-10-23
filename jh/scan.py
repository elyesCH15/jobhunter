"""Scan entry point:  python -m jh.scan [--dry-run] [--force] [--only SOURCE]

1. load profiles / filters / sources   2. run every source that's due
3. classify + dedupe                    4. store jobs, sources, raw payloads
5. score NEW jobs (rules + free LLM)    6. log events, write scan_summary.json
(python -m jh.rescore re-scores every stored job after a scoring change)
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

from dotenv import load_dotenv

from . import filters as F
from .classify import classify
from .collectors.base import Http, RelevanceGate
from .collectors.sources import AGGREGATORS, BOARDS, SkipSource
from .config import ROOT, load_config
from .dedupe import dedupe
from .llm import Gateway
from .score import score_jobs
from .store import get_store, now_iso


def rotate(items: list, n: int, salt: int) -> list:
    """Pick n items, rotating each run so every query gets used across the day."""
    if n <= 0 or n >= len(items):
        return list(items)
    start = (salt * n) % len(items)
    return (items + items)[start:start + n]


def is_due(src_row: dict | None, freq_min: int, force: bool) -> bool:
    if force or not src_row or not src_row.get("last_run_at"):
        return True
    last = datetime.fromisoformat(str(src_row["last_run_at"]).replace("Z", "+00:00"))
    return datetime.now(timezone.utc) - last >= timedelta(minutes=freq_min - 5)   # 5 min slack for cron jitter


def run(store=None, http: Http | None = None, gateway: Gateway | None = None, force=False, only=None,
        summary_path: Path | None = ROOT / "scan_summary.json", log=print) -> dict:
    store = store or get_store()
    http = http or Http()
    gateway = gateway if gateway is not None else Gateway(store=store)
    profiles, global_exclude, filter_rows, sources_cfg = load_config(store)
    active_profiles = [p for p in profiles if p.get("active", True)]
    rules = [F.Rule.from_dict(r) for r in filter_rows]
    gate = RelevanceGate(active_profiles)
    strict_gate = RelevanceGate(active_profiles, strict=True)
    strict_ids = {str(x).strip() for x in (sources_cfg.get("strict") or [])}

    def gate_for(sid: str) -> RelevanceGate:
        """Big boards listed under `strict:` in sources.yaml get the title-only, no-senior gate."""
        return strict_gate if sid in strict_ids else gate

    queries = [q for p in active_profiles for q in (p.get("queries") or [])]
    salt = int(time.time() // 3600)
    known_sources = store.load_sources()

    collected, per_source, configured = [], {}, set()

    def run_source(sid: str, kind: str, freq: int, fn, config: dict):
        configured.add(sid)
        if only and not sid.startswith(only):
            return
        if not is_due(known_sources.get(sid), freq, force):
            per_source[sid] = "not due"
            return
        try:
            jobs = fn()
            collected.extend(jobs)
            prev_total = (known_sources.get(sid) or {}).get("total_found") or 0
            store.update_source(sid, kind, config=config, frequency_min=freq, active=True, last_run_at=now_iso(),
                                last_status="ok", last_error=None, last_found=len(jobs), total_found=prev_total + len(jobs))
            per_source[sid] = len(jobs)
        except SkipSource as e:
            store.update_source(sid, kind, config=config, frequency_min=freq, active=True, last_run_at=now_iso(),
                                last_status="skipped", last_error=str(e), last_found=0)
            per_source[sid] = f"skipped: {e}"
        except Exception as e:  # noqa: BLE001 — one broken source never stops the scan
            msg = f"{type(e).__name__}: {str(e)[:250]}"
            store.update_source(sid, kind, config=config, frequency_min=freq, active=True, last_run_at=now_iso(),
                                last_status="error", last_error=msg, last_found=0)
            store.log_event("error", source_id=sid, detail={"error": msg})
            per_source[sid] = f"error: {msg}"

    for name, cfg in (sources_cfg.get("aggregators") or {}).items():
        if not cfg.get("active", True) or name not in AGGREGATORS:
            continue
        qs = rotate(queries, int(cfg.get("max_queries", 6)), salt)     # caps requests per run (Jooble quota)
        run_source(name, "api", int(cfg.get("frequency_min", 120)),
                   lambda fn=AGGREGATORS[name], c=cfg, q=qs, g=gate_for(name): fn(c, q, http, g), cfg)

    boards = sources_cfg.get("company_boards") or {}
    bfreq = int(boards.get("frequency_min", 240))
    for platform, fn in BOARDS.items():
        for slug in boards.get(platform) or []:
            sid = f"{platform}:{slug}"
            run_source(sid, "board", bfreq, lambda fn=fn, s=slug, g=gate_for(sid): fn(s, http, g),
                       {"slug": slug, "strict": sid in strict_ids})

    # sources removed from sources.yaml (or set active: false) stop showing as errors on the dashboard
    if not only:
        for sid, row in known_sources.items():
            if sid not in configured and row.get("kind") in ("api", "board") and row.get("active", True):
                store.update_source(sid, row["kind"], active=False, last_status="inactive",
                                    last_error="not in config/sources.yaml (removed or active: false)")

    # classify → dedupe → store
    for j in collected:
        classify(j)
    index = store.recent_jobs_index()
    known_ids = {r["id"] for r in index}
    unique, groups = dedupe(collected, index)
    new_jobs = [j for j in unique if j.fingerprint not in known_ids]
    seen_again = [j.fingerprint for j in unique if j.fingerprint in known_ids]

    ts = now_iso()
    store.upsert_jobs([{**j.to_row(), "last_seen_at": ts} for j in new_jobs])
    store.touch_jobs(seen_again)
    store.add_job_sources([{"job_id": cid, "source_id": j.source_id, "external_id": j.external_id, "url": j.url}
                           for cid, js in groups.items() for j in js])
    store.add_raw([{"source_id": j.source_id, "external_id": j.external_id, "payload": j.raw} for j in collected])

    rules_seniority = {j.fingerprint: j.seniority for j in new_jobs}
    results = score_jobs(new_jobs, active_profiles, global_exclude, rules, gateway)
    store.upsert_scores([r.to_row() for r in results])
    store.set_seniority({j.fingerprint: j.seniority for j in new_jobs              # levels the LLM filled in
                         if j.seniority != rules_seniority[j.fingerprint]})
    store.ensure_applications([j.fingerprint for j in new_jobs])

    by_id = {r.job_id: r for r in results}
    store.log_events([{"kind": "job_new", "job_id": j.fingerprint, "source_id": j.source_id,
                       "detail": {"title": j.title, "company": j.company, "score": by_id[j.fingerprint].score}}
                      for j in new_jobs])
    kept = [r for r in results if not r.hard_filtered]
    summary = {
        "at": ts, "collected": len(collected), "unique": len(unique), "new": len(new_jobs),
        "new_kept": len(kept), "new_filtered": len(results) - len(kept), "seen_again": len(seen_again),
        "llm_providers": gateway.available, "llm_scored": sum(1 for r in results if r.llm_used),
        "sources": per_source,
        "top_new": sorted(({"id": r.job_id, "score": r.score, "profile": r.profile_id,
                            "title": next(j.title for j in new_jobs if j.fingerprint == r.job_id),
                            "company": next(j.company for j in new_jobs if j.fingerprint == r.job_id)}
                           for r in kept), key=lambda x: -x["score"])[:15],
    }
    store.log_event("scan", detail={k: v for k, v in summary.items() if k != "top_new"})
    if summary_path:
        summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    log(f"collected {summary['collected']} · unique {summary['unique']} · new {summary['new']} "
        f"(kept {summary['new_kept']}, filtered {summary['new_filtered']}) · LLM {summary['llm_providers'] or 'off'}")
    for sid, v in per_source.items():
        log(f"  {sid:<32} {v}")
    return summary


def main(argv=None):
    load_dotenv()
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="use an in-memory store (nothing saved)")
    ap.add_argument("--force", action="store_true", help="ignore source frequencies")
    ap.add_argument("--only", help="run only sources whose id starts with this")
    a = ap.parse_args(argv)
    run(store=get_store(dry_run=a.dry_run), force=a.force, only=a.only)
    return 0


if __name__ == "__main__":
    sys.exit(main())
