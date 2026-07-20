import base64
import json

from notebook_terminal.notebook import TerminalWidget
from notebook_terminal.session import TerminalSession


def make_widget():
    session = TerminalSession(autostart=False)
    return TerminalWidget(session=session, auto_display=False)


def test_output_snapshot_is_lossless_and_acknowledged():
    widget = make_widget()
    widget._on_output(b"abc")
    # Avoid timer timing in unit test.
    with widget._output_lock:
        timer = widget._flush_timer
        widget._flush_timer = None
    if timer:
        timer.cancel()
    widget._flush()
    state = json.loads(widget._js_output.value)
    assert state["type"] == "output_batch"
    assert base64.b64decode(state["items"][0]["data"]) == b"abc"
    seq = state["upto"]
    widget._ack_output(seq)
    assert not widget._output_packets


def test_split_utf8_is_preserved_as_bytes():
    widget = make_widget()
    raw = "zażółć".encode("utf-8")
    widget._on_output(raw[:4])
    widget._on_output(raw[4:])
    with widget._output_lock:
        timer = widget._flush_timer
        widget._flush_timer = None
    if timer:
        timer.cancel()
    widget._flush()
    state = json.loads(widget._js_output.value)
    rebuilt = b"".join(base64.b64decode(item["data"]) for item in state["items"])
    assert rebuilt == raw


def test_input_events_are_deduplicated_and_ordered():
    widget = make_widget()
    written = []
    widget.session.write = lambda data: written.append(data)
    events = {
        "events": [
            {"seq": 2, "message": {"type": "input", "data": base64.b64encode(b"b").decode()}},
            {"seq": 1, "message": {"type": "input", "data": base64.b64encode(b"a").decode()}},
        ]
    }
    widget._bridge_input({"new": json.dumps(events)})
    widget._bridge_input({"new": json.dumps(events)})
    assert written == [b"a", b"b"]
    assert json.loads(widget._js_control.value)["input_ack"] == 2


def test_xterm_converts_lf_to_new_line():
    from pathlib import Path
    import notebook_terminal
    widget_js = Path(notebook_terminal.__file__).with_name("static") / "widget.js"
    source = widget_js.read_text(encoding="utf-8")
    assert "convertEol:!0" in source
    assert "convertEol:!1" not in source


def test_notebook_transport_keeps_raw_bytes():
    from pathlib import Path
    import notebook_terminal
    notebook_py = Path(notebook_terminal.__file__).with_name("notebook.py")
    source = notebook_py.read_text(encoding="utf-8")
    assert 'self._pending.append(bytes(data))' in source
    assert 'base64.b64encode(part)' in source
    assert 'data.decode("utf-8", "replace")' not in source

def test_bridge_translates_output_batch_to_xterm_output():
    widget = TerminalWidget(auto_display=False, interactive=False)
    try:
        script = widget._bridge_script()
        assert "message.type === 'output_batch'" in script
        assert "fn({type:'output', data:item.data})" in script
        assert "type:'output_ack'" in script
    finally:
        widget.close_terminal()
