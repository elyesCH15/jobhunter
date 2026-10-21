"""Hard/soft filter engine. Rules come from the `filters` table (or config/filters.yaml)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .models import Job, norm


@dataclass
class Rule:
    name: str
    kind: str          # hard | soft
    field: str
    operator: str
    value: Any
    weight: float = 0.0
    active: bool = True
    id: str | None = None

    @classmethod
    def from_dict(cls, d: dict) -> "Rule":
        return cls(name=d["name"], kind=d["kind"], field=d["field"], operator=d["operator"],
                   value=d["value"], weight=float(d.get("weight") or 0), active=d.get("active", True),
                   id=d.get("id"))


def _field_value(job: Job, field: str) -> Any:
    if field == "text":
        return job.text
    if field == "source":
        return job.source_id.split(":")[0]
    return getattr(job, field, None)


def _as_list(v: Any) -> list:
    return v if isinstance(v, list) else [v]


def matches(rule: Rule, job: Job) -> bool:
    """True when the job satisfies the rule's condition.
    Hard rules KEEP jobs that match; jobs that don't match are dropped.
    Soft rules apply their weight to jobs that match."""
    val = _field_value(job, rule.field)
    op = rule.operator
    if op in ("contains", "not_contains"):
        hay = " " + norm(str(val or "")) + " "
        hit = any(f" {norm(str(x))} " in hay or (len(norm(str(x)).split()) > 1 and norm(str(x)) in hay)
                  for x in _as_list(rule.value))
        return hit if op == "contains" else not hit
    if op in ("equals", "not_equals"):
        eq = norm(str(val or "")) == norm(str(rule.value))
        return eq if op == "equals" else not eq
    if op in ("in", "not_in"):
        inside = norm(str(val or "")) in {norm(str(x)) for x in _as_list(rule.value)}
        return inside if op == "in" else not inside
    if op in ("gte", "lte"):
        if val is None:
            return True            # unknown salary etc. → don't drop
        try:
            return float(val) >= float(rule.value) if op == "gte" else float(val) <= float(rule.value)
        except (TypeError, ValueError):
            return True
    raise ValueError(f"Unknown operator {op!r} in rule {rule.name!r}")


def apply(rules: list[Rule], job: Job) -> tuple[bool, float, list[str], list[str]]:
    """Returns (kept, soft_delta, hard_failures, soft_hits)."""
    failures, hits, delta = [], [], 0.0
    for r in rules:
        if not r.active:
            continue
        ok = matches(r, job)
        if r.kind == "hard" and not ok:
            failures.append(r.name)
        elif r.kind == "soft" and ok:
            delta += r.weight
            hits.append(f"{r.name} ({r.weight:+g})")
    return (not failures), delta, failures, hits
