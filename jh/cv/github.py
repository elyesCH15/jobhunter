"""Read and write career/career.yaml through the GitHub Contents API.

Streamlit Community Cloud can't commit to Git by itself, so "Save to database" in the CV editor
sends the edited file to GitHub with a fine-grained token (secrets GITHUB_TOKEN, GITHUB_REPO and
optionally GITHUB_BRANCH). Only career/career.yaml can be written: personal.yaml never goes this way.
"""
from __future__ import annotations

import base64
from typing import Callable, Optional

import requests

from .career import CAREER_PATH

API = "https://api.github.com"
WRITABLE = {CAREER_PATH}


class GitHubError(RuntimeError):
    pass


class Conflict(GitHubError):
    """The file changed on GitHub between our read and our write (sha mismatch)."""


class GitHubRepo:
    def __init__(self, token: str, repo: str, branch: Optional[str] = None, session=None, api: str = API):
        if not token or not repo or "/" not in repo:
            raise GitHubError("GITHUB_TOKEN and GITHUB_REPO (owner/name) are required")
        self.repo, self.branch, self.api = repo.strip().strip("/"), (branch or "main"), api.rstrip("/")
        self.s = session or requests.Session()
        self.headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                        "X-GitHub-Api-Version": "2022-11-28"}

    def _url(self, path: str) -> str:
        return f"{self.api}/repos/{self.repo}/contents/{path}"

    def read(self, path: str = CAREER_PATH) -> tuple[str, str]:
        """(text, sha) of a file on the branch."""
        r = self.s.get(self._url(path), params={"ref": self.branch}, headers=self.headers, timeout=20)
        if r.status_code != 200:
            raise GitHubError(f"GitHub read {path}: {r.status_code} {str(getattr(r, 'text', ''))[:200]}")
        body = r.json()
        return base64.b64decode(body["content"]).decode("utf-8"), body["sha"]

    def write(self, path: str, text: str, sha: str, message: str) -> str:
        """Commit `text` as the new content of `path` (replacing blob `sha`). Returns the new blob sha."""
        if path not in WRITABLE:
            raise GitHubError(f"refusing to write {path}: only {', '.join(sorted(WRITABLE))} can be saved from the dashboard")
        payload = {"message": message, "content": base64.b64encode(text.encode("utf-8")).decode("ascii"),
                   "sha": sha, "branch": self.branch}
        r = self.s.put(self._url(path), json=payload, headers=self.headers, timeout=30)
        if r.status_code in (409, 422):
            raise Conflict(f"GitHub write {path}: {r.status_code} (file changed meanwhile)")
        if r.status_code not in (200, 201):
            raise GitHubError(f"GitHub write {path}: {r.status_code} {str(getattr(r, 'text', ''))[:200]}")
        return r.json()["content"]["sha"]


def save_career(repo: GitHubRepo, edit: Callable[[str], str], message: str) -> tuple[str, str]:
    """Read career.yaml, apply `edit(text) -> new text`, commit it. Retries once if the file changed
    between read and write. Returns (new text, new sha)."""
    for attempt in (1, 2):
        text, sha = repo.read(CAREER_PATH)
        new = edit(text)
        if new == text:
            return text, sha
        try:
            return new, repo.write(CAREER_PATH, new, sha, message)
        except Conflict:
            if attempt == 2:
                raise
    raise GitHubError("unreachable")       # pragma: no cover


def from_settings(get: Callable[[str], Optional[str]]) -> Optional[GitHubRepo]:
    """GitHubRepo from secrets / env (GITHUB_TOKEN, GITHUB_REPO, GITHUB_BRANCH), or None if not set up."""
    token, repo = get("GITHUB_TOKEN"), get("GITHUB_REPO")
    if not (token and repo):
        return None
    return GitHubRepo(token, repo, get("GITHUB_BRANCH") or "main")
