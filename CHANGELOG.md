
## 0.4.1 (function API revision)

- Added `interpreter=` to `run_python()` and `run_python_file()` for local and remote sessions.
- Kept `executable=` as a backward-compatible alias.
- `cwd` remains function-level for `run()`, `run_python()`, and `run_python_file()`.
- Removed remote-session constructor `cwd`; remote commands choose their directory per call.

# Changelog

## 0.4.1

- Added `cwd` to `run()`, `run_python()` and `run_python_file()`.
- Added the same per-command working-directory support to local PTY/ConPTY and remote JupyterHub terminal backends.
- Added an optional default `cwd` for `remote_terminal()` and `terminal(backend="jupyterhub")`.
- Relative paths passed to local `run_python_file()` are resolved against `cwd`.
- A per-call `cwd` is temporary and does not permanently change the interactive shell directory.

## 0.4.0

- Added JupyterHub/Jupyter Server REST and WebSocket terminal backend.
