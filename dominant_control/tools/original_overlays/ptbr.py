"""Presentation adapter for the preserved original overlay sources.

The English build keeps the vendors' own wording, so every helper here is a
pass-through.  The renderer workers still call them, which keeps this module's
contract identical in both languages.
"""

from __future__ import annotations

from typing import Any

EXACT_TRANSLATIONS: dict[str, str] = {}


def translate_sr_html(html: Any) -> Any:
    return html


def translate_text(value: Any) -> Any:
    return value


def localize_tk_tree(root: Any) -> None:
    return None


def localize_qt_tree(root: Any, qt_widgets: Any) -> None:
    return None
