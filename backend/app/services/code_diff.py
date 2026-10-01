"""Fetch the code change of a push from the GitHub API and prepare it for review.

The only outbound GitHub request OpsPilot makes: `GET api.github.com/repos/{repo}/compare/{a}...{b}`
(or `/commits/{sha}` for a branch's first push), for a repository configured in
MONITORED_PROJECTS, with commit SHAs that were validated as hex. A read-only GITHUB_TOKEN is used
only if configured. Before anything reaches the AI:
- lockfiles, binaries, images, minified/build output and secret files (.env, keys, certificates)
  are left out (and listed as skipped);
- anything that looks like a credential inside the diff is replaced with [REDACTED];
- the total size is capped (CODE_REVIEW_MAX_DIFF_CHARS); files that do not fit are listed.
"""

import re
from dataclasses import dataclass, field
from typing import Any

import httpx

API = "https://api.github.com"
MAX_FILES = 300
_SHA = re.compile(r"[0-9a-f]{7,40}")
_REPOSITORY = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")

LOCKFILES = {
    "package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "bun.lockb",
    "poetry.lock",
    "Pipfile.lock",
    "Cargo.lock",
    "composer.lock",
    "Gemfile.lock",
    "go.sum",
}
SECRET_FILE = re.compile(
    r"(^|/)(\.env(\..*)?|.*\.(pem|key|p12|pfx|jks|keystore)|id_(rsa|dsa|ecdsa|ed25519)"
    r"|credentials\.json|service[-_]?account.*\.json|secrets?\.(json|ya?ml|toml))$",
    re.IGNORECASE,
)
SKIPPED_EXTENSIONS = re.compile(
    r"\.(png|jpe?g|gif|webp|ico|bmp|svg|pdf|woff2?|ttf|otf|eot|mp3|mp4|wav|webm|zip|gz|tgz|"
    r"jar|exe|dll|so|wasm|map)$|\.min\.(js|css)$|(^|/)(dist|build|\.next|node_modules)/",
    re.IGNORECASE,
)
REDACTIONS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S),
        "[REDACTED PRIVATE KEY]",
    ),
    (re.compile(r"AKIA[0-9A-Z]{16}"), "[REDACTED]"),
    (re.compile(r"AIza[0-9A-Za-z_\-]{30,}"), "[REDACTED]"),
    (re.compile(r"gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}"), "[REDACTED]"),
    (re.compile(r"sk-[A-Za-z0-9_\-]{20,}|xox[baprs]-[A-Za-z0-9-]{10,}"), "[REDACTED]"),
    (re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"), "[REDACTED]"),
    (re.compile(r"(\b[a-z][a-z0-9+.-]*://[^:/\s@]+:)[^@\s]+@", re.I), r"\1[REDACTED]@"),
    (
        re.compile(
            r"""(?i)((?:password|passwd|pwd|secret|token|api[_-]?key|private[_-]?key|access[_-]?key"""
            r"""|client[_-]?secret|auth)["']?\s*[:=]\s*)(["']?)[^\s"',;]{6,}\2"""
        ),
        r"\1\2[REDACTED]\2",
    ),
)


class DiffUnavailable(Exception):
    """The change could not be fetched. Messages are safe to store and show."""


@dataclass
class PreparedDiff:
    files: list[dict[str, Any]] = field(default_factory=list)  # reviewed: name, status, +/-
    skipped: list[dict[str, str]] = field(default_factory=list)  # left out: name, reason
    truncated: bool = False
    redactions: int = 0
    text: str = ""

    @property
    def names(self) -> set[str]:
        return {f["filename"] for f in self.files}


def redact(patch: str) -> tuple[str, int]:
    total = 0
    for pattern, replacement in REDACTIONS:
        patch, count = pattern.subn(replacement, patch)
        total += count
    return patch, total


def _skip_reason(filename: str, patch: str | None) -> str | None:
    if SECRET_FILE.search(filename):
        return "secret or credentials file (never sent for review)"
    if filename.rsplit("/", 1)[-1] in LOCKFILES:
        return "lockfile"
    if SKIPPED_EXTENSIONS.search(filename):
        return "binary, asset or build output"
    if not patch:
        return "no text diff (binary, renamed or too large)"
    return None


def prepare(changes: list[dict[str, Any]], *, max_chars: int) -> PreparedDiff:
    prepared = PreparedDiff()
    parts: list[str] = []
    size = 0
    for change in changes[:MAX_FILES]:
        name = str(change.get("filename", ""))[:300]
        patch = change.get("patch")
        reason = _skip_reason(name, patch if isinstance(patch, str) else None)
        if reason:
            prepared.skipped.append({"filename": name, "reason": reason})
            continue
        clean, count = redact(str(patch))
        block = (
            f"### {name} ({change.get('status')}, +{change.get('additions', 0)} "
            f"-{change.get('deletions', 0)})\n{clean}\n"
        )
        if size + len(block) > max_chars:
            prepared.truncated = True
            prepared.skipped.append({"filename": name, "reason": "review size limit reached"})
            continue
        size += len(block)
        parts.append(block)
        prepared.redactions += count
        prepared.files.append(
            {
                "filename": name,
                "status": str(change.get("status", "")),
                "additions": int(change.get("additions", 0) or 0),
                "deletions": int(change.get("deletions", 0) or 0),
            }
        )
    if len(changes) > MAX_FILES:
        prepared.truncated = True
    prepared.text = "\n".join(parts)
    return prepared


async def fetch_changes(
    client: httpx.AsyncClient, repository: str, base: str | None, head: str, token: str | None
) -> list[dict[str, Any]]:
    """The changed files (with unified diff patches) between `base` and `head`."""
    if not _REPOSITORY.fullmatch(repository) or not _SHA.fullmatch(head):
        raise DiffUnavailable("invalid repository or commit")
    if base and _SHA.fullmatch(base) and base.strip("0"):
        url = f"{API}/repos/{repository}/compare/{base}...{head}"
    else:  # first push of a branch: the head commit alone
        url = f"{API}/repos/{repository}/commits/{head}"
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        response = await client.get(url, headers=headers)
    except httpx.HTTPError as exc:
        raise DiffUnavailable(f"GitHub API unreachable ({type(exc).__name__})") from None
    if response.status_code == 404:
        raise DiffUnavailable(
            "repository or commit not found on GitHub (a private repository needs GITHUB_TOKEN)"
        )
    if response.status_code in (403, 429):
        raise DiffUnavailable("GitHub API rate limit reached; retry later or set GITHUB_TOKEN")
    if response.status_code >= 400:
        raise DiffUnavailable(f"GitHub API returned HTTP {response.status_code}")
    files = response.json().get("files") or []
    return [f for f in files if isinstance(f, dict)]
