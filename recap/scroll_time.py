"""How long a scrolling chapter's video takes to pass each scene, against how long its line is read, timed the
way the app's video does it (video.StripSegment). The scroll has a top speed while a line is read, so a short
line over a tall scene leaves the rest of it scrolling by with no voice. A line not voiced yet is timed from its
words. The Claude Code writer (claude_code.py) asks an agent to fix an answer with long silences; the narration
skill's fork_bridge.py and check_narration.py use this too."""
from . import pipeline, video

WPS = 3.15   # the voice's words per second at speed 1 (measured on two test series)
LIMIT = 6.0  # seconds with no voice between two lines that count as a long silence


def silences(p: dict, chapter_id: str, scenes: list, limit: float = LIMIT) -> list[dict]:
    """Stretches of more than limit seconds with no voice between two lines of a chapter.
    scenes: [(piece ids, narration, voice seconds or None)] in order; pieces in none of them are left out.
    Returns [{"line": index of the scene whose line comes before, "quiet": indexes of the empty scenes after it,
    "seconds": time with no voice, "scroll": seconds that scene takes to pass, "read": seconds its line takes}];
    [] when the chapter doesn't scroll."""
    s = p["settings"]
    pieces = [x for x in p["panels"] if x["chapter"] == chapter_id]
    pages = [x for x in p["pages"] if x["chapter"] == chapter_id]
    if not pages or not pipeline._scrolls(s, pages) or not all("y0" in x for x in pieces):
        return []
    W, H = video.frame_size(s)
    sw = W if H > W else int(W * 0.42) // 2 * 2  # strip width on screen (as StripSegment)
    base = pages[0]["w"]  # piece rows are in the strip with every page scaled to the first page's width
    rows_1080 = sw / base / (min(W, H) / 1080)  # screen pixels at 1080p per strip row: the speeds' unit
    at = {x["id"]: i for i, x in enumerate(pieces)}
    # scene edges split the gutter between pieces, as StripSegment splits it between scenes
    edges = ([0.0] + [(a["y1"] + b["y0"]) / 2 for a, b in zip(pieces, pieces[1:])]
             + [sum(x["h"] * base / x["w"] for x in pages)])
    S = video.StripSegment
    pad, wps = float(s["pad_seconds"]), WPS * float(s.get("speed") or 1.0)
    t, out, last = S.START_HOLD, [], None  # last: (scene, when its voice ends, scroll, read)
    quiet = []
    for k, (ids, text, voice) in enumerate(scenes):
        idx = sorted(at[i] for i in ids)
        rows = (edges[idx[-1] + 1] - edges[idx[0]]) * rows_1080
        words = len((text or "").split())
        if words:
            read = voice or words / wps
            d = max(S.LEAD + read + pad, rows / S.MAX_SPEED)
            if last and t + S.LEAD - last[1] > limit:
                out.append({"line": last[0], "quiet": quiet, "seconds": t + S.LEAD - last[1],
                            "scroll": last[2], "read": last[3]})
            last, quiet = (k, t + S.LEAD + read, d, read), []
        else:
            d = max(0.5, rows / S.QUIET_SPEED)
            quiet = quiet + [k]
        t += d
    return out  # (the stretches before a chapter's first line and after its last are its start and end)
