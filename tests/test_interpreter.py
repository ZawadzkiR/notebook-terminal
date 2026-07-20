
import sys
import pytest

from notebook_terminal.session import TerminalSession
from notebook_terminal.remote import RemoteTerminalSession


def test_local_run_python_uses_interpreter(monkeypatch):
    session = TerminalSession(shell="/bin/bash", autostart=False)
    captured = {}
    monkeypatch.setattr(session, "run", lambda command, **kwargs: captured.update(command=command, kwargs=kwargs) or session)
    session.run_python("print(1)", interpreter="/opt/venv/bin/python", cwd="/tmp")
    assert "/opt/venv/bin/python" in captured["command"]
    assert captured["kwargs"]["cwd"] == "/tmp"


def test_local_run_python_file_uses_interpreter(tmp_path, monkeypatch):
    script = tmp_path / "main.py"
    script.write_text("print(1)", encoding="utf-8")
    session = TerminalSession(shell="/bin/bash", autostart=False)
    captured = {}
    monkeypatch.setattr(session, "run", lambda command, **kwargs: captured.update(command=command, kwargs=kwargs) or session)
    session.run_python_file("main.py", interpreter="python3.12", cwd=tmp_path)
    assert "python3.12" in captured["command"]


def test_conflicting_interpreter_aliases_raise():
    session = TerminalSession(shell="/bin/bash", autostart=False)
    with pytest.raises(ValueError):
        session.run_python("print(1)", executable="python", interpreter="python3")


def test_remote_interpreter_and_function_cwd(monkeypatch):
    session = RemoteTerminalSession(token="x", server_url="https://example.test/user/a/", autostart=False)
    sent = []
    monkeypatch.setattr(session, "write", lambda data: sent.append(data) or session)
    session.run_python_file("main.py", interpreter="/srv/venv/bin/python", cwd="/srv/project")
    assert "cd -- /srv/project" in sent[0]
    assert "/srv/venv/bin/python" in sent[0]


def test_remote_constructor_does_not_accept_cwd():
    with pytest.raises(TypeError):
        RemoteTerminalSession(token="x", server_url="https://example.test/user/a/", cwd="/srv/project", autostart=False)
