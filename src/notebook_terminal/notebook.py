from __future__ import annotations

import ast
import base64
import contextlib
import io
import sys
import threading
import traceback
from pathlib import Path
from typing import Any

import anywidget
import ipywidgets as widgets
import traitlets
from IPython.display import display

from .session import TerminalSession
from .styling import Color, ansi_text

_STATIC = Path(__file__).with_name("static")


class _TerminalCanvas(anywidget.AnyWidget):
    _esm = _STATIC / "widget.js"
    _css = _STATIC / "all.css"

    height = traitlets.Int(420).tag(sync=True)
    font_size = traitlets.Int(14).tag(sync=True)
    scrollback = traitlets.Int(5000).tag(sync=True)
    interactive = traitlets.Bool(True).tag(sync=True)
    allow_copy = traitlets.Bool(True).tag(sync=True)
    allow_paste = traitlets.Bool(True).tag(sync=True)

    def push(self, data: bytes | str) -> None:
        if isinstance(data, str):
            data = data.encode("utf-8", "replace")
        if data:
            self.send({"type": "output", "data": base64.b64encode(data).decode("ascii")})




class _DataFrameView(anywidget.AnyWidget):
    _esm = _STATIC / "dataframe.js"
    _css = _STATIC / "dataframe.css"
    columns = traitlets.List(trait=traitlets.Unicode()).tag(sync=True)
    rows = traitlets.List(trait=traitlets.Dict()).tag(sync=True)


class _PlotlyView(anywidget.AnyWidget):
    _esm = _STATIC / "plotly_widget.js"
    spec = traitlets.Dict().tag(sync=True)


class _TerminalStream(io.TextIOBase):
    def __init__(self, widget: "TerminalWidget"):
        self.widget = widget
    def write(self, value: str) -> int:
        self.widget._queue_output(value)
        return len(value)
    def flush(self) -> None:
        self.widget._flush_output()


