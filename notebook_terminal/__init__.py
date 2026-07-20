from .session import TerminalSession
from .remote import RemoteTerminalError, RemoteTerminalSession
from .styling import Color, ansi_text
from .web import TerminalManager

try:
    from .notebook import ManagedTerminalWidget, TerminalWidget, remote_terminal, terminal
except ImportError:
    TerminalWidget = None
    ManagedTerminalWidget = None

    def terminal(*args, **kwargs):
        raise RuntimeError(
            "Jupyter UI requires ipywidgets, IPython and traitlets. "
            "Install the normal package dependencies."
        )

    def remote_terminal(*args, **kwargs):
        return terminal(*args, backend="jupyterhub", **kwargs)

__all__ = [
    "TerminalSession",
    "RemoteTerminalSession",
    "RemoteTerminalError",
    "Color",
    "ansi_text",
    "TerminalWidget",
    "ManagedTerminalWidget",
    "TerminalManager",
    "terminal",
    "remote_terminal",
]
__version__ = "0.4.1"
