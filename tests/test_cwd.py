from pathlib import Path

from notebook_terminal.session import TerminalSession
from notebook_terminal.remote import RemoteTerminalSession


def test_local_run_wraps_posix_cwd(tmp_path, monkeypatch):
    session = TerminalSession(shell="/bin/bash", autostart=False)
    sent = []
    monkeypatch.setattr(session, "write", lambda data: sent.append(data) or session)
    session.run("python main.py", cwd=tmp_path)
    assert "cd --" in sent[0]
    assert str(tmp_path) in sent[0]
    assert "python main.py" in sent[0]


def test_local_python_file_resolves_relative_to_cwd(tmp_path, monkeypatch):
    script = tmp_path / "main.py"
    script.write_text("print('ok')", encoding="utf-8")
    session = TerminalSession(shell="/bin/bash", autostart=False)
    captured = {}
    monkeypatch.setattr(session, "run", lambda command, **kwargs: captured.update(command=command, kwargs=kwargs) or session)
    session.run_python_file("main.py", cwd=tmp_path, args=["two words"])
    assert str(script) in captured["command"]
    assert captured["kwargs"]["cwd"] == str(tmp_path)
    assert "two words" in captured["command"]


def test_remote_run_wraps_cwd(monkeypatch):
    session = RemoteTerminalSession(token="x", server_url="https://example.test/user/a/", autostart=False)
    sent = []
    monkeypatch.setattr(session, "write", lambda data: sent.append(data) or session)
    session.run("ls -la", cwd="/home/a/my project")
    assert "cd -- '/home/a/my project'" in sent[0]
    assert "ls -la" in sent[0]
