from __future__ import annotations

import ast
import base64
import html
import json
import os
import runpy
import sys
import traceback
from pathlib import Path
from typing import Any, TextIO


def _writer(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    handle: TextIO = path.open("a", encoding="utf-8", buffering=1)

    def emit(payload: dict[str, Any]) -> None:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
        handle.flush()

    return handle, emit


def _capture_factory(emit):
    def emit_html(value: str, title: str = "Output") -> None:
        emit({"kind": "html", "title": title, "html": value})

    def emit_png(data: bytes, title: str = "Plot") -> None:
        emit({
            "kind": "image",
            "title": title,
            "mime": "image/png",
            "data": base64.b64encode(data).decode("ascii"),
        })

    def capture(value: Any, *, title: str | None = None) -> None:
        if value is None:
            return
        try:
            import pandas as pd  # type: ignore
            if isinstance(value, pd.DataFrame):
                frame = value.reset_index()
                frame = frame.where(frame.notna(), None)
                emit({"kind": "dataframe", "title": title or "DataFrame", "columns": [str(c) for c in frame.columns], "rows": frame.to_dict(orient="records")})
                return
            if isinstance(value, pd.Series):
                frame = value.to_frame().reset_index().where(lambda x: x.notna(), None)
                emit({"kind": "dataframe", "title": title or "Series", "columns": [str(c) for c in frame.columns], "rows": frame.to_dict(orient="records")})
                return
        except Exception:
            pass
        try:
            import plotly.graph_objects as go  # type: ignore
            if isinstance(value, go.Figure):
                from plotly.utils import PlotlyJSONEncoder  # type: ignore
                spec = json.loads(json.dumps(value.to_plotly_json(), cls=PlotlyJSONEncoder))
                emit({"kind": "plotly", "title": title or "Plotly", "spec": spec})
                return
        except Exception:
            pass
        try:
            from PIL import Image  # type: ignore
            if isinstance(value, Image.Image):
                import io
                buf = io.BytesIO(); value.save(buf, format="PNG")
                emit_png(buf.getvalue(), title or "Image")
                return
        except Exception:
            pass
        repr_html = getattr(value, "_repr_html_", None)
        if callable(repr_html):
            try:
                rendered = repr_html()
                if rendered:
                    emit_html(str(rendered), title or type(value).__name__)
                    return
            except Exception:
                pass
        repr_png = getattr(value, "_repr_png_", None)
        if callable(repr_png):
            try:
                rendered = repr_png()
                if rendered:
                    emit_png(rendered, title or type(value).__name__)
                    return
            except Exception:
                pass
        emit_html(f"<pre>{html.escape(repr(value))}</pre>", title or type(value).__name__)

    return capture, emit_png


def _execute_code(path: Path, capture) -> None:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path), mode="exec")
    if tree.body and isinstance(tree.body[-1], ast.Expr):
        last = tree.body[-1]
        tree.body[-1] = ast.Expr(value=ast.Call(
            func=ast.Name(id="__nbterm_display__", ctx=ast.Load()),
            args=[last.value], keywords=[]))
        ast.fix_missing_locations(tree)
    namespace = {
        "__name__": "__main__", "__file__": str(path),
        "__nbterm_display__": capture, "display": capture,
    }
    exec(compile(tree, str(path), "exec"), namespace, namespace)


def main() -> int:
    if len(sys.argv) < 5 or sys.argv[1] not in {"--code", "--file"} or sys.argv[3] != "--artifacts":
        print("Usage: rich_runner.py (--code|--file) PATH --artifacts JSONL [args...]", file=sys.stderr)
        return 2
    mode, target, artifact_path = sys.argv[1], Path(sys.argv[2]).resolve(), Path(sys.argv[4]).resolve()
    handle, emit = _writer(artifact_path)
    capture, emit_png = _capture_factory(emit)

    try:
        import IPython.display as ipd  # type: ignore
        ipd.display = lambda *objects, **kwargs: [capture(obj, title=str(kwargs.get("display_id")) if kwargs.get("display_id") else None) for obj in objects]
    except Exception:
        pass

    try:
        import matplotlib
        matplotlib.use("Agg", force=True)
        import matplotlib.pyplot as plt  # type: ignore
        def show(*args: Any, **kwargs: Any) -> None:
            import io
            nums = list(plt.get_fignums())
            for index, num in enumerate(nums, 1):
                fig = plt.figure(num); buf = io.BytesIO()
                fig.savefig(buf, format="png", bbox_inches="tight")
                emit_png(buf.getvalue(), f"Plot {index}")
            if nums: plt.close("all")
        plt.show = show
    except Exception:
        pass

    try:
        if mode == "--code":
            _execute_code(target, capture)
        else:
            old_argv = sys.argv[:]
            sys.argv = [str(target), *sys.argv[5:]]
            try: runpy.run_path(str(target), run_name="__main__")
            finally: sys.argv = old_argv
        return 0
    except SystemExit as exc:
        return int(exc.code or 0)
    except BaseException:
        traceback.print_exc(); return 1
    finally:
        handle.close()


if __name__ == "__main__":
    raise SystemExit(main())
