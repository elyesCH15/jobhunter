"""Free-tier LLM gateway with a provider fallback chain.

All three providers expose an OpenAI-compatible chat endpoint, so one client covers them.
Order and models are configurable with env vars because free tiers and model names change:
  LLM_CHAIN=gemini,groq,openrouter
  GEMINI_MODEL / GROQ_MODEL / OPENROUTER_MODEL
A provider without its API key is skipped. If every provider fails, callers fall back to
rule-based results — the pipeline never stops because of the LLM.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from dataclasses import dataclass
from typing import Callable, Optional

import requests


@dataclass
class Provider:
    name: str
    url: str
    key_env: str
    model_env: str
    default_model: str

    @property
    def key(self) -> Optional[str]:
        return os.getenv(self.key_env) or None

    @property
    def model(self) -> str:
        return os.getenv(self.model_env) or self.default_model


PROVIDERS = {
    "gemini": Provider("gemini", "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
                       "GEMINI_API_KEY", "GEMINI_MODEL", "gemini-2.5-flash"),
    "groq": Provider("groq", "https://api.groq.com/openai/v1/chat/completions",
                     "GROQ_API_KEY", "GROQ_MODEL", "llama-3.3-70b-versatile"),
    "openrouter": Provider("openrouter", "https://openrouter.ai/api/v1/chat/completions",
                           "OPENROUTER_API_KEY", "OPENROUTER_MODEL", "meta-llama/llama-3.3-70b-instruct:free"),
}


class LLMUnavailable(Exception):
    pass


def extract_json(text: str):
    """Parse JSON from a model reply, tolerating ```json fences and leading prose."""
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    for opener, closer in (("[", "]"), ("{", "}")):
        start, end = text.find(opener), text.rfind(closer)
        if start != -1 and end > start:
            try:
                return json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                continue
    return json.loads(text)


class Gateway:
    def __init__(self, store=None, chain: Optional[list[str]] = None,
                 post: Callable = requests.post, sleep: Callable = time.sleep):
        names = chain or [n.strip() for n in os.getenv("LLM_CHAIN", "gemini,groq,openrouter").split(",") if n.strip()]
        self.providers = [PROVIDERS[n] for n in names if n in PROVIDERS]
        self.store = store
        self._post = post
        self._sleep = sleep
        self._cooldown: dict[str, float] = {}     # provider → time until which it's skipped

    @property
    def available(self) -> list[str]:
        return [p.name for p in self.providers if p.key]

    def _log(self, provider, model, task, ok, error=None, cached=False):
        if self.store:
            self.store.log_llm(provider=provider, model=model, task=task, ok=ok, error=error, cached=cached)

    def complete_json(self, system: str, user: str, task: str = "generic", max_tokens: int = 2048):
        cache_key = hashlib.sha256(f"{task}|{system}|{user}".encode()).hexdigest()
        if self.store:
            hit = self.store.cache_get(cache_key)
            if hit is not None:
                self._log("cache", None, task, True, cached=True)
                return hit, "cache"

        errors = []
        for p in self.providers:
            if not p.key:
                continue
            if self._cooldown.get(p.name, 0) > time.time():
                errors.append(f"{p.name}: cooling down")
                continue
            for attempt in range(2):
                try:
                    r = self._post(
                        p.url,
                        headers={"Authorization": f"Bearer {p.key}", "Content-Type": "application/json"},
                        json={"model": p.model, "temperature": 0.1, "max_tokens": max_tokens,
                              "messages": [{"role": "system", "content": system},
                                           {"role": "user", "content": user}]},
                        timeout=90,
                    )
                    if r.status_code == 429:                 # rate limited → next provider
                        self._cooldown[p.name] = time.time() + 60
                        raise RuntimeError("rate limited (429)")
                    if r.status_code >= 500 and attempt == 0:
                        self._sleep(3)
                        continue
                    r.raise_for_status()
                    content = r.json()["choices"][0]["message"]["content"]
                    data = extract_json(content)
                    self._log(p.name, p.model, task, True)
                    if self.store:
                        self.store.cache_put(cache_key, data)
                    return data, f"{p.name}/{p.model}"
                except Exception as e:  # noqa: BLE001 — any failure moves down the chain
                    err = str(e)[:300]
                    if attempt == 0 and "429" not in err and "json" in err.lower():
                        continue                              # one retry on malformed JSON
                    self._log(p.name, p.model, task, False, error=err)
                    errors.append(f"{p.name}: {err}")
                    break
        raise LLMUnavailable("; ".join(errors) or "no LLM provider configured")
