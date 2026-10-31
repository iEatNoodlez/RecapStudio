"""Wait for fork_bridge.py's next request, or its end, and say where it is.
Usage: python bridge_next.py <bridge dir> [max seconds, default 540] [chapter name, e.g. "Chapter 5"]
Prints NEXT <folder> (a request to answer), FIX <folder> + the problem (an answer to correct),
DRAFT <folder> (--in-order: a request to read and draft, not answer yet), READY <folder> + the story so far
(the drafted request can be answered now), CHAPTER DONE / CHAPTER FAILED + details (with a chapter name),
FINISHED / STOPPED / ERROR + details, or WAITING when nothing came within the time (run it again)."""
import re
import sys
import time
from pathlib import Path

root = Path(sys.argv[1])
end = time.time() + (float(sys.argv[2]) if len(sys.argv) > 2 else 540)
chapter = sys.argv[3] if len(sys.argv) > 3 else None
tag = re.sub(r"[^A-Za-z0-9.-]+", "_", chapter) if chapter else None
while time.time() < end:
    for name in ("ERROR", "STOPPED"):
        if (root / name).exists():
            print(name)
            print((root / name).read_text(encoding="utf-8"))
            sys.exit(0)
    for d in sorted(root.glob("req_*")):
        if not d.is_dir() or d.name.endswith("_stale") or (d / "done").exists() or not (d / "prompt.md").exists():
            continue
        if chapter and (not (d / "chapter.txt").exists() or (d / "chapter.txt").read_text(encoding="utf-8") != chapter):
            continue
        if (d / "answer.json").exists():
            continue  # being checked by the bridge
        if (d / "error.txt").exists():
            print("FIX", d)
            print((d / "error.txt").read_text(encoding="utf-8"))
        elif (d / "draft").exists():
            if (d / "drafted").exists():
                continue  # already handed out as a draft: wait for the story so far
            (d / "drafted").write_text(time.strftime("%Y-%m-%d %H:%M:%S"), encoding="utf-8")
            print("DRAFT", d)
            print("The chapter before yours is still being written, so this request has no story so far yet. Read "
                  "prompt.md and every picture sheet now and draft your scenes, but don't write answer.json. Then run "
                  "the wait helper again: it prints READY with the story so far.")
        elif (d / "drafted").exists():
            print("READY", d)
            print("The story so far is here (below, and at the top of prompt.md). Check your draft against it as the "
                  "brief says, then write answer.json.")
            if (d / "answer.early.json").exists():
                print("An answer.json written before this was set aside as answer.early.json; it wasn't taken.")
            print()
            print((d / "story_so_far.md").read_text(encoding="utf-8"))
        else:
            print("NEXT", d)
        sys.exit(0)
    if tag:
        if (root / f"failed_{tag}").exists():
            print("CHAPTER FAILED")
            print((root / f"failed_{tag}").read_text(encoding="utf-8"))
            sys.exit(0)
        if (root / f"done_{tag}").exists():
            print("CHAPTER DONE")
            sys.exit(0)
    elif (root / "FINISHED").exists():
        print("FINISHED")
        print((root / "FINISHED").read_text(encoding="utf-8"))
        sys.exit(0)
    time.sleep(1)
print("WAITING (nothing new yet; run again)")
