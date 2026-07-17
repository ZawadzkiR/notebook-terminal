# Changelog

## 0\.2.0

* Removed the `anywidget` dependency and all custom widget model registration.
* Restored the full bundled xterm.js frontend through standard `ipywidgets` bridge channels.
* Added fully offline notebook operation with no CDN, local server, iframe, or extra port.
* Preserved PTY/ConPTY prompt output from the actual user shell; no prompt or user path is hardcoded.
* Kept kernel-loop dispatching for compatibility with ipykernel 6 and 7.
* Preserved Flask and Django terminal session integrations.

