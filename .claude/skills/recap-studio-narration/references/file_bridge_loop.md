# Handing chapters over with fork_bridge.py (the app closed)

<!-- For a run with scripts/fork_bridge.py instead of the app's MCP tools (when the app can't be open). In
     the brief, replace the "How you get your chapter" section with the two sections below and fill in the
     paths; change "The app measures this" to "The bridge measures this"; in the intro, the bridge hands
     the chapter over and the agent answers each request with a file; under Don't, the only files it
     writes are answer.json files. Delete this comment. -->

## Paths
- Python: `<APP>/.venv/Scripts/python.exe`
- Wait helper: `<SKILL>/scripts/bridge_next.py`
- Bridge dir: `<BRIDGE_DIR>`

## Loop
1. Run `<python> <wait helper> <bridge dir> 540 "<your chapter name>"` in the foreground (Bash, timeout
   600000). It prints one of these:
   - `NEXT <folder>`: a request to answer (step 2).
   - `DRAFT <folder>`: your request is here, but the chapter before yours is still being written, so it has no
     story so far yet. Do step 2 up to the answer: read everything and draft your scenes, but don't write
     answer.json. Then run the helper again.
   - `READY <folder>` and the story so far: the request you drafted can be answered now. Check your draft
     against the story so far (see *Continuity*), then write answer.json (the end of step 2).
   - `FIX <folder>` and a problem: your answer was rejected. Correct it and write answer.json again.
   - `WAITING`: nothing yet (the chapter before yours may still be being written); run it again.
   - `CHAPTER DONE`: your chapter is saved. Give your report and stop.
   - `CHAPTER FAILED`, `ERROR` or `STOPPED`: stop and report what it printed.
2. Answer a request:
   - Read `<folder>/prompt.md` and the system prompt it names (`<bridge dir>/system.md`). The system prompt
     holds the persona, the rules, the style and the character list. Follow them exactly.
   - Read EVERY `sheet_XX.jpg` in the folder. Read several per message, in parallel. Each piece is labelled
     P<number> in red, and a tall piece continues in the next column.
   - **If any sheet doesn't show as a picture** (for example "[media removed]", or an error), don't write from
     the text alone. Stop and report `PICTURES MISSING` with the sheet names.
   - Work out each scene from the pictures together with the OCR text (and any speaker guesses) in prompt.md:
     who is speaking, captions (narrating voice), thoughts as opposed to speech, messages on phone screens, and
     flashbacks or imagined scenes as opposed to real events.
   - Write `<folder>/answer.json` with the Write tool:
     `{"scenes": [{"first_piece": 1, "last_piece": 4, "narration": "..."}, ...], "summary": "..."}`
     (and `"cover"` when the prompt asks for black boxes: see *Black boxes*).
     The scenes must cover pieces 1..N in order, each piece exactly once. answer.json is saved the moment you
     write it, so write it once, finished. Later edits to it are ignored.
3. Go back to step 1. A long chapter comes in two parts; the second request appears a few seconds after the
   first answer is saved.

