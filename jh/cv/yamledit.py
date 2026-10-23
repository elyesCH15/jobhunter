"""Apply one change to career.yaml's text while keeping its comments, order and layout.

`set_text` changes the text of one node (bullet, headline, summary, degree variant…) in one language;
`add_bullet` appends a bullet to an entry. Both use ruamel.yaml round-trip when it's installed and fall
back to a targeted text replacement otherwise (set_text only). Every result is re-parsed and checked:
the data must equal the old data with exactly that change, or the edit is refused.
"""
from __future__ import annotations

import copy
import io
import re
import textwrap
from typing import Optional

import yaml

WRAP = 110                  # folded block lines are wrapped like the hand-written ones


class EditError(ValueError):
    pass


def normalise(text: str) -> str:
    """One paragraph, single spaces — what a folded (>-) block holds."""
    return " ".join(str(text or "").split())


# ---------------- locating a node ----------------
def find_node(data, node_id: str):
    """First mapping with `id: node_id`, searching the whole document."""
    if isinstance(data, dict):
        if data.get("id") == node_id:
            return data
        for v in data.values():
            hit = find_node(v, node_id)
            if hit is not None:
                return hit
    elif isinstance(data, list):
        for v in data:
            hit = find_node(v, node_id)
            if hit is not None:
                return hit
    return None


def _target(node: dict, field: Optional[str]):
    """The mapping that holds the language keys: the node itself, or node[field] (e.g. title)."""
    if field is None:
        return node
    sub = node.get(field)
    if isinstance(sub, dict):
        return sub
    raise EditError(f"{node.get('id')}.{field} is not an {{en, fr}} mapping")


def _expected(old_text: str, node_id: str, lang: str, value: str, field: Optional[str]) -> dict:
    data = yaml.safe_load(old_text)
    node = find_node(data, node_id)
    if node is None:
        raise EditError(f"id {node_id!r} not found in career.yaml")
    _target(node, field)[lang] = value
    return data


def _verify(new_text: str, expected) -> str:
    try:
        got = yaml.safe_load(new_text)
    except yaml.YAMLError as e:          # pragma: no cover — guarded by the checks above
        raise EditError(f"edit produced invalid YAML: {e}") from e
    if got != expected:
        raise EditError("edit would change more than the target text — refused")
    return new_text


# ---------------- ruamel round-trip ----------------
def _ruamel():
    try:
        from ruamel.yaml import YAML
    except ImportError:
        return None
    y = YAML()
    y.preserve_quotes = True
    y.width = 4096                      # never re-wrap existing lines
    y.indent(mapping=2, sequence=4, offset=2)
    return y


def _folded(value: str, indent: int):
    """A ruamel folded scalar (>-) wrapped at about WRAP columns."""
    from ruamel.yaml.scalarstring import FoldedScalarString
    fs = FoldedScalarString(value)
    width = max(40, WRAP - indent)
    pos, line_start = [], 0
    for m in re.finditer(" ", value):
        nxt = value.find(" ", m.start() + 1)
        end = len(value) if nxt == -1 else nxt
        if end - line_start > width:
            pos.append(m.start())
            line_start = m.start() + 1
    fs.fold_pos = pos
    return fs


def _dump(y, data) -> str:
    out = io.StringIO()
    y.dump(data, out)
    return out.getvalue()


def _set_ruamel(y, text: str, node_id: str, lang: str, value: str, field: Optional[str]) -> str:
    from ruamel.yaml.scalarstring import FoldedScalarString
    data = y.load(text)
    node = find_node(data, node_id)
    if node is None:
        raise EditError(f"id {node_id!r} not found in career.yaml")
    target = _target(node, field)
    old = target.get(lang)
    indent = _key_indent(text, node_id) + 2
    flow = hasattr(target, "fa") and target.fa.flow_style()
    if not flow and (isinstance(old, FoldedScalarString) or len(value) > WRAP - indent - len(lang) - 2):
        value = _folded(value, indent)
    if old is None and "en" in target and lang != "en" and hasattr(target, "insert"):
        target.insert(list(target.keys()).index("en") + 1, lang, value)     # new translation goes under en
    else:
        target[lang] = value
    return _dump(y, data)


def _key_indent(text: str, node_id: str) -> int:
    m = re.search(rf"^(\s*)(- )?id: {re.escape(node_id)}\s*(#.*)?$", text, re.M)
    if not m:
        return 8
    return len(m.group(1)) + (2 if m.group(2) else 0)


