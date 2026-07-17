from __future__ import annotations

import ast
import base64
import html
import json
import uuid
from pathlib import Path
import io
import re
import threading
import contextvars
import asyncio
from collections import deque
from typing import Any

import ipywidgets as widgets
from IPython.display import Javascript, display

from .session import TerminalSession
from .styling import Color, ansi_text

_STATIC = Path(__file__).with_name("static")

_ANSI_RE = re.compile(r"\x1b\[([0-9;]*)m")
_COLORS = {
    30: "#111827", 31: "#dc2626", 32: "#16a34a", 33: "#ca8a04",
    34: "#2563eb", 35: "#9333ea", 36: "#0891b2", 37: "#e5e7eb",
    90: "#6b7280", 91: "#ef4444", 92: "#22c55e", 93: "#eab308",
    94: "#3b82f6", 95: "#a855f7", 96: "#06b6d4", 97: "#ffffff",
}


def _ansi_to_html(text: str) -> str:
    """Convert the common ANSI SGR sequences used by shells to safe HTML."""
    parts: list[str] = []
    pos = 0
    color: str | None = None
    bold = False

    def open_span() -> str:
        styles = []
        if color:
            styles.append(f"color:{color}")
        if bold:
            styles.append("font-weight:700")
        return f'<span style="{";".join(styles)}">' if styles else ""

    active = False
    for match in _ANSI_RE.finditer(text):
        chunk = html.escape(text[pos:match.start()])
        if chunk:
            if active:
                parts.append("</span>")
            span = open_span()
            parts.append(span + chunk)
            active = bool(span)
        codes = [int(x) for x in match.group(1).split(";") if x] or [0]
        for code in codes:
            if code == 0:
                color = None
                bold = False
            elif code == 1:
                bold = True
            elif code == 22:
                bold = False
            elif code == 39:
                color = None
            elif code in _COLORS:
                color = _COLORS[code]
        pos = match.end()
    tail = html.escape(text[pos:])
    if tail:
        if active:
            parts.append("</span>")
        span = open_span()
        parts.append(span + tail)
        active = bool(span)
    if active:
        parts.append("</span>")
    return "".join(parts)


class _KernelDispatcher:
    """Marshal callbacks onto the Jupyter kernel event-loop safely.

    PTY readers and artifact watchers run in worker threads. ipywidgets must not
    be mutated from those threads, especially with ipykernel 7 where the parent
    message is stored in a ContextVar. This dispatcher captures the cell context
    at widget construction and executes queued UI work on the kernel loop.
    """

    def __init__(self):
        self._owner_thread = threading.get_ident()
        self._base_context = contextvars.copy_context()
        self._queue = deque()
        self._lock = threading.Lock()
        self._scheduled = False
        self._asyncio_loop = None
        self._io_loop = None
        try:
            self._asyncio_loop = asyncio.get_running_loop()
        except RuntimeError:
            pass
        try:
            from IPython import get_ipython
            shell = get_ipython()
            kernel = getattr(shell, "kernel", None)
            self._io_loop = getattr(kernel, "io_loop", None)
        except Exception:
            self._io_loop = None

    def is_owner_thread(self) -> bool:
        return threading.get_ident() == self._owner_thread

    def call(self, callback, *args, **kwargs) -> None:
        if self.is_owner_thread():
            callback(*args, **kwargs)
            return
        with self._lock:
            self._queue.append((callback, args, kwargs))
            if self._scheduled:
                return
            self._scheduled = True
        self._schedule_drain()

    def _schedule_drain(self) -> None:
        # Tornado's IOLoop is the most stable integration point across
        # ipykernel 6 and 7. asyncio is used as a fallback.
        if self._io_loop is not None:
            self._io_loop.add_callback(self._run_in_context)
            return
        if self._asyncio_loop is not None and self._asyncio_loop.is_running():
            self._asyncio_loop.call_soon_threadsafe(self._run_in_context)
            return
        # Last-resort fallback for non-Jupyter tests. It intentionally avoids
        # touching widgets from arbitrary threads in a real kernel.
        self._run_in_context()

    def _run_in_context(self) -> None:
        context = self._base_context.copy()
        context.run(self._drain)

    def _drain(self) -> None:
        while True:
            with self._lock:
                if not self._queue:
                    self._scheduled = False
                    return
                callback, args, kwargs = self._queue.popleft()
            try:
                callback(*args, **kwargs)
            except Exception:
                # UI delivery should not terminate PTY reader threads.
                pass