class TerminalWidget(widgets.VBox):
    """Terminal plus native Jupyter tabs for rich output and widgets."""

    def __init__(
        self,
        session: TerminalSession | None = None,
        *,
        interactive: bool = True,
        auto_display: bool = True,
        command: str | None = None,
        height: int = 420,
        font_size: int = 14,
        scrollback: int = 5000,
        allow_copy: bool = True,
        allow_paste: bool = True,
        rich_tabs: bool = True,
        **session_kwargs,
    ):
        self.session = session or TerminalSession(**session_kwargs)
        self.rich_tabs = bool(rich_tabs)
        self._canvas = _TerminalCanvas(
            height=height, font_size=font_size, scrollback=scrollback,
            interactive=interactive, allow_copy=allow_copy, allow_paste=allow_paste,
        )
        self._tab_items: list[tuple[str, widgets.Widget]] = []
        self._tab_buttons = widgets.HBox(layout=widgets.Layout(display="none", flex_flow="row wrap"))
        self._tab_stack = widgets.Stack(children=())
        self._tab_stack.layout.display = "none"
        self._tabs_box = widgets.VBox([self._tab_buttons, self._tab_stack])
        super().__init__([self._canvas, self._tabs_box])

        self._output_buffer: list[str] = []
        self._flush_timer: threading.Timer | None = None
        self._flush_lock = threading.Lock()
        self._namespace: dict[str, Any] = {"__name__": "__main__"}
        self._unsubscribe = self.session.subscribe(self._on_output, replay=True)
        self._canvas.on_msg(self._on_message)
        self._initial_command = command
        if auto_display:
            display(self)

    @property
    def interactive(self) -> bool: return bool(self._canvas.interactive)
    @property
    def height(self) -> int: return int(self._canvas.height)
    @property
    def font_size(self) -> int: return int(self._canvas.font_size)
    @property
    def scrollback(self) -> int: return int(self._canvas.scrollback)
    @property
    def allow_copy(self) -> bool: return bool(self._canvas.allow_copy)
    @property
    def allow_paste(self) -> bool: return bool(self._canvas.allow_paste)

    def _queue_output(self, data: bytes | str) -> None:
        text = data.decode("utf-8", "replace") if isinstance(data, bytes) else data
        if not text: return
        with self._flush_lock:
            self._output_buffer.append(text)
            size = sum(map(len, self._output_buffer))
            if size >= 65536:
                self._flush_output_locked()
            elif self._flush_timer is None:
                self._flush_timer = threading.Timer(.025, self._flush_output)
                self._flush_timer.daemon = True
                self._flush_timer.start()

    def _flush_output_locked(self) -> None:
        text = "".join(self._output_buffer)
        self._output_buffer.clear()
        self._flush_timer = None
        if text: self._canvas.push(text)

    def _flush_output(self) -> None:
        with self._flush_lock: self._flush_output_locked()

    def _on_output(self, data: bytes) -> None:
        self._queue_output(data)

    def _on_message(self, _widget, message, _buffers) -> None:
        kind = message.get("type")
        if kind == "ready":
            self.session.resize(int(message.get("cols", 80)), int(message.get("rows", 24)))
            if self._initial_command:
                self.session.run(self._initial_command); self._initial_command = None
        elif kind == "input" and self.interactive:
            self.session.write(base64.b64decode(message.get("data", "")))
        elif kind == "resize":
            self.session.resize(int(message.get("cols", 80)), int(message.get("rows", 24)))

    def _refresh_tabs(self, selected: int | None = None) -> None:
        buttons = []
        for idx, (title, _child) in enumerate(self._tab_items):
            select = widgets.Button(description=title, layout=widgets.Layout(width="auto"))
            close = widgets.Button(description="×", tooltip=f"Zamknij {title}", layout=widgets.Layout(width="32px"))
            select.on_click(lambda _b, i=idx: setattr(self._tab_stack, "selected_index", i))
            close.on_click(lambda _b, i=idx: self.close_tab(i))
            buttons.append(widgets.HBox([select, close], layout=widgets.Layout(width="auto")))
        self._tab_buttons.children = tuple(buttons)
        self._tab_stack.children = tuple(child for _title, child in self._tab_items)
        visible = bool(self._tab_items)
        self._tab_buttons.layout.display = "flex" if visible else "none"
        self._tab_stack.layout.display = "" if visible else "none"
        if visible:
            self._tab_stack.selected_index = min(selected if selected is not None else len(self._tab_items)-1, len(self._tab_items)-1)

    def close_tab(self, index: int) -> None:
        if 0 <= index < len(self._tab_items):
            _title, child = self._tab_items.pop(index)
            try: child.close()
            except Exception: pass
            self._refresh_tabs(max(0, index-1))

    def _add_tab(self, value: Any, title: str | None = None) -> None:
        if not self.rich_tabs:
            return
        if isinstance(value, dict) and value.get("kind") == "dataframe":
            child = _DataFrameView(columns=[str(x) for x in value.get("columns", [])], rows=value.get("rows", []))
            title = title or value.get("title") or "DataFrame"
        elif isinstance(value, dict) and value.get("kind") == "plotly":
            child = _PlotlyView(spec=value.get("spec", {}), layout=widgets.Layout(width="100%", min_height="340px"))
            title = title or value.get("title") or "Plotly"
        elif isinstance(value, dict) and value.get("kind") == "image":
            raw = base64.b64decode(value.get("data", "")); mime = str(value.get("mime", "image/png")); fmt=mime.split("/",1)[-1].replace("jpeg","jpg")
            child = widgets.Image(value=raw, format=fmt, layout=widgets.Layout(max_width="100%", width="auto")); title=title or value.get("title") or "Plot"
        elif isinstance(value, dict) and value.get("kind") == "html":
            child = widgets.HTML(value=str(value.get("html", "")), layout=widgets.Layout(width="100%", overflow="auto")); title=title or value.get("title") or "Output"
        elif isinstance(value, widgets.Widget):
            child=value; title=title or type(value).__name__
        else:
            try:
                import pandas as pd
                if isinstance(value, pd.DataFrame):
                    child=_DataFrameView(columns=[str(c) for c in value.reset_index().columns], rows=value.reset_index().where(value.reset_index().notna(), None).to_dict(orient="records")); title=title or "DataFrame"
                elif isinstance(value, pd.Series):
                    frame=value.to_frame().reset_index(); child=_DataFrameView(columns=[str(c) for c in frame.columns], rows=frame.where(frame.notna(),None).to_dict(orient="records")); title=title or "Series"
                else: raise TypeError
            except Exception:
                try:
                    import plotly.graph_objects as go
                    if isinstance(value, go.Figure):
                        import json
                        from plotly.utils import PlotlyJSONEncoder
                        spec=json.loads(json.dumps(value.to_plotly_json(), cls=PlotlyJSONEncoder))
                        child=_PlotlyView(spec=spec); title=title or "Plotly"
                    else: raise TypeError
                except Exception:
                    out=widgets.Output(layout=widgets.Layout(width="100%"))
                    with out: display(value)
                    child=out; title=title or type(value).__name__
        self._tab_items.append((str(title or f"Output {len(self._tab_items)+1}"), child))
        self._refresh_tabs(len(self._tab_items)-1)

    def show(self): display(self); return self

    def _new_window(self, command: str | None = None):
        return TerminalWidget(shell=self.session.shell, cwd=self.session.cwd, env=self.session.env,
            cols=self.session.cols, rows=self.session.rows, height=self.height,
            font_size=self.font_size, scrollback=self.scrollback, interactive=self.interactive,
            allow_copy=self.allow_copy, allow_paste=self.allow_paste, rich_tabs=self.rich_tabs,
            command=command, auto_display=True)

    def run(self, command: str, *, new_window: bool = False):
        if new_window: return self._new_window(command)
        self.session.run(command); return None

    def send_text(self, text: str, *, new_window: bool = False):
        if new_window:
            w=self._new_window(); w.session.send(text); return w
        self.session.send(text); return None

    def run_python(self, code: str, executable: str | None = None, *, rich_output: bool = False,
                   new_window: bool = False, clear_previous: bool = False):
        if new_window:
            w=self._new_window(); w.run_python(code, executable, rich_output=rich_output, clear_previous=clear_previous); return w
        if clear_previous: self.clear_tabs()
        self.session.run_python(code, executable, rich_output=rich_output,
                                artifact_callback=self._add_tab if rich_output else None)
        return None

    def run_python_file(self, path: str, executable: str | None = None, *, rich_output: bool = False,
                        args: list[str] | None = None, new_window: bool = False,
                        clear_previous: bool = False):
        if new_window:
            w=self._new_window(); w.run_python_file(path, executable, rich_output=rich_output, args=args, clear_previous=clear_previous); return w
        if clear_previous: self.clear_tabs()
        self.session.run_python_file(path, executable, rich_output=rich_output, args=args,
                                     artifact_callback=self._add_tab if rich_output else None)
        return None

    def run_kernel(self, code: str, *, clear_previous: bool = False, background: bool = False,
                   namespace: dict[str, Any] | None = None):
        """Execute in the current kernel. Supports live ipywidgets in tabs.

        This is intentionally separate from subprocess execution: Jupyter widgets
        need the current kernel's Comm channel and cannot be made interactive from
        an unrelated child Python process.
        """
        if clear_previous: self.clear_tabs()
        ns = namespace if namespace is not None else self._namespace

        def execute():
            old_display = None
            try:
                import IPython.display as ipd
                old_display = ipd.display
                def tab_display(*objects, **kwargs):
                    title = kwargs.get("display_id")
                    for obj in objects: self._add_tab(obj, str(title) if title else None)
                ipd.display = tab_display
                ns["display"] = tab_display
                tree = ast.parse(code, mode="exec")
                if tree.body and isinstance(tree.body[-1], ast.Expr):
                    last = tree.body[-1]
                    tree.body[-1] = ast.Expr(value=ast.Call(func=ast.Name("display", ast.Load()), args=[last.value], keywords=[]))
                    ast.fix_missing_locations(tree)
                stream = _TerminalStream(self)
                with contextlib.redirect_stdout(stream), contextlib.redirect_stderr(stream):
                    exec(compile(tree, "<notebook-terminal>", "exec"), ns, ns)
                # Capture standard matplotlib figures not explicitly displayed.
                try:
                    import matplotlib.pyplot as plt
                    for idx, num in enumerate(list(plt.get_fignums()), 1):
                        self._add_tab(plt.figure(num), f"Plot {idx}")
                    if plt.get_fignums(): plt.close("all")
                except Exception: pass
            except BaseException:
                self._queue_output(traceback.format_exc())
            finally:
                if old_display is not None:
                    try:
                        import IPython.display as ipd
                        ipd.display = old_display
                    except Exception: pass
                self._flush_output()
        if background:
            threading.Thread(target=execute, daemon=True).start(); return None
        execute(); return None

    def write_line(self, text: object = "", *, color=None, bold: bool = False,
                   icon: str | None = None):
        """Render a display-only line directly in xterm.js.

        This path never touches the PowerShell/CMD/Bash input stream, preventing
        ANSI reset sequences from being interpreted as commands on Windows.
        """
        prefix = f"{icon} " if icon else ""
        value = ansi_text(prefix + str(text), color=color, bold=bold) + "\r\n"
        self._queue_output(value)
        return None

    def success(self, text: object, *, bold: bool = False, icon: bool | str = True):
        marker = "✔" if icon is True else (str(icon) if icon else None)
        return self.write_line(text, color=Color.BRIGHT_GREEN, bold=bold, icon=marker)

    def error(self, text: object, *, bold: bool = False, icon: bool | str = True):
        marker = "✖" if icon is True else (str(icon) if icon else None)
        return self.write_line(text, color=Color.BRIGHT_RED, bold=bold, icon=marker)

    def warning(self, text: object, *, bold: bool = False, icon: bool | str = True):
        marker = "⚠" if icon is True else (str(icon) if icon else None)
        return self.write_line(text, color=Color.BRIGHT_YELLOW, bold=bold, icon=marker)

    def info(self, text: object, *, bold: bool = False, icon: bool | str = True):
        marker = "ℹ" if icon is True else (str(icon) if icon else None)
        return self.write_line(text, color=Color.BRIGHT_CYAN, bold=bold, icon=marker)

    def debug(self, text: object, *, bold: bool = False, icon: bool | str = True):
        marker = "•" if icon is True else (str(icon) if icon else None)
        return self.write_line(text, color=Color.BRIGHT_BLACK, bold=bold, icon=marker)

    @property
    def command_history(self) -> tuple[str, ...]:
        return self.session.command_history

    @property
    def last_command(self) -> str | None:
        return self.session.last_command

    def history(self, limit: int | None = None) -> list[str]:
        return self.session.history(limit)

    def clear_history(self):
        self.session.clear_history()
        return None

    def rerun(self, index: int = -1):
        self.session.rerun(index)
        return None

    def run_many(self, commands, *, stop_on_error: bool = False):
        self.session.run_many(commands, stop_on_error=stop_on_error)
        return None

    def send_key(self, key: str):
        self.session.send_key(key)
        return None

    def wait_for(self, text: str, timeout: float = 10.0) -> bool:
        return self.session.wait_for(text, timeout)

    def wait_until_idle(self, idle_for: float = 0.25, timeout: float = 30.0) -> bool:
        return self.session.wait_until_idle(idle_for, timeout)

    def restart(self, *, clear: bool = True):
        self.session.restart()
        if clear:
            self.clear()
        return None

    def interrupt(self): self.session.interrupt(); return None
    def clear(self, *, clear_tabs: bool=False):
        self._canvas.send({"type":"clear"})
        if clear_tabs: self.clear_tabs()
        return None
    def clear_tabs(self):
        for _title, child in self._tab_items:
            try: child.close()
            except Exception: pass
        self._tab_items.clear(); self._refresh_tabs(); return None
    def focus(self):
        if self.interactive: self._canvas.send({"type":"focus"})
        return None
    def set_interactive(self, enabled: bool): self._canvas.interactive=bool(enabled); return None
    @property
    def output(self) -> str: return self.session.output
    def close_terminal(self):
        self._flush_output(); self._unsubscribe(); self.session.close(); self.close()


class ManagedTerminalWidget(TerminalWidget):
    def __init__(self, *args, **kwargs): kwargs["interactive"] = False; super().__init__(*args, **kwargs)


def terminal(command: str | None = None, *, interactive: bool = True,
             auto_display: bool = True, **kwargs):
    return TerminalWidget(command=command, interactive=interactive,
                          auto_display=auto_display, **kwargs)
