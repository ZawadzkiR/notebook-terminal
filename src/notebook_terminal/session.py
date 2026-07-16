from __future__ import annotations
import os, shlex, shutil, subprocess, threading, time, sys, tempfile, json
from collections import deque
from typing import Callable, Optional, Any

from .styling import Color, ansi_text

if os.name == 'posix':
    import errno, fcntl, pty, signal, struct, termios

OutputCallback = Callable[[bytes], None]

class TerminalSession:
    """Framework-independent PTY/ConPTY terminal session."""
    def __init__(self, *, shell: str|None=None, cwd: str|None=None, env: dict[str,str]|None=None,
                 cols: int=100, rows: int=30, capture_limit: int=2_000_000, autostart: bool=True):
        self.shell = shell or self.default_shell()
        self.cwd = os.path.abspath(cwd or os.getcwd())
        if not os.path.isdir(self.cwd): raise NotADirectoryError(self.cwd)
        self.env = dict(os.environ); self.env.update(env or {})
        self.env.setdefault('TERM','xterm-256color'); self.env.setdefault('COLORTERM','truecolor')
        self.cols, self.rows = cols, rows
        self._callbacks: list[OutputCallback] = []
        self._capture = deque(); self._capture_size=0; self._capture_limit=capture_limit
        self._lock=threading.RLock(); self._closed=False; self._started=False
        self._master_fd=None; self._pid=None; self._proc: Any=None; self._reader=None
        self._temp_files: list[str] = []
        self._command_history: list[str] = []
        if autostart: self.start()
    @staticmethod
    def default_shell() -> str:
        if os.name == 'nt':
            for x in ('pwsh.exe','powershell.exe'):
                if shutil.which(x): return x
            return os.environ.get('COMSPEC', r'C:\Windows\System32\cmd.exe')
        return os.environ.get('SHELL') or '/bin/bash'
    @property
    def pid(self):
        p=getattr(self._proc,'pid',None)
        return p() if callable(p) else (p or self._pid)
    @property
    def is_running(self):
        if self._closed or not self._started: return False
        if os.name=='nt':
            try: return bool(self._proc and self._proc.isalive())
            except Exception: return False
        if not self._pid: return False
        try: return os.waitpid(self._pid, os.WNOHANG)[0] == 0
        except ChildProcessError: return False
    def start(self):
        with self._lock:
            if self._started: return self
            if os.name=='nt': self._start_windows()
            elif os.name=='posix': self._start_posix()
            else: raise RuntimeError(f'Unsupported OS: {os.name}')
            self._started=True
            self._reader=threading.Thread(target=self._read_loop, daemon=True); self._reader.start()
        return self
    def _start_posix(self):
        pid, fd = pty.fork()
        if pid==0:
            os.chdir(self.cwd); os.execvpe(self.shell,[self.shell,'-i'],self.env)
        self._pid,self._master_fd=pid,fd; self.resize(self.cols,self.rows)
    def _start_windows(self):
        try: from winpty import PtyProcess
        except ImportError as e: raise RuntimeError('Windows requires pywinpty: pip install pywinpty') from e
        dims=(self.rows,self.cols)
        last=None
        for kwargs in ({'cwd':self.cwd,'env':self.env,'dimensions':dims},{'cwd':self.cwd,'env':self.env},{'cwd':self.cwd},{}):
            try: self._proc=PtyProcess.spawn(self.shell,**kwargs); break
            except TypeError as e: last=e
        if self._proc is None: raise RuntimeError(f'Cannot start ConPTY: {last}')
        self.resize(self.cols,self.rows)
    def subscribe(self, callback: OutputCallback, *, replay: bool=False):
        self._callbacks.append(callback)
        if replay:
            data=self.output_bytes
            if data: callback(data)
        return lambda: self.unsubscribe(callback)
    def unsubscribe(self, callback):
        try: self._callbacks.remove(callback)
        except ValueError: pass
    @property
    def output_bytes(self): return b''.join(self._capture)
    @property
    def output(self): return self.output_bytes.decode('utf-8','replace')
    def clear_capture(self): self._capture.clear(); self._capture_size=0
    def _emit(self, data: bytes):
        """Publish bytes to attached frontends and the capture buffer.

        This is the terminal's output channel. It must not be confused with
        :meth:`write`, which sends bytes to the shell's stdin.
        """
        if not data:
            return
        with self._lock:
            self._capture.append(data)
            self._capture_size += len(data)
            while self._capture_size > self._capture_limit and self._capture:
                self._capture_size -= len(self._capture.popleft())
            callbacks = tuple(self._callbacks)
        for callback in callbacks:
            try:
                callback(data)
            except Exception:
                pass

    def emit_output(self, data: bytes | str):
        """Emit display-only terminal output without executing shell input.

        Use this for application logs, status lines, ANSI messages, or any
        content that should appear in every attached frontend but must never be
        interpreted as a PowerShell, CMD, Bash, or Zsh command.
        """
        if isinstance(data, str):
            data = data.encode("utf-8", "replace")
        self._emit(data)
        return self
    def _read_loop(self):
        try:
            if os.name=='nt':
                while not self._closed:
                    try: chunk=self._proc.read(65536)
                    except TypeError: chunk=self._proc.read()
                    except (EOFError,OSError): break
                    if chunk: self._emit(chunk if isinstance(chunk,bytes) else str(chunk).encode())
                    elif not self.is_running: break
            else:
                while not self._closed:
                    try: chunk=os.read(self._master_fd,65536)
                    except OSError as e:
                        if e.errno in (errno.EIO,errno.EBADF): break
                        raise
                    if not chunk: break
                    self._emit(chunk)
        finally: self._closed=True
    def write(self,data:bytes|str):
        if isinstance(data,str): data=data.encode()
        with self._lock:
            if os.name=='nt': self._proc.write(data.decode('utf-8','replace'))
            else: os.write(self._master_fd,data)
        return self
    def send(self, text: str):
        """Send raw text to the shell input stream."""
        return self.write(text)

    def run(self, command: str, *, record_history: bool = True):
        """Execute a command in the persistent shell session."""
        command = str(command)
        if record_history and command.strip():
            with self._lock:
                self._command_history.append(command)
        return self.write(command + ('\r\n' if os.name == 'nt' else '\n'))

    @property
    def command_history(self) -> tuple[str, ...]:
        """Return commands submitted through :meth:`run`."""
        with self._lock:
            return tuple(self._command_history)

    def history(self, limit: int | None = None) -> list[str]:
        """Return programmatic command history, oldest first."""
        items = list(self.command_history)
        if limit is None:
            return items
        if limit < 0:
            raise ValueError("limit must be non-negative")
        return items[-limit:] if limit else []

    @property
    def last_command(self) -> str | None:
        with self._lock:
            return self._command_history[-1] if self._command_history else None

    def clear_history(self):
        with self._lock:
            self._command_history.clear()
        return self

    def rerun(self, index: int = -1):
        """Run a command from programmatic history by index."""
        with self._lock:
            if not self._command_history:
                raise IndexError("command history is empty")
            command = self._command_history[index]
        return self.run(command)

    def run_many(self, commands, *, stop_on_error: bool = False):
        """Submit multiple commands to the same shell.

        ``stop_on_error`` is implemented by the shell and is intended for simple
        command sequences. For precise exit-code handling, execute a script.
        """
        commands = [str(command) for command in commands]
        if stop_on_error:
            separator = ' && '
            return self.run(separator.join(commands))
        for command in commands:
            self.run(command)
        return self

    def write_line(self, text: object = "", *, color: str | Color | None = None,
                   bold: bool = False, icon: str | None = None):
        """Write a styled line directly to the terminal session.

        This does not execute a shell command. It emits ANSI text through the
        same PTY/ConPTY stream, so it works in Jupyter, Flask and Django views.
        """
        prefix = f"{icon} " if icon else ""
        value = ansi_text(prefix + str(text), color=color, bold=bold)
        # Status messages are frontend output, not shell input. Writing this to
        # stdin would make PowerShell try to execute ANSI fragments such as
        # ``[0m`` as commands.
        return self.emit_output(value + "\r\n")

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
    def _quote_command(self, args: list[str]) -> str:
        if os.name == 'nt':
            return subprocess.list2cmdline(args)
        return ' '.join(shlex.quote(arg) for arg in args)

    def _write_temp_python(self, code: str) -> str:
        fd, path = tempfile.mkstemp(prefix='nbterm_', suffix='.py', text=True)
        with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as handle:
            handle.write(code)
        self._temp_files.append(path)
        return path

    def _artifact_file(self) -> str:
        fd, path = tempfile.mkstemp(prefix='nbterm_artifacts_', suffix='.jsonl', text=True)
        os.close(fd)
        self._temp_files.append(path)
        return path

    def _watch_artifacts(self, path: str, callback):
        if callback is None:
            return
        def watch():
            position = 0
            idle_after_exit = 0
            while not self._closed:
                try:
                    with open(path, 'r', encoding='utf-8') as handle:
                        handle.seek(position)
                        while True:
                            line = handle.readline()
                            if not line:
                                position = handle.tell(); break
                            position = handle.tell()
                            try: callback(json.loads(line))
                            except Exception: pass
                except OSError:
                    pass
                if not self.is_running:
                    idle_after_exit += 1
                    if idle_after_exit > 10: break
                else:
                    idle_after_exit = 0
                time.sleep(.03)
        threading.Thread(target=watch, daemon=True).start()

    def run_python(self, code: str, executable: str | None = None, *, rich_output: bool = False, artifact_callback=None):
        executable = executable or sys.executable
        source_path = self._write_temp_python(code)
        if rich_output:
            runner = os.path.join(os.path.dirname(__file__), 'rich_runner.py')
            artifact_path = self._artifact_file()
            self._watch_artifacts(artifact_path, artifact_callback)
            cmd = self._quote_command([executable, runner, '--code', source_path, '--artifacts', artifact_path])
        else:
            cmd = self._quote_command([executable, source_path])
        return self.run(cmd)

    def run_python_file(self, path: str, executable: str | None = None, *, rich_output: bool = False, args: list[str] | None = None, artifact_callback=None):
        executable = executable or sys.executable
        full_path = os.path.abspath(path)
        if not os.path.isfile(full_path):
            raise FileNotFoundError(full_path)
        argv = list(args or [])
        if rich_output:
            runner = os.path.join(os.path.dirname(__file__), 'rich_runner.py')
            artifact_path = self._artifact_file()
            self._watch_artifacts(artifact_path, artifact_callback)
            command = [executable, runner, '--file', full_path, '--artifacts', artifact_path, *argv]
        else:
            command = [executable, full_path, *argv]
        return self.run(self._quote_command(command))
    def send_key(self, key: str):
        """Send a named control key to the process."""
        keys = {
            "ctrl+c": b"\x03", "ctrl+d": b"\x04", "ctrl+z": b"\x1a",
            "enter": b"\r\n" if os.name == "nt" else b"\n",
            "tab": b"\t", "escape": b"\x1b", "esc": b"\x1b",
        }
        normalized = str(key).strip().lower()
        if normalized not in keys:
            raise ValueError(f"Unsupported key: {key!r}")
        return self.write(keys[normalized])

    def interrupt(self):
        return self.send_key("ctrl+c")

    def wait_until_idle(self, idle_for: float = 0.25, timeout: float = 30.0) -> bool:
        """Wait until terminal output has stopped changing for ``idle_for`` seconds."""
        deadline = time.time() + timeout
        last_size = len(self.output_bytes)
        stable_since = time.time()
        while time.time() < deadline:
            time.sleep(min(0.05, max(idle_for / 4, 0.01)))
            current_size = len(self.output_bytes)
            if current_size != last_size:
                last_size = current_size
                stable_since = time.time()
            elif time.time() - stable_since >= idle_for:
                return True
        return False

    def restart(self):
        """Restart the shell while preserving configuration and command history."""
        history = list(self.command_history)
        self.close()
        self._closed = False
        self._started = False
        self._master_fd = None
        self._pid = None
        self._proc = None
        self._reader = None
        self._callbacks = list(self._callbacks)
        self._command_history = history
        self.start()
        return self
    def resize(self,cols:int,rows:int):
        self.cols,self.rows=max(2,cols),max(2,rows)
        if os.name=='nt' and self._proc:
            try:
                if hasattr(self._proc,'setwinsize'): self._proc.setwinsize(self.rows,self.cols)
                elif hasattr(self._proc,'set_size'): self._proc.set_size(self.cols,self.rows)
            except Exception: pass
        elif os.name=='posix' and self._master_fd is not None:
            try: fcntl.ioctl(self._master_fd,termios.TIOCSWINSZ,struct.pack('HHHH',self.rows,self.cols,0,0))
            except OSError: pass
        return self
    def wait_for(self,text:str,timeout:float=10):
        end=time.time()+timeout
        while time.time()<end:
            if text in self.output:return True
            time.sleep(.02)
        return False
    def close(self):
        self._closed=True
        if os.name=='nt' and self._proc:
            try:self._proc.terminate(True)
            except Exception: pass
        elif os.name=='posix':
            if self._pid:
                try:os.kill(self._pid,signal.SIGHUP)
                except Exception:pass
            if self._master_fd is not None:
                try:os.close(self._master_fd)
                except Exception:pass
        for path in tuple(self._temp_files):
            try: os.remove(path)
            except OSError: pass
        self._temp_files.clear()
        return self
