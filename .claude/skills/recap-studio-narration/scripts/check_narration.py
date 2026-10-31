"""Check a project's narration after a run (read only): density per chapter, and the wording rules.
Usage: python check_narration.py <project id> [--names REGEX] [--chapters] [--youtube]
  --names     a regex of names that shouldn't appear (a translation's other names for listed characters)
  --chapters  also print one line per chapter (scenes, narrated, words, narrator label)
  --youtube   also run the app's YouTube-safe wording check (recap/wording.py, as the Claude Code writer does):
              named acts, body parts and orgasms, and a forced scene told step by step (undressing, his
              excitement, her body reacting). On by itself for a project set to YouTube-safe.
Prints medians and ranges, lines over 45 words, and lines that break a rule: quotation marks, digits, crude
words for genitals, talk of panels/pages/speech bubbles, and empty scenes whose pieces have text that isn't
a title or "to be continued" card (a story beat left blank), and long silences in a scrolling video: a line
much shorter than its scene's pictures (scroll_time.py; lines not voiced yet are timed from their words)."""
import argparse
import os
import re
import statistics as st
import sys
from pathlib import Path

APP = Path(os.environ.get("RECAP_APP") or Path(__file__).resolve().parents[4] / "App")
sys.path.insert(0, str(APP))
from recap import pipeline, scenes, store, wording  # noqa: E402
import scroll_time  # noqa: E402  (next to this script)

ap = argparse.ArgumentParser()
ap.add_argument("pid")
ap.add_argument("--names")
ap.add_argument("--chapters", action="store_true")
ap.add_argument("--youtube", action="store_true")
args = ap.parse_args()

p = store.load(args.pid)
names = {c["id"]: c["name"] for c in p["chapters"]}
by_chapter = {}
for g in scenes.groups(p["panels"]):
    by_chapter.setdefault(g[0]["chapter"], []).append(g)

rows, lines, blanks = [], [], []
# title, end and credit cards: the series' name, "to be continued" (OCR often garbles it), the end, credits
# (text/art/coloring/adaptation), the scan group's notices
CARD = re.compile(r"(?i)" + re.escape(p["name"]) + r"|contin|the end|\bend\b|illustrator|adaptation|original|"
                  r"season|review|\*{3,}|text\s*/|art\s*/|coloring|scans|read early|patreon|discord|^\W*$")
REAL_WORD = re.compile(r"[A-Za-z]{3,}")  # OCR-garbled cards ("C ON ONTINUE ED") have few real words
for c in p["chapters"]:
    gs = by_chapter.get(c["id"], [])
    if not gs:
        continue
    texts = [g[0].get("narration") or "" for g in gs]
    words = sum(len(t.split()) for t in texts)
    rows.append((c["name"], len(gs), sum(1 for t in texts if t.strip()), words, c.get("narrator") or ""))
    lines += [(c["name"], t) for t in texts if t.strip()]
    for g, t in zip(gs, texts):
        said = " / ".join((x.get("text") or "").replace("\n", " ") for x in g).strip(" /")
        if not t.strip() and said and not CARD.search(said) and len(REAL_WORD.findall(said)) >= 3:
            blanks.append((c["name"], g[0]["id"], said[:100]))

if not rows:
    sys.exit("No scenes yet: run Detect panels (and Read text) first.")
if args.chapters:
    for r in rows:
        print(f"{r[0]}: {r[1]} scenes, {r[2]} narrated, {r[3]} words  [{r[4]}]")
print(f"{len(rows)} chapters, {sum(r[1] for r in rows)} scenes, {sum(r[2] for r in rows)} narrated, "
      f"{sum(r[3] for r in rows)} words")
print(f"scenes per chapter: median {st.median(r[1] for r in rows)} ({min(r[1] for r in rows)}-{max(r[1] for r in rows)})")
print(f"words per chapter: median {st.median(r[3] for r in rows)} ({min(r[3] for r in rows)}-{max(r[3] for r in rows)})")
print(f"words per line: median {st.median(len(t.split()) for _, t in lines)}, "
      f"over 45: {sum(1 for _, t in lines if len(t.split()) > 45)}")
print("labels:", sorted({r[4] for r in rows}))

checks = {
    "quotation marks": r"[\"“”]",
    "digits": r"\d",
    "crude words for genitals": r"(?i)\b(pussy|cock|dick|cunt|cum|clit)\b",
    "panels, pages or bubbles": r"(?i)\b(panel|speech bubble|sound effect)s?\b",
}
if args.names:
    checks["other names"] = args.names
for label, pattern in checks.items():
    hits = [(ch, t) for ch, t in lines if re.search(pattern, t)]
    print(f"{label}: {len(hits)}")
    for ch, t in hits[:8]:
        print(f"   {ch}: {t[:150]}")
if args.youtube or p["settings"].get("youtube_safe"):  # a word search: read the lines, some may be fine
    found = []
    for c in p["chapters"]:
        texts = [g[0].get("narration") or "" for g in by_chapter.get(c["id"], [])]
        kept = set(c.get("wording_kept") or [])  # kept as they are in the app (Check wording)
        found += [(c["name"], f) for f in wording.find(texts) if f["line"] not in kept]
    print(f"YouTube-safe wording check: {len(found)} lines")
    for ch, f in found[:30]:
        print(f"   {ch} [{f['why']}: {f['word']}]: {f['line'][:150]}")
    if found:
        print("   to redo:", " ".join(sorted({ch.removeprefix("Chapter ") for ch, _ in found},
                                             key=lambda n: (len(n), n))))
print(f"blank scenes with story text: {len(blanks)}")
for ch, pid, said in blanks[:20]:
    print(f"   {ch} ({pid}): {said}")

silent = []  # (chapter, seconds with no voice, the scene whose line comes before)
for c in p["chapters"]:
    runs = [r for r in pipeline._strip_runs([x for x in p["panels"] if x["chapter"] == c["id"]])
            if r[0].get("enabled", True)]  # the scenes the video scrolls past, as video_items makes them
    found = scroll_time.silences(p, c["id"], [([x["id"] for x in r], r[0].get("narration", ""),
                                               r[0].get("duration") or None) for r in runs])
    silent += [(c["name"], f["seconds"], runs[f["line"]][0]) for f in found]
print(f"silences over {scroll_time.LIMIT:.0f} s inside chapters: {len(silent)}, "
      f"over 10 s: {sum(1 for _, s, _ in silent if s > 10)}")
for ch, secs, head in sorted(silent, key=lambda x: -x[1])[:20]:
    print(f"   {ch} ({head['id']}): {secs:.0f} s after: {(head.get('narration') or '')[:110]}")
if silent:
    print("   to redo:", " ".join(sorted({ch.removeprefix("Chapter ") for ch, _, _ in silent},
                                         key=lambda n: (len(n), n))))
