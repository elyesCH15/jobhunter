"""Read secrets and settings from the environment, always trimmed.

A key pasted into GitHub Secrets or Streamlit with a trailing tab or newline breaks URLs
(e.g. Jooble's https://jooble.org/api/<key>%09 → 403). Every key / URL goes through env().
"""
from __future__ import annotations

import os
from typing import Optional


def clean(value) -> Optional[str]:
    """Strip spaces, tabs and newlines; empty → None. Non-strings are returned unchanged."""
    if value is None:
        return None
    if not isinstance(value, str):
        return value
    value = value.strip()
    return value or None


def env(name: str, default: Optional[str] = None) -> Optional[str]:
    v = clean(os.getenv(name))
    return v if v is not None else default