class _DataFrameView(widgets.VBox):
    """Portable DataFrame viewer built only from standard ipywidgets."""

    def __init__(self, columns: list[str], rows: list[dict[str, Any]], page_size: int = 25):
        self.columns = [str(c) for c in columns]
        self.rows = list(rows)
        self._page = 0
        self.search = widgets.Text(placeholder="Filter rows…", description="Filter:")
        self.page_size = widgets.Dropdown(options=[10, 25, 50, 100], value=page_size, description="Rows:")
        self.previous = widgets.Button(description="Previous")
        self.next = widgets.Button(description="Next")
        self.status = widgets.HTML()
        self.table = widgets.HTML(layout=widgets.Layout(width="100%", overflow_x="auto"))
        controls = widgets.HBox([self.search, self.page_size, self.previous, self.next, self.status])
        super().__init__([controls, self.table], layout=widgets.Layout(width="100%"))
        self.search.observe(self._changed, names="value")
        self.page_size.observe(self._changed, names="value")
        self.previous.on_click(lambda _: self._move(-1))
        self.next.on_click(lambda _: self._move(1))
        self._render()

    def _filtered(self) -> list[dict[str, Any]]:
        query = self.search.value.strip().casefold()
        if not query:
            return self.rows
        return [row for row in self.rows if any(query in str(row.get(col, "")).casefold() for col in self.columns)]

    def _changed(self, _change) -> None:
        self._page = 0
        self._render()

    def _move(self, delta: int) -> None:
        rows = self._filtered()
        pages = max(1, (len(rows) + self.page_size.value - 1) // self.page_size.value)
        self._page = min(max(0, self._page + delta), pages - 1)
        self._render()

    def _render(self) -> None:
        rows = self._filtered()
        size = self.page_size.value
        pages = max(1, (len(rows) + size - 1) // size)
        self._page = min(self._page, pages - 1)
        current = rows[self._page * size:(self._page + 1) * size]
        head = "".join(f"<th>{html.escape(c)}</th>" for c in self.columns)
        body = []
        for row in current:
            cells = "".join(f"<td>{html.escape(str(row.get(c, '')))}</td>" for c in self.columns)
            body.append(f"<tr>{cells}</tr>")
        self.table.value = (
            "<style>.nbt-table{border-collapse:collapse;width:100%;font-family:system-ui,sans-serif;font-size:13px}"
            ".nbt-table th,.nbt-table td{border:1px solid #d1d5db;padding:6px 8px;text-align:left;white-space:nowrap}"
            ".nbt-table th{background:#f3f4f6;position:sticky;top:0}</style>"
            f'<div style="max-height:420px;overflow:auto"><table class="nbt-table"><thead><tr>{head}</tr></thead>'
            f"<tbody>{''.join(body)}</tbody></table></div>"
        )
        self.status.value = f"<span>{len(rows)} rows · page {self._page + 1}/{pages}</span>"
        self.previous.disabled = self._page <= 0
        self.next.disabled = self._page >= pages - 1


class TerminalWidget(widgets.VBox):
    """Jupyter terminal UI using only standard ipywidgets.

    The notebook transport is entirely local to the Jupyter kernel and uses no
    custom widget extension, HTTP server, iframe, or WebSocket.
    Interactive input is line-oriented. Full-screen terminal applications such
    as vim/top require a JavaScript terminal emulator and are intentionally not
    supported by this portable fallback frontend.
    """

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
        self._interactive = bool(interactive)
        self._height = int(height)
        self._font_size = int(font_size)
        self._scrollback = int(scrollback)
        self._allow_copy = bool(allow_copy)
        self._allow_paste = bool(allow_paste)
        self._text = ""
        self._dispatcher = _KernelDispatcher()
        self._output_lock = threading.Lock()
        self._pending: list[bytes] = []
        self._pending_size = 0
        self._flush_timer = None
        self._output_packets = deque()
        self._next_output_seq = 1
        self._last_output_ack = 0
        self._last_input_seq = 0
        self._control_actions = deque(maxlen=32)
        self._next_control_seq = 1
        self._tab_items: list[tuple[str, widgets.Widget]] = []
        self._namespace: dict[str, Any] = {"__name__": "__main__"}

        self._bridge_id = "nbterm-" + uuid.uuid4().hex
        css = (_STATIC / "all.css").read_text(encoding="utf-8")
        self._screen = widgets.HTML(
            value=(
                f"<style>{css}</style>"
                f"<div id=\"{self._bridge_id}-terminal\" "
                f"style=\"width:100%;height:{height}px;min-height:{height}px\"></div>"
            ),
            layout=widgets.Layout(width="100%", height=f"{height}px"),
        )
        self._screen.add_class(self._bridge_id + "-screen")
        self._js_input = widgets.Textarea(value="", layout=widgets.Layout(display="none"))
        self._js_input.add_class(self._bridge_id + "-input")
        self._js_output = widgets.Textarea(value="", layout=widgets.Layout(display="none"))
        self._js_output.add_class(self._bridge_id + "-output")
        self._js_control = widgets.Textarea(value="", layout=widgets.Layout(display="none"))
        self._js_control.add_class(self._bridge_id + "-control")
        self._js_input.observe(self._bridge_input, names="value")
        self._bridge_output_seq = 0
        self._bridge_control_seq = 0

        self._tab_buttons = widgets.HBox(layout=widgets.Layout(display="none", flex_flow="row wrap"))
        self._tab_stack = widgets.Stack(children=())
        self._tab_stack.layout.display = "none"
        self._tabs_box = widgets.VBox([self._tab_buttons, self._tab_stack])
        super().__init__([
            self._screen,
            self._js_input,
            self._js_output,
            self._js_control,
            self._tabs_box,
        ], layout=widgets.Layout(width="100%"))

        self._unsubscribe = self.session.subscribe(self._on_output, replay=True)
        if command:
            self.session.run(command)
        if auto_display:
            display(self)
            display(Javascript(self._bridge_script()))

    @property
    def interactive(self) -> bool:
        return self._interactive

    @property
    def height(self) -> int:
        return self._height

    @property
    def font_size(self) -> int:
        return self._font_size

    @property
    def scrollback(self) -> int:
        return self._scrollback

    @property
    def allow_copy(self) -> bool:
        return self._allow_copy

    @property
    def allow_paste(self) -> bool:
        return self._allow_paste

    def _bridge_script(self) -> str:
        bundle = (_STATIC / "widget.js").read_text(encoding="utf-8")
        bundle = bundle.replace(
            "var n_={render:bl};export{n_ as default};",
            "window.__nbterm_render = bl;",
        )
        settings = {
            "height": self._height,
            "font_size": self._font_size,
            "scrollback": self._scrollback,
            "interactive": self._interactive,
            "allow_copy": self._allow_copy,
            "allow_paste": self._allow_paste,
        }
        bridge_id = json.dumps(self._bridge_id)
        settings_json = json.dumps(settings)
        return f"""
(() => {{
  const bridgeId = {bridge_id};
  const settings = {settings_json};
  const find = (suffix) => document.querySelector('.' + bridgeId + '-' + suffix);
  const inner = (suffix) => {{
    const outer = find(suffix);
    return outer ? (outer.querySelector('textarea,input') || outer) : null;
  }};
  const wait = () => {{
    const root = document.getElementById(bridgeId + '-terminal');
    const input = inner('input');
    const output = inner('output');
    const control = inner('control');
    if (!root || !input || !output || !control) {{ setTimeout(wait, 30); return; }}
    if (root.dataset.nbtermReady === '1') return;
    root.dataset.nbtermReady = '1';
    {bundle}
    const listeners = {{}};
    let nextInputSeq = 1;
    let inputAck = 0;
    let pendingInput = [];
    let publishTimer = null;
    const publishInput = () => {{
      publishTimer = null;
      if (!pendingInput.length) return;
      input.value = JSON.stringify({{events: pendingInput}});
      input.dispatchEvent(new Event('input', {{bubbles:true}}));
      input.dispatchEvent(new Event('change', {{bubbles:true}}));
    }};
    const scheduleInput = () => {{
      if (publishTimer === null) publishTimer = setTimeout(publishInput, 8);
    }};
    const model = {{
      get(name) {{ return settings[name]; }},
      on(name, callback) {{ (listeners[name] ||= []).push(callback); }},
      send(message) {{
        pendingInput.push({{seq: nextInputSeq++, message}});
        scheduleInput();
      }}
    }};
    window.__nbterm_render({{model, el: root}});
    let previousOutput = '';
    let previousControl = '';
    let lastActionSeq = 0;
    const poll = () => {{
      if (!document.body.contains(root)) return;
      if (output.value && output.value !== previousOutput) {{
        previousOutput = output.value;
        try {{
          const message = JSON.parse(output.value);
          (listeners['msg:custom'] || []).forEach(fn => fn(message));
        }} catch (error) {{ console.error('notebook-terminal output bridge', error); }}
      }}
      if (control.value && control.value !== previousControl) {{
        previousControl = control.value;
        try {{
          const state = JSON.parse(control.value);
          const ack = Number(state.input_ack || 0);
          if (ack > inputAck) {{
            inputAck = ack;
            pendingInput = pendingInput.filter(item => item.seq > ack);
            if (pendingInput.length) scheduleInput();
          }}
          for (const action of (state.actions || [])) {{
            if (action.seq <= lastActionSeq) continue;
            lastActionSeq = action.seq;
            const message = action.message || {{}};
            if (message.type === 'interactive') {{
              settings.interactive = !!message.value;
              (listeners['change:interactive'] || []).forEach(fn => fn());
            }} else {{
              (listeners['msg:custom'] || []).forEach(fn => fn(message));
            }}
          }}
        }} catch (error) {{ console.error('notebook-terminal control bridge', error); }}
      }}
      setTimeout(poll, 12);
    }};
    poll();
  }};
  wait();
}})();
"""

    def _bridge_input(self, change) -> None:
        raw = change.get("new", "")
        if not raw:
            return
        try:
            envelope = json.loads(raw)
        except Exception:
            return
        events = envelope.get("events") or []
        for event in sorted(events, key=lambda item: int(item.get("seq", 0))):
            seq = int(event.get("seq", 0))
            if seq <= self._last_input_seq:
                continue
            message = event.get("message") or {}
            kind = message.get("type")
            if kind == "ready":
                self.session.resize(int(message.get("cols", 80)), int(message.get("rows", 24)))
            elif kind == "input" and self._interactive:
                try:
                    self.session.write(base64.b64decode(message.get("data", "")))
                except Exception:
                    pass
            elif kind == "resize":
                self.session.resize(int(message.get("cols", 80)), int(message.get("rows", 24)))
            elif kind == "output_ack":
                self._ack_output(int(message.get("seq", 0)))
            self._last_input_seq = seq
        self._publish_control_state()

    def _publish_control_state(self) -> None:
        payload = {
            "input_ack": self._last_input_seq,
            "actions": list(self._control_actions),
        }
        self._js_control.value = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))

    def _queue_control(self, message: dict[str, Any]) -> None:
        self._control_actions.append({"seq": self._next_control_seq, "message": message})
        self._next_control_seq += 1
        self._publish_control_state()

    def _ack_output(self, seq: int) -> None:
        if seq <= self._last_output_ack:
            return
        self._last_output_ack = seq
        while self._output_packets and self._output_packets[0][0] <= seq:
            self._output_packets.popleft()
        if self._output_packets:
            self._publish_output_state()

    def _on_output(self, data: bytes) -> None:
        if not data:
            return
        with self._output_lock:
            self._pending.append(bytes(data))
            self._pending_size += len(data)
            if self._flush_timer is not None:
                return
            self._flush_timer = threading.Timer(0.025, lambda: self._dispatcher.call(self._flush))
            self._flush_timer.daemon = True
            self._flush_timer.start()

    def _flush(self) -> None:
        with self._output_lock:
            chunks = self._pending
            self._pending = []
            self._pending_size = 0
            self._flush_timer = None
        if not chunks:
            return
        data = b"".join(chunks)
        # Keep individual Comm payloads moderate while preserving exact bytes.
        for offset in range(0, len(data), 65536):
            part = data[offset:offset + 65536]
            seq = self._next_output_seq
            self._next_output_seq += 1
            encoded = base64.b64encode(part).decode("ascii")
            self._output_packets.append((seq, encoded))
        self._publish_output_state()

    def _publish_output_state(self) -> None:
        if not self._output_packets:
            return
        # The newest state always contains every unacknowledged packet. Trait
        # update coalescing therefore cannot lose output.
        items = [{"seq": seq, "data": data} for seq, data in self._output_packets]
        payload = {"type": "output_batch", "items": items, "upto": items[-1]["seq"]}
        self._js_output.value = json.dumps(payload, separators=(",", ":"))

    def _refresh_tabs(self, selected: int | None = None) -> None:
        buttons = []
        for idx, (title, _child) in enumerate(self._tab_items):
            select = widgets.Button(description=title, layout=widgets.Layout(width="auto"))
            close = widgets.Button(description="×", tooltip=f"Close {title}", layout=widgets.Layout(width="32px"))
            select.on_click(lambda _b, i=idx: setattr(self._tab_stack, "selected_index", i))
            close.on_click(lambda _b, i=idx: self.close_tab(i))
            buttons.append(widgets.HBox([select, close], layout=widgets.Layout(width="auto")))
        self._tab_buttons.children = tuple(buttons)
        self._tab_stack.children = tuple(child for _title, child in self._tab_items)
        visible = bool(self._tab_items)
        self._tab_buttons.layout.display = "flex" if visible else "none"
        self._tab_stack.layout.display = "" if visible else "none"
        if visible:
            target = len(self._tab_items) - 1 if selected is None else selected
            self._tab_stack.selected_index = min(max(0, target), len(self._tab_items) - 1)

    def close_tab(self, index: int) -> None:
        if 0 <= index < len(self._tab_items):
            _title, child = self._tab_items.pop(index)
            try:
                child.close()
            except Exception:
                pass
            self._refresh_tabs(max(0, index - 1))

    def _add_tab(self, value: Any, title: str | None = None) -> None:
        if not self._dispatcher.is_owner_thread():
            self._dispatcher.call(self._add_tab, value, title)
            return
        if not self.rich_tabs:
            return
        child: widgets.Widget
        if isinstance(value, dict) and value.get("kind") == "dataframe":
            child = _DataFrameView([str(x) for x in value.get("columns", [])], value.get("rows", []))
            title = title or value.get("title") or "DataFrame"
        elif isinstance(value, dict) and value.get("kind") == "plotly":
            out = widgets.Output(layout=widgets.Layout(width="100%", min_height="340px"))
            try:
                import plotly.graph_objects as go
                figure = go.Figure(value.get("spec", {}))
                out.append_display_data(figure)
            except Exception as exc:
                out.append_stderr(f"Unable to render Plotly figure: {exc}\n")
            child = out
            title = title or value.get("title") or "Plotly"
        elif isinstance(value, dict) and value.get("kind") == "image":
            raw = base64.b64decode(value.get("data", ""))
            mime = str(value.get("mime", "image/png"))
            fmt = mime.split("/", 1)[-1].replace("jpeg", "jpg")
            child = widgets.Image(value=raw, format=fmt, layout=widgets.Layout(max_width="100%", width="auto"))
            title = title or value.get("title") or "Plot"
        elif isinstance(value, dict) and value.get("kind") == "html":
            child = widgets.HTML(value=str(value.get("html", "")), layout=widgets.Layout(width="100%", overflow="auto"))
            title = title or value.get("title") or "Output"
        elif isinstance(value, widgets.Widget):
            child = value
            title = title or type(value).__name__
        else:
            try:
                import pandas as pd
                if isinstance(value, pd.DataFrame):
                    frame = value.reset_index().where(lambda x: x.notna(), None)
                    child = _DataFrameView([str(c) for c in frame.columns], frame.to_dict(orient="records"))
                    title = title or "DataFrame"
                elif isinstance(value, pd.Series):
                    frame = value.to_frame().reset_index().where(lambda x: x.notna(), None)
                    child = _DataFrameView([str(c) for c in frame.columns], frame.to_dict(orient="records"))
                    title = title or "Series"
                else:
                    raise TypeError
            except Exception:
                out = widgets.Output(layout=widgets.Layout(width="100%"))
                out.append_display_data(value)
                child = out
                title = title or type(value).__name__
        self._tab_items.append((str(title or f"Output {len(self._tab_items) + 1}"), child))
        self._refresh_tabs(len(self._tab_items) - 1)

    def show(self):
        display(self)
        display(Javascript(self._bridge_script()))
        return self

    def _new_window(self, command: str | None = None):
        return TerminalWidget(
            shell=self.session.shell,
            cwd=self.session.cwd,
            env=self.session.env,
            cols=self.session.cols,
            rows=self.session.rows,
            height=self.height,
            font_size=self.font_size,
            scrollback=self.scrollback,
            interactive=self.interactive,
            allow_copy=self.allow_copy,
            allow_paste=self.allow_paste,
            rich_tabs=self.rich_tabs,
            command=command,
            auto_display=True,
        )

    def run(self, command: str, *, new_window: bool = False):
        if new_window:
            return self._new_window(command)
        self.session.run(command)
        return None

    def send_text(self, text: str, *, new_window: bool = False):
        if new_window:
            widget = self._new_window()
            widget.session.send(text)
            return widget
        self.session.send(text)
        return None

    def run_python(self, code: str, executable: str | None = None, *, rich_output: bool = False,
                   new_window: bool = False, clear_previous: bool = False):
        if new_window:
            widget = self._new_window()
            widget.run_python(code, executable, rich_output=rich_output, clear_previous=clear_previous)
            return widget
        if clear_previous:
            self.clear_tabs()
        self.session.run_python(code, executable, rich_output=rich_output,
                                artifact_callback=self._add_tab if rich_output else None)
        return None

    def run_python_file(self, path: str, executable: str | None = None, *, rich_output: bool = False,
                        args: list[str] | None = None, new_window: bool = False,
                        clear_previous: bool = False):
        if new_window:
            widget = self._new_window()
            widget.run_python_file(path, executable, rich_output=rich_output, args=args,
                                   clear_previous=clear_previous)
            return widget
        if clear_previous:
            self.clear_tabs()
        self.session.run_python_file(path, executable, rich_output=rich_output, args=args,
                                     artifact_callback=self._add_tab if rich_output else None)
        return None

    def run_kernel(self, code: str, *, clear_previous: bool = False, background: bool = False,
                   namespace: dict[str, Any] | None = None):
        if clear_previous:
            self.clear_tabs()
        ns = namespace if namespace is not None else self._namespace
        ns.setdefault("display", lambda *objects, **_kwargs: [self._add_tab(obj) for obj in objects])

        def execute() -> None:
            try:
                tree = ast.parse(code, mode="exec")
                if tree.body and isinstance(tree.body[-1], ast.Expr):
                    last = tree.body[-1]
                    tree.body[-1] = ast.Expr(value=ast.Call(
                        func=ast.Name(id="display", ctx=ast.Load()), args=[last.value], keywords=[]))
                    ast.fix_missing_locations(tree)
                exec(compile(tree, "<notebook-terminal-kernel>", "exec"), ns, ns)
            except BaseException as exc:
                self.error(f"{type(exc).__name__}: {exc}")

        if background:
            thread = threading.Thread(target=execute, daemon=True)
            thread.start()
            return thread
        execute()
        return None

    def write_line(self, text: object = "", *, color: str | Color | None = None,
                   bold: bool = False, icon: str | None = None):
        prefix = f"{icon} " if icon else ""
        self.session.emit_output(ansi_text(prefix + str(text), color=color, bold=bold) + "\r\n")
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
        return self.write_line(text, color=Color.BRIGHT_MAGENTA, bold=bold, icon=marker)

    def history(self, limit: int | None = None) -> list[str]:
        return self.session.history(limit)

    @property
    def last_command(self) -> str | None:
        return self.session.last_command

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

    def interrupt(self):
        self.session.interrupt()
        return None

    def wait_for(self, text: str, timeout: float = 30.0) -> bool:
        return self.session.wait_for(text, timeout=timeout)

    def wait_until_idle(self, idle_for: float = 0.5, timeout: float = 30.0) -> bool:
        return self.session.wait_until_idle(idle_for=idle_for, timeout=timeout)

    def restart(self, *, clear: bool = True):
        self.session.restart()
        if clear:
            self.clear()
        return None

    def clear(self, *, clear_tabs: bool = False):
        self.session.clear_capture()
        self._queue_control({"type": "clear"})
        if clear_tabs:
            self.clear_tabs()
        return None

    def clear_tabs(self):
        for _title, child in self._tab_items:
            try:
                child.close()
            except Exception:
                pass
        self._tab_items.clear()
        self._refresh_tabs()
        return None

    def set_interactive(self, value: bool):
        self._interactive = bool(value)
        self._queue_control({"type": "interactive", "value": self._interactive})
        return None

    def focus(self):
        self._queue_control({"type": "focus"})
        return None

    def close_terminal(self):
        try:
            self._unsubscribe()
        except Exception:
            pass
        self.session.close()
        self.close()
        return None


class ManagedTerminalWidget(TerminalWidget):
    def __init__(self, *args, **kwargs):
        kwargs["interactive"] = False
        super().__init__(*args, **kwargs)


def terminal(*args, **kwargs) -> TerminalWidget:
    return TerminalWidget(*args, **kwargs)
