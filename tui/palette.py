"""Warm "ai4kernel" color palette + truecolor ANSI helpers.

Hex values are lifted verbatim from /amd4/cpu/ebpf-opt4/tui.py:35-44 (the RGB form
lives in agentic.py:594-616). Rendered as 24-bit ANSI; auto-disabled when stdout is
not a TTY or NO_COLOR is set.
"""
import os
import sys

# warm palette (hex)
AMBER    = "#e8a33d"   # tool names / accents
BORDER   = "#e0913a"   # panel border
GOLD     = "#f0b860"   # brand / headers / done
OFFWHITE = "#e8e0d0"   # assistant narration
DIM      = "#8a8276"   # thinking / labels
GREEN    = "#a7c080"   # success ✓
RED      = "#e06c5f"   # failure ✗ / errors
DOTS     = "#6b665d"   # rules
FRAME    = "#5a554d"   # outer frame

RESET = "\x1b[0m"
SPIN = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

ENABLED = sys.stdout.isatty() and os.environ.get("NO_COLOR") is None


def set_enabled(flag: bool):
    global ENABLED
    ENABLED = flag


def _rgb(hexcolor: str):
    h = hexcolor.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def fg(hexcolor: str, bold: bool = False, italic: bool = False) -> str:
    r, g, b = _rgb(hexcolor)
    return "\x1b[" + ("1;" if bold else "") + ("3;" if italic else "") + f"38;2;{r};{g};{b}m"


def paint(s: str, hexcolor: str, bold: bool = False, italic: bool = False) -> str:
    if not ENABLED or not s:
        return s
    return fg(hexcolor, bold, italic) + s + RESET


def rule(char: str = "─", width: int = 72) -> str:
    return paint(char * width, DOTS)
