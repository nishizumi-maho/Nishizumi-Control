"""Overlay visual constants and stylesheet."""

COLOR_BG = "rgba(15, 17, 20, 235)"
COLOR_BORDER = "rgba(255, 255, 255, 40)"
COLOR_BORDER_UNLOCKED = "rgba(76, 215, 135, 190)"
COLOR_TEXT = "#E8E8E8"
COLOR_TEXT_DIM = "#9AA0A6"
COLOR_POSITIVE = "#4CD787"
COLOR_NEGATIVE = "#E5534B"
COLOR_NEUTRAL = "#E8E8E8"
COLOR_WARNING = "#E8B339"

FONT_FAMILY = "Consolas, 'Cascadia Mono', 'Courier New', monospace"


def stylesheet(locked: bool) -> str:
    """Full sheet for the current lock state.

    The border doubles as the lock indicator: a faint grey hairline while the
    overlay is click-through, a green outline while it can be dragged. Built as
    one string per state rather than patched with str.replace(), which silently
    stopped matching whenever a colour constant was reformatted.
    """
    border = COLOR_BORDER_UNLOCKED if not locked else COLOR_BORDER
    width = 2 if not locked else 1
    return f"""
QFrame#panel {{
    background-color: {COLOR_BG};
    border: {width}px solid {border};
    border-radius: 10px;
}}
QLabel#content {{
    background: transparent;
    color: {COLOR_TEXT};
    border: none;
    font-family: {FONT_FAMILY};
}}
QLabel#title {{
    background: transparent;
    color: {COLOR_TEXT_DIM};
    border: none;
    font-family: {FONT_FAMILY};
    font-size: 10px;
    letter-spacing: 1px;
}}
QWidget#header {{
    background: transparent;
    border: none;
}}
QPushButton#headerButton {{
    background: rgba(255, 255, 255, 18);
    color: {COLOR_TEXT};
    border: none;
    border-radius: 4px;
    padding: 1px 7px;
    font-family: {FONT_FAMILY};
    font-size: 11px;
}}
QPushButton#headerButton:hover {{
    background: rgba(255, 255, 255, 45);
}}
QPushButton#headerButton:pressed {{
    background: rgba(255, 255, 255, 70);
}}
QPushButton#closeButton {{
    background: rgba(229, 83, 75, 45);
    color: {COLOR_TEXT};
    border: none;
    border-radius: 4px;
    padding: 1px 8px;
    font-family: {FONT_FAMILY};
    font-size: 12px;
    font-weight: bold;
}}
QPushButton#closeButton:hover {{
    background: rgba(229, 83, 75, 220);
}}
QPushButton#closeButton:pressed {{
    background: rgba(229, 83, 75, 255);
}}
QMenu {{
    background-color: #14171B;
    color: {COLOR_TEXT};
    border: 1px solid {COLOR_BORDER};
    padding: 4px;
}}
QMenu::item {{
    padding: 5px 22px 5px 16px;
    border-radius: 4px;
}}
QMenu::item:selected {{
    background-color: rgba(76, 215, 135, 60);
}}
QMenu::separator {{
    height: 1px;
    background: {COLOR_BORDER};
    margin: 4px 8px;
}}
"""


# Kept for anything still importing the old name (the sheet is now state-driven).
BASE_STYLESHEET = stylesheet(locked=True)


def delta_color(value: float) -> str:
    if value > 0:
        return COLOR_POSITIVE
    if value < 0:
        return COLOR_NEGATIVE
    return COLOR_NEUTRAL
