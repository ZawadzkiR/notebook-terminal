from __future__ import annotations

import json
import os
import threading
import time
from collections import deque
from typing import Any, Callable
from urllib.parse import quote, urlencode, urlsplit, urlunsplit

import requests
import websocket

from .styling import Color, ansi_text

OutputCallback = Callable[[bytes], None]


class RemoteTerminalError(RuntimeError):
    """Raised when a remote Jupyter terminal cannot be created or connected."""


def _normalise_server_url(
    *,
    server_url: str | None,
    hub_url: str | None,
    username: str | None,
    server_name: str | None,
) -> str:
    if server_url:
        value = server_url.strip()
        if not value:
            raise ValueError("server_url cannot be empty")
        return value.rstrip("/") + "/"
    if not hub_url or not username:
        raise ValueError("Provide server_url, or both hub_url and username")
    base = hub_url.strip().rstrip("/")
    user = quote(username, safe="")
    if server_name:
        return f"{base}/user/{user}/{quote(server_name, safe='')}/"
    return f"{base}/user/{user}/"


def _websocket_url(http_url: str) -> str:
    parts = urlsplit(http_url)
    if parts.scheme == "https":
        scheme = "wss"
    elif parts.scheme == "http":
        scheme = "ws"
    elif parts.scheme in {"ws", "wss"}:
        scheme = parts.scheme
    else:
        raise ValueError(f"Unsupported URL scheme: {parts.scheme!r}")
    return urlunsplit((scheme, parts.netloc, parts.path, parts.query, parts.fragment))


