from .session import TerminalSession
from .styling import Color, ansi_text
from .web import TerminalManager

try:
    from .notebook import ManagedTerminalWidget, TerminalWidget, terminal
except ImportError:
    TerminalWidget = None
    ManagedTerminalWidget = None

    def terminal(*args, **kwargs):
        raise RuntimeError(
            "Jupyter UI requires anywidget, ipython and traitlets. "
            "Install the normal package dependencies."
        )

__all__ = [
    "TerminalSession",
    "Color",
    "ansi_text",
    "TerminalWidget",
    "ManagedTerminalWidget",
    "TerminalManager",
    "terminal",
]
__version__ = "0.1.1"
