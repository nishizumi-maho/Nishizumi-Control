"""Ask GitHub which release is the latest one published for this product.

Only the public product looks for updates, and it looks at *published*
releases: ``/releases/latest`` is the endpoint that skips drafts and
pre-releases, so a release published for testing never reaches the drivers who
are running the current version.  ``edition.py`` says whether the check exists
at all and which repository answers it.

Everything here is plain data over ``urllib`` so the whole flow can be tested
without a network: ``fetch_latest_release`` takes the opener it should use.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Any, Callable
import urllib.error
import urllib.request


GITHUB_API_VERSION = "2022-11-28"
DEFAULT_TIMEOUT_S = 8.0
# Six hours: often enough to notice a release the same day, rare enough to be
# invisible to both the driver and GitHub's rate limit.
CHECK_INTERVAL_S = 6 * 60 * 60

_DIGITS = re.compile(r"\d+")


def release_api_url(owner: str, repo: str) -> str:
    """Endpoint of the latest published release (never a pre-release)."""

    return f"https://api.github.com/repos/{owner}/{repo}/releases/latest"


def releases_page_url(owner: str, repo: str) -> str:
    """Page a driver can open to download that same release."""

    return f"https://github.com/{owner}/{repo}/releases/latest"


def parse_version(version: str) -> tuple[int, ...]:
    """Turn ``v13``, ``13.1`` or ``13.1.2`` into something comparable."""

    cleaned = str(version or "").strip().lstrip("vV")
    digits = _DIGITS.findall(cleaned)
    return tuple(int(part) for part in digits) if digits else (0,)


def is_newer(latest: str, current: str) -> bool:
    """True when the published release is ahead of what is running."""

    return parse_version(latest) > parse_version(current)


@dataclass(frozen=True)
class ReleaseCheck:
    """Result of one check: either a release, or why there is none."""

    ok: bool
    version: str = ""
    html_url: str = ""
    published_at: str = ""
    error: str = ""


def fetch_latest_release(
    owner: str,
    repo: str,
    *,
    user_agent: str,
    timeout: float = DEFAULT_TIMEOUT_S,
    opener: Callable[..., Any] | None = None,
) -> ReleaseCheck:
    """Read the latest published release, or say why it could not be read."""

    if not owner or not repo:
        return ReleaseCheck(ok=False, error="Repository not configured.")

    request = urllib.request.Request(
        release_api_url(owner, repo),
        headers={
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": GITHUB_API_VERSION,
            "User-Agent": user_agent,
        },
        method="GET",
    )
    open_url = opener or urllib.request.urlopen
    try:
        with open_url(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return ReleaseCheck(ok=False, error=f"GitHub HTTP {exc.code}")
    except urllib.error.URLError as exc:
        return ReleaseCheck(ok=False, error=f'Network: {exc.reason}')
    except Exception as exc:  # noqa: BLE001 - any failure is just "no answer"
        return ReleaseCheck(ok=False, error=str(exc))

    if not isinstance(payload, dict):
        return ReleaseCheck(ok=False, error="Unexpected answer from GitHub.")

    version = str(payload.get("tag_name") or "").strip()
    if not version:
        return ReleaseCheck(ok=False, error="The answer carried no tag_name.")

    return ReleaseCheck(
        ok=True,
        version=version,
        html_url=str(payload.get("html_url") or "").strip()
        or releases_page_url(owner, repo),
        published_at=str(payload.get("published_at") or "").strip(),
    )


__all__ = [
    "CHECK_INTERVAL_S",
    "DEFAULT_TIMEOUT_S",
    "GITHUB_API_VERSION",
    "ReleaseCheck",
    "fetch_latest_release",
    "is_newer",
    "parse_version",
    "release_api_url",
    "releases_page_url",
]
