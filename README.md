# Notebook Terminal 0.5.1

A real PTY/ConPTY terminal embedded in Jupyter using bundled xterm.js and only standard `ipywidgets` channels. It needs no `anywidget`, JupyterLab extension, administrator installation, HTTP server, WebSocket, CDN, or internet connection.

## Installation

```bash
python -m pip install notebook_terminal-0.5.1-py3-none-any.whl
```

Restart the kernel after replacing an older version.

## Usage

```python
from notebook_terminal import terminal
term = terminal(height=450, interactive=True)
```

The shell prompt, username, host, and working directory come from the actual PowerShell, CMD, Bash, or Zsh process. They are not hardcoded.

## 0.5.0 transport changes

- lossless output packets with sequence numbers and acknowledgements;
- every frontend state contains all unacknowledged packets, so coalesced widget updates cannot drop output;
- exact byte transport preserves split UTF-8 and ANSI sequences;
- output batching every 25 ms and maximum 64 KiB packets;
- ordered, acknowledged input queue prevents lost keystrokes;
- xterm writes are serialized and acknowledged only after rendering;
- resize events and terminal control messages remain deduplicated.

This release specifically addresses missing characters, overwritten output, invisible fast commands such as `ls`, and severe lag caused by sending every PTY fragment as an independent widget trait update.
