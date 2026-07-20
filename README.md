# notebook-terminal

Cross-platform xterm.js terminal for Jupyter Notebook and JupyterLab, with local PTY/ConPTY and remote JupyterHub/Jupyter Server backends.

Repository: `https://github.com/ZawadzkiR/notebook-terminal`

## Installation

```bash
pip install notebook\_terminal-0.4.1-py3-none-any.whl
```

Restart the kernel and refresh JupyterLab after replacing an older build.

## Local terminal

```python
from notebook\_terminal import terminal

term = terminal(height=450)
term.run("python main.py", cwd="/home/user/project")
```

On Windows:

```python
term.run("python main.py", cwd=r"C:\\Users\\User\\project")
```

## Python code with a working directory

```python
term.run\_python(
    """
from utils import helper
helper()
""",
    cwd="/home/user/project",
)
```

## Python file, arguments and working directory

```python
term.run\_python\_file(
    "main.py",
    cwd="/home/user/project",
    args=\["input.csv", "two words"],
    kwargs={"limit": 100, "verbose": True},
)
```

For a local backend, a relative file path is resolved against `cwd`. The child Python process is run from that directory, so imports such as `from utils import helper` work when `utils.py` or the package is located there.

## Remote JupyterHub terminal

```python
import os
from notebook\_terminal import remote\_terminal

remote = remote\_terminal(
    server\_url="https://jupyter.example.com/user/<user>/",
    token=os.environ\["JUPYTERHUB\_TOKEN"],
)

remote.run("python main.py", cwd="/home/user/project")
remote.run\_python("from utils import helper; helper()", cwd="/home/user/project")
remote.run\_python\_file(
    "main.py",
    cwd="/home/user/project",
    args=\["input.csv"],
)
```

A default remote directory can also be set once:

```python
remote = remote\_terminal(
    server\_url="https://jupyter.example.com/user/user/",
    token=os.environ\["JUPYTERHUB\_TOKEN"],
    cwd="/home/user/project",
)

remote.run("git status")
remote.run\_python\_file("main.py")
```

The remote backend uses the Jupyter Server terminal REST API and terminal WebSocket. It does not require SSH. Remote `cwd` currently targets POSIX shells, which are standard on Linux JupyterHub installations.

## `cwd` behavior

* `cwd` on a single method call applies only to that command.
* The current interactive shell directory is restored after the command.
* `terminal(cwd=...)` sets the initial directory for a local terminal session.
* `remote\_terminal(cwd=...)` sets the default directory used for remote commands.
* A per-call `cwd` overrides the remote default.

## Main API

```python
term.run(command, cwd=None)
term.run\_python(code, cwd=None, rich\_output=False)
term.run\_python\_file(path, cwd=None, args=None, kwargs=None, rich\_output=False)
term.send\_text(text)
term.interrupt()
term.history()
term.success("Done")
```

## Security

Treat a JupyterHub token as a password. Do not commit it to Git or place it directly in a notebook that will be shared.

## Author

Robert Zawadzki — GitHub: `ZawadzkiR`



## Function-level working directory and Python interpreter

`cwd` is supplied to each operation. Remote connection creation does not set a working directory.

```python
term.run("git status", cwd="/home/user/project")
term.run\_python("from helpers import load\_data", cwd="/home/user/project", interpreter="python3.12")
term.run\_python\_file(
    "main.py",
    cwd="/home/user/project",
    interpreter="/home/user/.venvs/project/bin/python",
    args=\["input.csv"],
)
```

The same calls work with `remote\_terminal(...)`. `executable=` remains available as a backward-compatible alias for `interpreter=`; do not pass different values to both.

