"""The update check: what it asks GitHub, and what it does with the answer."""

from __future__ import annotations

import io
import json
import unittest
import urllib.error

import _stubs  # noqa: F401

from dominant_control.core.updates import (
    ReleaseCheck,
    fetch_latest_release,
    is_newer,
    parse_version,
    release_api_url,
    releases_page_url,
)


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        self.close()
        return False


def _payload(**fields) -> _Response:
    return _Response(json.dumps(fields).encode("utf-8"))


class VersionTests(unittest.TestCase):
    def test_a_tag_is_read_without_its_v(self):
        self.assertEqual((13,), parse_version("v13"))
        self.assertEqual((13, 1, 2), parse_version("13.1.2"))

    def test_a_missing_version_never_wins(self):
        self.assertEqual((0,), parse_version(""))
        self.assertFalse(is_newer("", "13"))

    def test_only_a_higher_release_counts_as_new(self):
        self.assertTrue(is_newer("14", "13"))
        self.assertTrue(is_newer("13.1", "13"))
        self.assertFalse(is_newer("13", "13"))
        self.assertFalse(is_newer("12", "13"))


class FetchTests(unittest.TestCase):
    def test_it_asks_for_the_latest_published_release(self):
        """``releases/latest`` is what skips drafts and pre-releases."""

        self.assertEqual(
            "https://api.github.com/repos/owner/repo/releases/latest",
            release_api_url("owner", "repo"),
        )
        self.assertEqual(
            "https://github.com/owner/repo/releases/latest",
            releases_page_url("owner", "repo"),
        )

    def test_a_release_comes_back_whole(self):
        captured = {}

        def opener(request, timeout=None):
            captured["url"] = request.full_url
            captured["headers"] = dict(request.header_items())
            captured["timeout"] = timeout
            return _payload(
                tag_name="v14",
                html_url="https://github.com/owner/repo/releases/tag/v14",
                published_at="2026-09-08T12:00:00Z",
            )

        result = fetch_latest_release(
            "owner", "repo", user_agent="Dominant Control/13", opener=opener
        )
        self.assertEqual(
            ReleaseCheck(
                ok=True,
                version="v14",
                html_url="https://github.com/owner/repo/releases/tag/v14",
                published_at="2026-09-08T12:00:00Z",
            ),
            result,
        )
        self.assertEqual(
            "https://api.github.com/repos/owner/repo/releases/latest",
            captured["url"],
        )
        self.assertEqual("Dominant Control/13", captured["headers"]["User-agent"])
        self.assertEqual(8.0, captured["timeout"])

    def test_a_release_without_a_page_falls_back_to_the_releases_page(self):
        result = fetch_latest_release(
            "owner",
            "repo",
            user_agent="ua",
            opener=lambda request, timeout=None: _payload(tag_name="v14"),
        )
        self.assertTrue(result.ok)
        self.assertEqual(
            "https://github.com/owner/repo/releases/latest", result.html_url
        )

    def test_an_http_error_is_reported_instead_of_raised(self):
        def opener(request, timeout=None):
            raise urllib.error.HTTPError(request.full_url, 404, "Not Found", {}, None)

        result = fetch_latest_release(
            "owner", "repo", user_agent="ua", opener=opener
        )
        self.assertFalse(result.ok)
        self.assertIn("404", result.error)

    def test_a_network_error_is_reported_instead_of_raised(self):
        def opener(request, timeout=None):
            raise urllib.error.URLError("offline")

        result = fetch_latest_release(
            "owner", "repo", user_agent="ua", opener=opener
        )
        self.assertFalse(result.ok)
        self.assertTrue(result.error)

    def test_a_release_with_no_tag_is_not_a_release(self):
        result = fetch_latest_release(
            "owner",
            "repo",
            user_agent="ua",
            opener=lambda request, timeout=None: _payload(html_url="x"),
        )
        self.assertFalse(result.ok)

    def test_without_a_repository_nothing_is_requested(self):
        def opener(request, timeout=None):  # pragma: no cover - must not run
            raise AssertionError("the check must not reach the network")

        result = fetch_latest_release("", "", user_agent="ua", opener=opener)
        self.assertFalse(result.ok)


if __name__ == "__main__":
    unittest.main()
