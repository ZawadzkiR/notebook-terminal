# Notebook Terminal

A cross-platform embeddable terminal for Jupyter Notebook, JupyterLab, Flask, Django, and custom Python frontends.

Notebook Terminal provides a persistent PTY/ConPTY session with real-time output, optional keyboard input, programmatic command execution, rich output tabs, interactive DataFrame viewing, Plotly support, and native Jupyter widgets.

> Version 0.1.1 is an alpha release. Test carefully before production use.

## Features

- POSIX PTY support on Linux and macOS
- ConPTY support on Windows through `pywinpty`
- PowerShell, CMD, Bash, Zsh, and other shells
- Real-time output streaming
- Interactive or read-only terminal mode
- Programmatic commands and process input
- Python code and Python-file execution
- Matplotlib and Seaborn image tabs
- Interactive Plotly tabs
- Interactive DataFrame viewer with filtering, sorting, and pagination
- Native `ipywidgets` through the active Jupyter kernel
- Closable rich-output tabs
- ANSI colors and status helpers
- Flask WebSocket and Django Channels adapters
- Framework-independent `TerminalSession`

## Installation

```bash
python -m pip install notebook-terminal
```

Install optional integrations:

```bash
python -m pip install "notebook-terminal[flask]"
python -m pip install "notebook-terminal[django]"
python -m pip install "notebook-terminal[data,plotly]"
```

For local development:

```bash
git clone https://github.com/YOUR-USERNAME/notebook-terminal.git
cd notebook-terminal
python -m pip install -e ".[dev]"
```

## Jupyter quick start

```python
from notebook_terminal import terminal

term = terminal(height=450)
```

Run a command from another cell:

```python
term.run("python --version")
```

`term.run()` uses the existing terminal. It does not display a second copy of the widget.

## Interactive and managed modes

Interactive terminal:

```python
term = terminal(interactive=True)
```

Read-only terminal controlled only through Python:

```python
term = terminal(interactive=False)
term.run("python application.py")
```

You can still send input programmatically:

```python
term.send_text("answer\r\n")
```

## Running commands

```python
term.run("git status")
term.run("pip list")
term.run("python script.py")
```

Create a separate terminal explicitly:

```python
second = term.run("python another_script.py", new_window=True)
```

## Running Python code

```python
term.run_python("""
import time

for value in range(5):
    print(value, flush=True)
    time.sleep(1)
""")
```

Run an existing file:

```python
term.run_python_file(
    "analysis.py",
    args=["input.csv", "--limit", "100"],
)
```

## Rich output

### Interactive DataFrame

```python
term.run_python("""
import pandas as pd

frame = pd.DataFrame({
    "city": ["Warsaw", "Krakow", "Gdansk", "Poznan"],
    "value": [12, 18, 9, 15],
})

display(frame)
""", rich_output=True, clear_previous=True)
```

The DataFrame tab supports filtering, sorting, pagination, page-size selection, and horizontal scrolling.

### Matplotlib

```python
term.run_python("""
import matplotlib.pyplot as plt

plt.plot([1, 2, 3, 4], [3, 7, 4, 8], marker="o")
plt.title("Example chart")
plt.show()
""", rich_output=True, clear_previous=True)
```

### Seaborn

```python
term.run_python("""
import pandas as pd
import seaborn as sns
import matplotlib.pyplot as plt

frame = pd.DataFrame({
    "category": ["A", "B", "C"],
    "value": [10, 17, 13],
})

sns.barplot(data=frame, x="category", y="value")
plt.show()
""", rich_output=True, clear_previous=True)
```

### Plotly

```python
term.run_python("""
import pandas as pd
import plotly.express as px

frame = pd.DataFrame({
    "category": ["A", "B", "C", "D"],
    "value": [10, 17, 13, 22],
})

figure = px.bar(frame, x="category", y="value", title="Interactive Plotly chart")
display(figure)
""", rich_output=True, clear_previous=True)
```

## Native Jupyter widgets

Jupyter widgets need the current notebook kernel and its Comm connection. Use `run_kernel()`:

```python
term.run_kernel("""
import ipywidgets as widgets
from IPython.display import display

slider = widgets.IntSlider(value=25, min=0, max=100, description="Value:")
label = widgets.Label()

slider.observe(
    lambda change: setattr(label, "value", f"Selected: {change['new']}"),
    names="value",
)

display(widgets.VBox([slider, label]))
""", clear_previous=True)
```

For interactive Matplotlib, install `ipympl` and use `%matplotlib widget` inside `run_kernel()`.

## Styled messages

```python
term.success("Code completed successfully")
term.error("Operation failed")
term.warning("Configuration file is missing")
term.info("Starting analysis")
term.debug("Current value: 42")
```

Custom styling:

```python
from notebook_terminal import Color

term.write_line(
    "Custom message",
    color=Color.GREEN,
    bold=True,
    icon="→",
)
```

Styled messages are emitted directly to the terminal output channel. They are not written to shell input, so PowerShell does not try to execute ANSI reset sequences such as `[0m`.

## Copy and paste

In interactive mode:

- select text and press `Ctrl+C` to copy
- press `Ctrl+C` without a selection to interrupt the active process
- press `Ctrl+V` to paste
- use the mouse wheel to scroll

In read-only mode, copying remains available, but keyboard input is not forwarded to the process.

## Process control

```python
term.send_text("answer\r\n")
term.interrupt()
term.clear()
term.clear(clear_tabs=True)
term.clear_tabs()
term.close_tab(0)
term.focus()
term.close_terminal()
```

## Framework-independent session

```python
from notebook_terminal import TerminalSession

session = TerminalSession()

session.subscribe(
    lambda chunk: print(chunk.decode("utf-8", errors="replace"), end="")
)

session.run("python --version")
session.success("Completed")
```

`session.write()` sends input to the shell. `session.emit_output()` publishes display-only output to attached frontends.

## Flask

```python
from flask import Flask, render_template
from notebook_terminal.web import flask_blueprint, manager

app = Flask(__name__)
app.register_blueprint(flask_blueprint(), url_prefix="/terminal")

@app.route("/")
def index():
    session_id, session = manager.create(cwd=".", interactive=True)
    return render_template("terminal.html", session_id=session_id)
```

Connect an xterm.js frontend to the WebSocket endpoint exposed by the adapter. Programmatic control remains available through the returned session object.

## Django

Install Django Channels and add the provided consumer to your ASGI WebSocket routes:

```python
from django.urls import re_path
from notebook_terminal.web import django_consumer

TerminalConsumer = django_consumer()

websocket_urlpatterns = [
    re_path(
        r"^ws/terminal/(?P<sid>[0-9a-f]+)/$",
        TerminalConsumer.as_asgi(),
    ),
]
```

## Architecture

```text
                         TerminalSession
                               |
             +-----------------+-----------------+
             |                 |                 |
       Jupyter widget     Flask WebSocket   Django Channels
             |                 |                 |
             +-----------------+-----------------+
                               |
                          PTY / ConPTY
                               |
                  PowerShell / CMD / Bash / Zsh
```

## Security

An embedded terminal executes commands with the permissions of the hosting Python process. Do not expose unrestricted terminal access publicly without authentication, authorization, session ownership checks, process limits, automatic cleanup, and operating-system or container isolation.

## Development and publishing

See [PUBLISHING.md](PUBLISHING.md) for complete build, GitHub, TestPyPI, and PyPI instructions.

## License

MIT License. See [LICENSE](LICENSE).
