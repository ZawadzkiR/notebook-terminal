# Changelog

## 0.1.1

- Fixed ANSI status messages being interpreted as PowerShell input.
- Added programmatic command history and rerun helpers.
- Added run_many, send_key, wait_until_idle, and restart helpers.


## 0.1.1

Initial public alpha release.

### Included

- PTY/ConPTY terminal sessions
- Jupyter terminal widget
- interactive and read-only modes
- real-time output streaming
- Python command and file execution
- DataFrame, Matplotlib, Seaborn, Plotly, and Jupyter widget tabs
- closable rich-output tabs
- Flask and Django adapters
- ANSI status helpers

### Fixed before release

- Styled messages now use the terminal output channel rather than shell stdin. This prevents PowerShell from interpreting ANSI reset fragments such as `[0m` as commands.