class RemoteTerminalSession:
    """Terminal session backed by Jupyter Server REST and terminal WebSocket APIs.

    The class mirrors the public surface of :class:`TerminalSession` closely
    enough to be used by ``TerminalWidget``. Commands run with the permissions
    and environment of the remote Jupyter user server.
    """

    def __init__(
        self,
        *,
        token: str,
        server_url: str | None = None,
        hub_url: str | None = None,
        username: str | None = None,
        server_name: str | None = None,
        terminal_name: str | None = None,
        create_terminal: bool = True,
        delete_on_close: bool = True,
        verify_ssl: bool | str = True,
        request_timeout: float = 20.0,
        websocket_timeout: float = 30.0,
        extra_headers: dict[str, str] | None = None,
        cols: int = 100,
        rows: int = 30,
        capture_limit: int = 2_000_000,
        autostart: bool = True,
        allow_token_in_url: bool = False,
    ):
        if not token or not str(token).strip():
            raise ValueError("token is required")
        self.token = str(token).strip()
        self.server_url = _normalise_server_url(
            server_url=server_url,
            hub_url=hub_url,
            username=username,
            server_name=server_name,
        )
        self.username = username
        self.server_name = server_name
        self.terminal_name = terminal_name
        self.create_terminal = bool(create_terminal)
        self.delete_on_close = bool(delete_on_close)
        self.verify_ssl = verify_ssl
        self.request_timeout = float(request_timeout)
        self.websocket_timeout = float(websocket_timeout)
        self.extra_headers = dict(extra_headers or {})
        self.allow_token_in_url = bool(allow_token_in_url)

        self.shell = "remote-jupyter-terminal"
        self.cwd = None
        self.env: dict[str, str] = {}
        self.cols, self.rows = max(2, int(cols)), max(2, int(rows))
        self._capture_limit = int(capture_limit)
        self._capture = deque()
        self._capture_size = 0
        self._callbacks: list[OutputCallback] = []
        self._command_history: list[str] = []
        self._lock = threading.RLock()
        self._send_lock = threading.Lock()
        self._closed = False
        self._started = False
        self._connected = threading.Event()
        self._reader: threading.Thread | None = None
        self._ws: Any = None
        self._owns_terminal = False
        self._last_output_at = 0.0

        self._http = requests.Session()
        self._http.headers.update({"Authorization": f"token {self.token}"})
        self._http.headers.update(self.extra_headers)
        if autostart:
            self.start()


    def clone_kwargs(self) -> dict[str, Any]:
        """Return connection settings suitable for opening another remote terminal."""
        return {
            "token": self.token,
            "server_url": self.server_url,
            "create_terminal": True,
            "delete_on_close": self.delete_on_close,
            "verify_ssl": self.verify_ssl,
            "request_timeout": self.request_timeout,
            "websocket_timeout": self.websocket_timeout,
            "extra_headers": self.extra_headers,
            "cols": self.cols,
            "rows": self.rows,
            "capture_limit": self._capture_limit,
            "allow_token_in_url": self.allow_token_in_url,
        }

    @property
    def pid(self):
        return None

    @property
    def is_running(self) -> bool:
        return self._started and not self._closed and self._connected.is_set()

    @property
    def output_bytes(self) -> bytes:
        with self._lock:
            return b"".join(self._capture)

    @property
    def output(self) -> str:
        return self.output_bytes.decode("utf-8", "replace")

    @property
    def command_history(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(self._command_history)

    @property
    def last_command(self) -> str | None:
        with self._lock:
            return self._command_history[-1] if self._command_history else None

    def _rest_url(self, suffix: str = "") -> str:
        return self.server_url + "api/terminals" + suffix

    def _terminal_ws_url(self, name: str) -> str:
        url = _websocket_url(self.server_url + "terminals/websocket/" + quote(name, safe=""))
        if self.allow_token_in_url:
            separator = "&" if "?" in url else "?"
            url += separator + urlencode({"token": self.token})
        return url

    def _request(self, method: str, url: str, **kwargs):
        kwargs.setdefault("timeout", self.request_timeout)
        kwargs.setdefault("verify", self.verify_ssl)
        response = self._http.request(method, url, **kwargs)
        if response.status_code >= 400:
            body = response.text[:1000]
            raise RemoteTerminalError(
                f"Jupyter Server returned HTTP {response.status_code} for {method} {url}: {body}"
            )
        return response

    def _ensure_terminal(self) -> str:
        if self.terminal_name:
            if not self.create_terminal:
                self._request("GET", self._rest_url("/" + quote(self.terminal_name, safe="")))
            return self.terminal_name
        if not self.create_terminal:
            raise ValueError("terminal_name is required when create_terminal=False")
        response = self._request("POST", self._rest_url(), json={})
        try:
            model = response.json()
            name = str(model["name"])
        except Exception as exc:
            raise RemoteTerminalError("Invalid response while creating remote terminal") from exc
        self.terminal_name = name
        self._owns_terminal = True
        return name

    def start(self):
        with self._lock:
            if self._started and not self._closed:
                return self
            self._closed = False
            name = self._ensure_terminal()
            ws_url = self._terminal_ws_url(name)

            headers = [f"Authorization: token {self.token}"]
            for key, value in self.extra_headers.items():
                if key.lower() != "authorization":
                    headers.append(f"{key}: {value}")
            cookie = "; ".join(f"{k}={v}" for k, v in self._http.cookies.get_dict().items()) or None
            sslopt = None
            if self.verify_ssl is False:
                sslopt = {"cert_reqs": 0, "check_hostname": False}
            elif isinstance(self.verify_ssl, str):
                sslopt = {"ca_certs": self.verify_ssl}

            try:
                self._ws = websocket.create_connection(
                    ws_url,
                    header=headers,
                    cookie=cookie,
                    timeout=self.websocket_timeout,
                    sslopt=sslopt or {},
                    enable_multithread=True,
                )
            except Exception as exc:
                if self._owns_terminal and self.delete_on_close:
                    try:
                        self._request("DELETE", self._rest_url("/" + quote(name, safe="")))
                    except Exception:
                        pass
                raise RemoteTerminalError(f"Cannot connect to remote terminal WebSocket: {exc}") from exc

            self._started = True
            self._connected.set()
            self._reader = threading.Thread(target=self._read_loop, daemon=True)
            self._reader.start()
            self.resize(self.cols, self.rows)
        return self

    def subscribe(self, callback: OutputCallback, *, replay: bool = False):
        with self._lock:
            self._callbacks.append(callback)
            data = self.output_bytes if replay else b""
        if data:
            callback(data)
        return lambda: self.unsubscribe(callback)

    def unsubscribe(self, callback):
        with self._lock:
            try:
                self._callbacks.remove(callback)
            except ValueError:
                pass

    def clear_capture(self):
        with self._lock:
            self._capture.clear()
            self._capture_size = 0
        return self

    def _emit(self, data: bytes):
        if not data:
            return
        with self._lock:
            self._capture.append(data)
            self._capture_size += len(data)
            while self._capture_size > self._capture_limit and self._capture:
                self._capture_size -= len(self._capture.popleft())
            callbacks = tuple(self._callbacks)
            self._last_output_at = time.monotonic()
        for callback in callbacks:
            try:
                callback(data)
            except Exception:
                pass

    def emit_output(self, data: bytes | str):
        if isinstance(data, str):
            data = data.encode("utf-8", "replace")
        self._emit(data)
        return self

    def _read_loop(self):
        try:
            while not self._closed:
                try:
                    raw = self._ws.recv()
                except Exception as exc:
                    if not self._closed:
                        self._emit(f"\r\n[remote terminal disconnected: {exc}]\r\n".encode())
                    break
                if raw is None:
                    break
                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8", "replace")
                try:
                    message = json.loads(raw)
                except Exception:
                    continue
                if not isinstance(message, list) or not message:
                    continue
                kind = message[0]
                if kind == "stdout" and len(message) > 1:
                    value = message[1]
                    self._emit(value if isinstance(value, bytes) else str(value).encode("utf-8", "replace"))
                elif kind == "disconnect":
                    reason = message[1] if len(message) > 1 else "server closed the terminal"
                    self._emit(f"\r\n[remote terminal disconnected: {reason}]\r\n".encode())
                    break
        finally:
            self._connected.clear()

    def _send_message(self, payload: list[Any]):
        if self._closed or not self._ws:
            raise RemoteTerminalError("Remote terminal is closed")
        encoded = json.dumps(payload, ensure_ascii=False)
        with self._send_lock:
            try:
                self._ws.send(encoded)
            except Exception as exc:
                self._connected.clear()
                raise RemoteTerminalError(f"Cannot send data to remote terminal: {exc}") from exc
        return self

    def write(self, data: bytes | str):
        if isinstance(data, bytes):
            text = data.decode("utf-8", "replace")
        else:
            text = str(data)
        return self._send_message(["stdin", text])

    def send(self, text: str):
        return self.write(text)

    @staticmethod
    def _command_in_cwd(command: str, cwd: str | None) -> str:
        if cwd is None:
            return command
        import shlex
        return f"(cd -- {shlex.quote(str(cwd))} && {command})"

    def run(self, command: str, *, cwd: str | None = None, record_history: bool = True):
        """Execute a command on the remote Jupyter terminal.

        ``cwd`` applies only to this command. If omitted, a default directory
        configured on the remote session is used.
        """
        command = str(command)
        effective_cwd = self.cwd if cwd is None else cwd
        submitted = self._command_in_cwd(command, effective_cwd)
        if record_history and command.strip():
            with self._lock:
                self._command_history.append(command)
        return self.write(submitted + "\n")

    def resize(self, cols: int, rows: int):
        self.cols, self.rows = max(2, int(cols)), max(2, int(rows))
        if self._connected.is_set():
            self._send_message(["set_size", self.rows, self.cols, 0, 0])
        return self

    def history(self, limit: int | None = None) -> list[str]:
        items = list(self.command_history)
        if limit is None:
            return items
        if limit < 0:
            raise ValueError("limit must be non-negative")
        return items[-limit:] if limit else []

    def clear_history(self):
        with self._lock:
            self._command_history.clear()
        return self

    def rerun(self, index: int = -1):
        with self._lock:
            if not self._command_history:
                raise IndexError("command history is empty")
            command = self._command_history[index]
        return self.run(command)

    def run_many(self, commands, *, stop_on_error: bool = False):
        values = [str(command) for command in commands]
        if stop_on_error:
            return self.run(" && ".join(values))
        for command in values:
            self.run(command)
        return self

    def send_key(self, key: str):
        keys = {
            "ctrl+c": "\x03", "ctrl+d": "\x04", "ctrl+z": "\x1a",
            "enter": "\r", "tab": "\t", "escape": "\x1b", "esc": "\x1b",
        }
        normalized = str(key).strip().lower()
        if normalized not in keys:
            raise ValueError(f"Unsupported key: {key!r}")
        return self.write(keys[normalized])

    def interrupt(self):
        return self.send_key("ctrl+c")

    def wait_for(self, text: str, timeout: float = 10.0) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if text in self.output:
                return True
            time.sleep(0.02)
        return False

    def wait_until_idle(self, idle_for: float = 0.25, timeout: float = 30.0) -> bool:
        deadline = time.monotonic() + timeout
        stable_since = time.monotonic()
        last_size = len(self.output_bytes)
        while time.monotonic() < deadline:
            time.sleep(min(0.05, max(idle_for / 4, 0.01)))
            current_size = len(self.output_bytes)
            if current_size != last_size:
                last_size = current_size
                stable_since = time.monotonic()
            elif time.monotonic() - stable_since >= idle_for:
                return True
        return False

    def write_line(self, text: object = "", *, color: str | Color | None = None,
                   bold: bool = False, icon: str | None = None):
        prefix = f"{icon} " if icon else ""
        return self.emit_output(ansi_text(prefix + str(text), color=color, bold=bold) + "\r\n")

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

    @staticmethod
    def _resolve_interpreter(executable: str | None, interpreter: str | None, default: str = "python") -> str:
        if executable is not None and interpreter is not None and str(executable) != str(interpreter):
            raise ValueError("Provide either executable or interpreter, not both")
        return str(interpreter or executable or default)

    def run_python(self, code: str, executable: str | None = None, *, interpreter: str | None = None,
                   cwd: str | None = None, rich_output: bool = False, artifact_callback=None):
        if rich_output:
            raise NotImplementedError(
                "rich_output for remote terminals requires uploading helper files and is not available yet"
            )
        executable = self._resolve_interpreter(executable, interpreter)
        # stdin heredoc is shell-dependent; write a temporary script remotely via
        # base64 to keep quoting deterministic on POSIX JupyterHub deployments.
        import base64
        encoded = base64.b64encode(code.encode("utf-8")).decode("ascii")
        command = (
            f"{executable} -c \"import base64;exec(base64.b64decode('{encoded}').decode('utf-8'))\""
        )
        return self.run(command, cwd=cwd)

    def run_python_file(self, path: str, executable: str | None = None, *, interpreter: str | None = None,
                        cwd: str | None = None, rich_output: bool = False, args=None, kwargs=None,
                        artifact_callback=None):
        if rich_output:
            raise NotImplementedError("rich_output is not available for remote run_python_file")
        executable = self._resolve_interpreter(executable, interpreter)
        argv = [str(value) for value in (args or [])]
        for key, value in (kwargs or {}).items():
            option = str(key)
            if not option.startswith("-"):
                option = "--" + option.replace("_", "-")
            if value is True:
                argv.append(option)
            elif value is False or value is None:
                continue
            elif isinstance(value, (list, tuple)):
                for item in value:
                    argv.extend((option, str(item)))
            else:
                argv.extend((option, str(value)))
        import shlex
        command = " ".join(shlex.quote(part) for part in [executable, str(path), *argv])
        return self.run(command, cwd=cwd)

    def restart(self):
        history = list(self.command_history)
        self.close()
        self._closed = False
        self._started = False
        self._command_history = history
        self.terminal_name = None if self._owns_terminal else self.terminal_name
        self._owns_terminal = False
        return self.start()

    def close(self):
        with self._lock:
            if self._closed:
                return self
            self._closed = True
            ws = self._ws
            self._ws = None
            self._connected.clear()
        if ws is not None:
            try:
                ws.close()
            except Exception:
                pass
        if self.delete_on_close and self.terminal_name and self._owns_terminal:
            try:
                self._request("DELETE", self._rest_url("/" + quote(self.terminal_name, safe="")))
            except Exception:
                pass
        try:
            self._http.close()
        except Exception:
            pass
        return self
