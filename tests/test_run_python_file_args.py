from pathlib import Path

from notebook_terminal.session import TerminalSession


def make_session(tmp_path):
    session = TerminalSession(autostart=False)
    session.cwd = str(tmp_path)
    captured = []
    session.run = lambda command, **kwargs: captured.append(command) or command
    return session, captured


def test_positional_args_are_quoted_separately(tmp_path):
    script = tmp_path / "my script.py"
    script.write_text("print('ok')", encoding="utf-8")
    session, captured = make_session(tmp_path)
    session.run_python_file(script, executable="python", args=["one", "two words", 3])
    command = captured[-1]
    assert "my script.py" in command
    assert "two words" in command
    assert "3" in command


def test_kwargs_generate_options(tmp_path):
    script = tmp_path / "script.py"
    script.write_text("print('ok')", encoding="utf-8")
    session, captured = make_session(tmp_path)
    session.run_python_file(
        script,
        executable="python",
        kwargs={
            "epochs": 10,
            "learning_rate": 0.01,
            "verbose": True,
            "disabled": False,
            "missing": None,
            "tag": ["a", "b"],
        },
    )
    command = captured[-1]
    assert "--epochs" in command and "10" in command
    assert "--learning-rate" in command and "0.01" in command
    assert "--verbose" in command
    assert "--disabled" not in command
    assert "--missing" not in command
    assert command.count("--tag") == 2


def test_rich_output_keeps_script_args(tmp_path):
    script = tmp_path / "script.py"
    script.write_text("print('ok')", encoding="utf-8")
    session, captured = make_session(tmp_path)
    session._watch_artifacts = lambda *args, **kwargs: None
    session.run_python_file(
        script,
        executable="python",
        rich_output=True,
        args=["alpha", "two words"],
        kwargs={"count": 2},
    )
    command = captured[-1]
    assert "rich_runner.py" in command
    assert "alpha" in command
    assert "two words" in command
    assert "--count" in command and "2" in command