# ---------------- text fallback ----------------
def _set_textual(text: str, node_id: str, lang: str, value: str) -> str:
    """Replace (or insert) `lang:` inside the block mapping that starts with `- id: node_id`."""
    lines = text.split("\n")
    start = next((i for i, ln in enumerate(lines)
                  if re.match(rf"^\s*(- )?id: {re.escape(node_id)}\s*(#.*)?$", ln)), None)
    if start is None:
        raise EditError(f"id {node_id!r} not found as a block mapping")
    m = re.match(r"^(\s*)(- )?", lines[start])
    ind = len(m.group(1)) + (2 if m.group(2) else 0)
    end = start + 1
    while end < len(lines) and (not lines[end].strip() or len(lines[end]) - len(lines[end].lstrip()) >= ind):
        if lines[end].strip() and len(lines[end]) - len(lines[end].lstrip()) == ind and lines[end].lstrip().startswith("- "):
            break
        end += 1
    while end > start + 1 and not lines[end - 1].strip():
        end -= 1
    width = max(40, WRAP - ind - 2)
    block = [f"{' ' * ind}{lang}: >-"] + [" " * (ind + 2) + ln for ln in textwrap.wrap(value, width)]

    def key_span(key):
        for i in range(start, end):
            if re.match(rf"^{' ' * ind}{key}:", lines[i]) or (i == start and re.match(rf"^\s*- {key}:", lines[i])):
                j = i + 1
                while j < end and (not lines[j].strip() or len(lines[j]) - len(lines[j].lstrip()) > ind):
                    j += 1
                while j > i + 1 and not lines[j - 1].strip():
                    j -= 1
                return i, j
        return None

    span = key_span(lang)
    if span:
        lines[span[0]:span[1]] = block
    else:
        after = key_span("en")
        at = after[1] if after else end
        lines[at:at] = block
    return "\n".join(lines)


# ---------------- public API ----------------
def set_text(text: str, node_id: str, lang: str, value: str, field: Optional[str] = None) -> str:
    """career.yaml text with node `node_id`'s `lang` text (or its `field`'s) replaced by `value`."""
    value = normalise(value)
    if not value:
        raise EditError("empty text — not saved")
    expected = _expected(text, node_id, lang, value, field)
    y = _ruamel()
    if y is not None:
        return _verify(_set_ruamel(y, text, node_id, lang, value, field), expected)
    if field is not None:
        raise EditError("editing nested fields needs ruamel.yaml (pip install ruamel.yaml)")
    return _verify(_set_textual(text, node_id, lang, value), expected)


def add_bullet(text: str, entry_id: str, bullet: dict) -> str:
    """career.yaml text with `bullet` appended to entry `entry_id`'s bullets (ruamel.yaml required)."""
    y = _ruamel()
    if y is None:
        raise EditError("adding bullets needs ruamel.yaml (pip install ruamel.yaml)")
    from ruamel.yaml.comments import CommentedMap
    bullet = {k: v for k, v in bullet.items() if v not in (None, "", [])}
    expected = yaml.safe_load(text)
    exp_entry = find_node(expected, entry_id)
    if exp_entry is None or not isinstance(exp_entry.get("bullets"), list):
        raise EditError(f"entry {entry_id!r} with bullets not found")
    if find_node(expected, bullet["id"]) is not None:
        raise EditError(f"id {bullet['id']!r} already exists")
    exp_entry["bullets"].append(copy.deepcopy(bullet))

    data = y.load(text)
    seq = find_node(data, entry_id)["bullets"]
    indent = _key_indent(text, seq[0]["id"]) + 2 if len(seq) else 10
    cm = CommentedMap()
    for k, v in bullet.items():
        cm[k] = _folded(v, indent) if k in ("en", "fr") and len(v) > WRAP - indent - 4 else v
    last = len(seq) - 1
    seq.append(cm)
    # comments after the old last bullet (e.g. the "# ----" line before the next entry) stay after the new one
    if last >= 0 and last in seq.ca.items:
        seq.ca.items[last + 1] = seq.ca.items.pop(last)
    last_map = seq[last] if last >= 0 else None
    if last_map is not None and getattr(last_map, "ca", None) is not None:
        tail_key = list(last_map.keys())[-1]
        if tail_key in last_map.ca.items:
            moved = last_map.ca.items.pop(tail_key)
            cm.ca.items[list(cm.keys())[-1]] = moved
    return _verify(_dump(y, data), expected)


def new_bullet_id(text: str, entry_id: str, words: str) -> str:
    """A readable, unused id like `eur_custom_lora_ablation`."""
    data = yaml.safe_load(text)
    entry = find_node(data, entry_id) or {}
    first = (entry.get("bullets") or [{}])[0].get("id", entry_id)
    prefix = first.split("_")[0] if "_" in first else entry_id
    stem = "_".join(re.findall(r"[a-z0-9]+", words.lower())[:3]) or "bullet"
    cand, n = f"{prefix}_{stem}", 2
    while find_node(data, cand) is not None:
        cand, n = f"{prefix}_{stem}_{n}", n + 1
    return cand
