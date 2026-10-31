"""The app's silence timer (recap/scroll_time.py), for fork_bridge.py and check_narration.py: how long a scrolling
chapter's video takes to pass each scene against how long its line is read. The script that imports this has
already put the app folder on sys.path."""
from recap.scroll_time import LIMIT, WPS, silences  # noqa: F401
