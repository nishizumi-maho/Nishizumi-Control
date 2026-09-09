"""Tell the driver when a newer release of this product is published.

The check itself lives in ``core/updates.py`` and knows nothing about Tk; this
mixin owns the parts that do: the worker thread, the periodic schedule and the
two dialogs.  ``UPDATE_CHECK_AVAILABLE`` decides whether any of it runs, so the
edition that is not published never talks to GitHub.
"""

from __future__ import annotations

import threading
import webbrowser

from ..core.updates import (
    CHECK_INTERVAL_S,
    ReleaseCheck,
    fetch_latest_release,
    is_newer,
    releases_page_url,
)
from ..edition import (
    APP_DISPLAY_NAME,
    APP_VERSION,
    UPDATE_CHECK_AVAILABLE,
    UPDATE_REPO_NAME,
    UPDATE_REPO_OWNER,
)


class UpdatesMixin:
    """Update check for the published product."""

    def schedule_update_check(self) -> None:
        """Check once at startup, then every few hours."""

        if not UPDATE_CHECK_AVAILABLE:
            return
        self._check_for_updates_async(notify_when_current=False)
        self.root.after(
            int(CHECK_INTERVAL_S * 1000),
            self.schedule_update_check,
        )

    def check_for_updates_now(self) -> None:
        """Menu entry: say something even when there is nothing new."""

        if not UPDATE_CHECK_AVAILABLE:
            return
        self._check_for_updates_async(notify_when_current=True)

    def open_latest_release_page(self) -> None:
        """Menu entry: open the download page for the latest release."""

        webbrowser.open(releases_page_url(UPDATE_REPO_OWNER, UPDATE_REPO_NAME))

    def _check_for_updates_async(self, notify_when_current: bool) -> None:
        if getattr(self, "_update_check_running", False):
            if notify_when_current:
                self._show_info(
                    "Updates",
                    "A check is already running. Please wait.",
                )
            return
        self._update_check_running = True

        def _worker() -> None:
            result = fetch_latest_release(
                UPDATE_REPO_OWNER,
                UPDATE_REPO_NAME,
                user_agent=f"{APP_DISPLAY_NAME}/{APP_VERSION}",
            )
            self.ui(self._handle_update_result, result, notify_when_current)

        threading.Thread(
            target=_worker,
            name="ReleaseCheck",
            daemon=True,
        ).start()

    def _handle_update_result(
        self,
        result: ReleaseCheck,
        notify_when_current: bool,
    ) -> None:
        self._update_check_running = False
        if not result.ok:
            if notify_when_current:
                self._show_warning(
                    "Updates",
                    "The updates could not be checked.\n\n"
                    + (result.error or "Unknown error."),
                )
            return

        self._latest_release_version = result.version
        if is_newer(result.version, APP_VERSION):
            published = result.published_at or "unknown date"
            if self._ask_yes_no(
                "New version available",
                f'Installed version: {APP_VERSION}\nPublished version: {result.version}\nPublished on: {published}\n\nOpen the page of the newest release now?',
            ):
                webbrowser.open(
                    result.html_url
                    or releases_page_url(UPDATE_REPO_OWNER, UPDATE_REPO_NAME)
                )
            return

        if notify_when_current:
            self._show_info(
                "Updates",
                f'You are on the newest version.\n\nInstalled version: {APP_VERSION}\nLatest published: {result.version}',
            )


__all__ = ["UpdatesMixin"]
