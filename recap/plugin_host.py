"""Runs one source plugin (sources.py) in a process of its own, so a plugin that hangs, crashes or prints can't take
the app with it. Started with the app's own Python in isolated mode (-I: nothing from the current folder, the user's
site-packages or PYTHONPATH), so a plugin has the standard library, the app's packages (requests) and the plugins
folder (for helpers shared by several plugins).

One JSON message a line. In: {"call": name, "args": [...]}. Out: {"ok": true, "result": ...} or
{"ok": false, "error": "...", "trace": "..."}. What the plugin prints goes to stderr (the plugin's log), never into
the answers.

    python -I -X utf8 plugin_host.py <plugin file>
"""
import importlib.util
import json
import sys
import traceback
from pathlib import Path

CALLS = {"search", "browse", "series", "pages"}


def main():
    path = Path(sys.argv[1])
    answers = sys.stdout
    sys.stdout = sys.stderr
    sys.path.insert(0, str(path.parent))
    module, failed = None, None
    try:
        spec = importlib.util.spec_from_file_location(f"recap_plugin_{path.stem}", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    except BaseException as e:  # reported with every call, so the app can show it
        failed = (f"The plugin didn't load: {type(e).__name__}: {e}", traceback.format_exc()[-4000:])
    for line in sys.stdin:
        try:
            msg = json.loads(line)
            if failed:
                answers.write(json.dumps({"ok": False, "error": failed[0], "trace": failed[1]}) + "\n")
                answers.flush()
                continue
            name = msg["call"]
            fn = getattr(module, name, None) if name in CALLS else None
            if not callable(fn):
                raise AttributeError(f"The plugin has no {name}()")
            text = json.dumps({"ok": True, "result": fn(*msg.get("args", []))}, ensure_ascii=False, default=str)
        except BaseException as e:
            text = json.dumps({"ok": False, "error": f"{type(e).__name__}: {e}", "trace": traceback.format_exc()[-4000:]},
                              ensure_ascii=False)
        answers.write(text + "\n")
        answers.flush()


if __name__ == "__main__":
    main()
