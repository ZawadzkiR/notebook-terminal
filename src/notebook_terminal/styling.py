from __future__ import annotations

from enum import Enum


class Color(str, Enum):
    BLACK = "black"
    RED = "red"
    GREEN = "green"
    YELLOW = "yellow"
    BLUE = "blue"
    MAGENTA = "magenta"
    CYAN = "cyan"
    WHITE = "white"
    BRIGHT_BLACK = "bright_black"
    BRIGHT_RED = "bright_red"
    BRIGHT_GREEN = "bright_green"
    BRIGHT_YELLOW = "bright_yellow"
    BRIGHT_BLUE = "bright_blue"
    BRIGHT_MAGENTA = "bright_magenta"
    BRIGHT_CYAN = "bright_cyan"
    BRIGHT_WHITE = "bright_white"


ANSI_COLORS = {
    "black": 30, "red": 31, "green": 32, "yellow": 33,
    "blue": 34, "magenta": 35, "cyan": 36, "white": 37,
    "bright_black": 90, "bright_red": 91, "bright_green": 92,
    "bright_yellow": 93, "bright_blue": 94, "bright_magenta": 95,
    "bright_cyan": 96, "bright_white": 97,
}


def ansi_text(text: object, *, color: str | Color | None = None, bold: bool = False) -> str:
    value = str(text)
    codes: list[str] = []
    if bold:
        codes.append("1")
    if color is not None:
        name = color.value if isinstance(color, Color) else str(color).lower()
        code = ANSI_COLORS.get(name)
        if code is None:
            allowed = ", ".join(ANSI_COLORS)
            raise ValueError(f"Unknown color {color!r}. Allowed: {allowed}")
        codes.append(str(code))
    if not codes:
        return value
    return f"\x1b[{';'.join(codes)}m{value}\x1b[0m"
