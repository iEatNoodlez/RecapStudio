---
name: recap-studio-narration
description: Write a Recap Studio project's narration (the recap script) with Claude Code agents instead of the app's own API writer, with the app open. The app's code builds each chapter's writer request and hands it, through its MCP tools (recap-studio), to a fresh Claude Code agent that reads that chapter's pictures; the app's code saves the answer. Use this whenever the user wants a Recap Studio project narrated, scripted, re-narrated or rerun "with Claude Code", "with a fork", "with agents", "instead of the app" or to save API credit; when they want some chapters redone; when new chapters were added to a project that's already narrated; when a project is set to YouTube-safe (milder sex scenes, black boxes); or when they want the narration's wording changed across a project (how sex scenes are told, real words instead of safe words, names), even if they don't mention the bridge.
---

# Narrating a Recap Studio project with Claude Code agents

Recap Studio (`Recap Studio\App`, a FastAPI + pywebview app) turns comic chapters into narrated recap videos. Its
step 3, **Write script**, normally sends each chapter to Claude through the API. This skill does the same job
with Claude Code agents, and keeps everything else the app's own. It all runs inside the open app
(`recap/claude_code.py`), reached through its MCP server, `recap-studio` (`Recap Studio\.mcp.json`):
- `write_script` starts a job that runs the app's writer code (`pipeline._api_chapter` + `claude.narrate`) with a
  stand-in API client. Each request is written out as a folder (`prompt.md`, the system prompt as `system.md`,
  and the chapter's pieces on labelled picture sheets) under `<project>\claude_code\<run>`, and waits.
- An agent per chapter gets each request with `next_request` and answers with `submit_answer`. The answer is
  checked at once (every piece covered once, in order, plus a summary, black boxes, long silences, and for a
  YouTube-safe project wording that seems to go too far) and handed to
  the app's code, which saves scenes, narration, chapter summary and the narrator label, exactly as an API
  answer would be saved.
- The other tools: `list_projects`, `project_status`, `job_status`, `cancel_job`, `get_narration`,
  `set_narration`, `run_steps` (any app step), `detect_characters`, `open_app`.
- `scripts/check_narration.py` checks the result.

**The app can also do all of this by itself**: Settings → Script writer → *Claude Code* (or `write_script` with
`agents: "app"`) starts one `claude -p` agent per chapter with the brief built from this skill (the series' saved
brief in `series/`, else the template). Use that when the user just wants it done. This skill's way (`agents:
"session"`, your own agents) is for when you want to watch the reports, correct the agents and redo chapters.

Reading `recap/claude.py`, `recap/pipeline.py` (`_api_chapter`), `recap/script.py` (`system_prompt`) and
`recap/claude_code.py` is the way to learn more about the requests themselves.

## Before you start

1. **Find the project.** `list_projects`, then `project_status` (`open_app` if the app isn't open). Both give
   the project's folder (named after the project); its `project.json` can be read while the app runs.
2. **Check what the chapters have.** Text should be read (step 2, Read text: `project_status` counts it), so
   agents get OCR text along with the pictures; `run_steps` with `ocr` reads it. Characters should be named
   (Prompt & characters): the system prompt gives agents the character list, with aliases and descriptions.
   `detect_characters` scans for them and has the script writer suggest names, for the user to accept in the
   app. Magi (speaker guesses) is optional; agents with pictures do fine without it.
3. **The app stays open.** Everything goes through its own code and store, so the user can keep using it (one
   job per project at a time). Only if the `recap-studio` tools aren't there (the MCP server isn't connected:
   `/mcp`) fall back to `scripts/fork_bridge.py`, which needs the app **closed** (its store locks are per
   process) and the brief's file-bridge sections (`references/file_bridge_loop.md`).
4. **Back up `project.json`** to the scratchpad before writing anything.
5. Keep a progress file in the scratchpad (chapters done, running, next, notes to fix later). A long run
   outlives your context window, and the file is what lets you carry on after it's summarized.

## Choose how to run it

With the MCP tools the app chooses for you: `write_script` runs `workers` chapters at once (4 unless the user's
setting says otherwise) and goes in order whenever a chapter's previous chapter is in the run without a summary
yet (a first narration). It writes what has no narration yet, or with `redo` the chosen chapters again. What
follows explains the choices; the flags are fork_bridge.py's.

- **First narration of a series: four drafting, finished in order** (`--workers 4 --in-order`). Each
  chapter's request opens with the previous chapter's summary and last lines ("the story so far"), written by
  the agent before it, so chapters must be finished one after another. Most of an agent's time goes on reading
  10 to 15 picture sheets, which doesn't need the story so far. With `--in-order` the next three chapters'
  agents get their pictures at once as a `DRAFT` and draft from them; each gets `READY` with the story so far
  when the chapter before it is saved, checks its draft against it (the brief's *Continuity* paragraph lists
  what to check), and answers. A long chapter's second part still waits for its first.
  First used on an 81-chapter series (2026-10-29): 4 hours 8 minutes, about 3 minutes per chapter,
  no faster than an earlier one-at-a-time run. Each chapter still waits for the one before it, and an
  agent takes about 3 minutes from `READY` to its answer (checking the draft, rewriting, the wait helper's
  loop), so that step sets the pace. The check did its job: chapter 7's agent drafted a coerced scene as
  willing and rewrote it once the story so far came. It's at least as good as one at a time; it isn't faster
  yet. Spot-check the first chapters whose story so far mentions a drugging, blackmail or a camera.
- **A redo or rerun, when every chapter already has a summary: four at a time** (`--redo --workers 4`).
  Chapters start from the old summaries, which are good enough for orientation.
- Redoing a few chapters: list their numbers (`... <bridge dir> 2 5 17 --redo --workers 4`). A chapter whose
  name isn't "Chapter N" goes by its whole name in quotes.
- **New chapters added to a narrated project** (the user imported more, at the end or in between): `--new`
  picks the chapters that have pieces but no narration yet; `status.json` lists them under `names`, and any
  still without pieces under `not detected` (they need Detect panels and Read text first, and new faces need
  Characters). Each starts from the story so far the chapter before it left, so an inserted chapter fits in
  where it sits. Consecutive new chapters: `--workers 4 --in-order`; scattered ones can run four at a time.
  Reuse the series' saved brief (see *Write the brief*).

## Write the brief

A series narrated before has its filled-in brief in `series/<project id>-<name>.md`: copy that to the
scratchpad and check it against the template for rules added since (a newer template wins on general rules;
the series brief on names and story). After a first narration, save the filled-in brief there: the app's own
Claude Code writer uses it for that series too (with the template's *How you get your chapter*, *Don't* and
*Report*), so keep it free of run-specific notes.

Otherwise copy `references/brief_template.md` to the scratchpad and fill it in. It already carries the user's
settings:
- **Density**, from a project the user liked: about 5 pieces per scene, 20 to 35 words per line (never over
  45), 800 to 1,200 words per chapter.
- **No skipped beats**: wordless pieces that carry the story (someone shaking afterwards, tears, a stunned
  stare) get narration. Empty lines only for title and end cards, credits and blank pieces.
- **Lines as long as their pictures**: the scrolling video has a top speed while a line is read, so one short
  line over a tall scene (8 or more pieces of a sex scene, typically) leaves 10 to 17 seconds of silence. The
  bridge times every answer as the video would (`scripts/scroll_time.py`). If a line would leave more than
  6 seconds with no voice, it sends one `FIX` asking for the pieces to be split into more scenes.
- **Sex scenes and wording**, below.

Fill in the series' **names and translation traps** (other spellings, a name the translation changed
partway, disguises and fake identities), a line of premise and the cast's ages. Agents are good with
pictures but will follow the OCR's spelling of a name unless told otherwise.

### Sex scenes and wording

These webtoons are adult. What the user asked for, and where the line stays:
- **Willing scenes: detailed and sensual**, in plain words (what they do, positions, reactions, climax,
  aftermath), several lines. Not porn: no close-up anatomy of genitals, no crude slang, no gross-out detail.
- **Real words, not safe words**: sex, naked, breasts, came, condom, and real swearing in retold dialogue.
  Coy stand-ins ("their bodies moved together", "chest") were what the user didn't want.
- **Pressured scenes stay non-graphic, whatever else is asked**: blackmail, someone saying no or pleading to
  stop (including "no aloud, yes in her thoughts"), and anyone drunk, groggy, half-asleep or thinking it's a
  dream. Name it once, then thoughts, words, feelings and aftermath; no description of the acts. Judge each
  stretch by what it shows at that moment; agreed role-play counts as willing.
- **Anyone shown or described as under eighteen in a sexual scene: leave it empty** and report it.

Agents drift on this in predictable ways, so plan to correct them early (see *While it runs*):
- told to describe "more", some overshoot into explicit, line-per-motion porn; a concrete example line at
  the intended level (the brief has one) helps more than adjectives;
- they misjudge consent both ways: calling a blackmailed or drunk scene willing (then describing it), or
  calling a whole encounter pressured because it began with blackmail, when the stretch itself is eager.
  The commonest slip: she starts something (flirts, places his hand), he goes much further while she freezes
  or asks what he's doing, and the agent narrates it all as willing because she started it;
- they leave aftermath and reaction panels blank when told "no physical detail".

**YouTube-safe projects** (Panel settings → YouTube; `project_status` says `"youtube_safe": true`)
go on YouTube as age-restricted videos. The user asked for "mild, but not too mild: right on the edge of
YouTube's policies". Replace the brief's sex-scene section with `references/youtube_wording.md`: willing sex
said plainly (kissing, undressing, naked, had sex) but with no named acts, body parts, orgasms or fluids,
in a line or two; pressured sex in one plain, non-sexual sentence, then the aftermath. `system.md` carries
the app's own version (`script.YOUTUBE`). Each request then also asks for `"cover"`: black boxes over
exposed nipples and genitals, as fractions of a piece, which the bridge checks and the app saves as the
script writer's boxes. The app's box finder add-on and the user's own boxes add to them (`recap/cover.py`).
Switching a narrated project to YouTube-safe doesn't change its script: redo the chapters (`redo`) for
the milder wording and the writer's boxes.
`submit_answer` also runs the app's word check on a YouTube-safe answer (`recap/wording.py`) and sends one
`FIX` listing lines that seem to go too far: named acts, body parts and orgasms, and in a forced scene
undressing told in more than one line, his excitement in more than two, or her body reacting. A chapter whose
agent keeps them is flagged in the app (**Check wording** in the chapter list, `"check wording"` in
`project_status`) for the user to read, edit, rewrite or keep. It's a word search, so read what it lists.
Voice pauses before voicing flagged lines (`job_status`: "Paused before Voice: …") and carries on once
they're edited or kept; tell the user rather than resuming it yourself.

## Run it

With the app open (the MCP tools):
1. `write_script` with the project, the chapters (or none for all), `redo` when rewriting, and
   `agents: "session"`. It answers with the chapters it will write.
2. Start one fresh agent per chapter, as in step 2 below; its brief's *How you get your chapter* tells it to use
   `next_request` and `submit_answer`. Keep `workers` agents going (step 3 below); `job_status` shows each
   chapter's state.

With the app closed (fork_bridge.py, only when the MCP tools aren't available):
1. Start the bridge in the background with the app's Python:
   ```
   "<Recap Studio>\App\.venv\Scripts\python.exe" <skill>\scripts\fork_bridge.py <project id> <bridge dir> [chapters] [--new] [--redo] --workers N --label "Claude Code (<model>)"
   ```
   Use a new bridge dir per run. Before rerunning chapters in a bridge dir used before, delete their
   `done_Chapter_N` files (and `FINISHED`): an agent started before its chapter's thread would see the old
   `done_` file and stop at once.
2. Start **one fresh agent per chapter**: Agent tool, `subagent_type: general-purpose`, `model: opus`,
   `run_in_background: true`. Never one long-lived agent or a fork for many chapters: each request carries
   up to 15 picture sheets, and once an agent's context passes the per-request image limit, new sheets come
   back as "[media removed]" and it carries on writing from the OCR text alone, without saying so. This
   happened on a whole project once and it had to be redone. Prompt:
   ```
   Your chapter: "Chapter N".

   Read your brief first and follow it exactly: <path to the filled-in brief>

   It tells you how to wait for your chapter's requests from the bridge, read the prompt and every picture
   sheet, and write answer.json. The only files you write are answer.json files. Pay close attention to its
   "Sex scenes and wording" section. End with the short report the brief describes.
   ```
   For a chapter being redone, add a sentence saying what the last attempt got wrong.
3. Keep the pipeline full. One at a time: start chapters 1 and 2 together, then start chapter N+2 when
   chapter N's agent reports; the waiting agent loops on `WAITING` until its request appears. Four at a time,
   with or without `--in-order`: start four, then one more each time one reports. With `--in-order` the bridge
   makes a chapter's request only when a thread is free, so a fifth agent started early just waits.
   With `--in-order`, keep the brief's *Continuity* paragraph on drafting; without it, delete that paragraph.

## While it runs

- **Read every report.** Agents note speaker corrections, places where the story so far was wrong, and how
  they judged each sex scene. Note anything to fix later in the progress file.
- **Spot-check the first chapters with sexual content** with `get_narration` (or from `project.json`:
  `scenes.groups(p["panels"])`, the first piece of each group holds the scene's narration). If the level is
  off, change the brief and tell the agents still running (`SendMessage` to their agent id): a change only
  reaches answers not yet written, so list the chapters already written for a redo.
- An answer is taken the moment it passes the check (with fork_bridge, the moment `answer.json` is written); an
  agent's later changes are ignored. Agents sometimes save
  crops or drafts in the scratchpad despite the brief: harmless, delete them at the end (check first that
  none touches the project).

## Afterwards

1. When the job is done (`job_status`; with fork_bridge, `FINISHED`), redo the chapters that missed the brief
   (`write_script` with those chapters and `redo`; with fork_bridge, the same bridge, their numbers, `--redo`).
2. Run `scripts/check_narration.py <project id> --names "<regex of names that shouldn't appear>"`. It checks
   density per chapter, lines over 45 words, quotation marks, digits, crude words, talk of panels, blank
   scenes with story text, and long silences, ending with a "to redo" list of chapters. Redo those chapters
   too. For a YouTube-safe project it also runs the app's wording check (the one `submit_answer` runs) on
   every chapter, leaving out lines the user chose to keep; read what it lists, since it's a word search.
   The bridge's `FIX` round has long silences split; `silences.txt` in the bridge dir lists what an agent
   left silent after its `FIX`.
3. Fix single lines with `set_narration` (the scene's first piece, from `get_narration`): it clears the line's
   voice so Voice reads it again. With the app closed, a small script does it instead: `store.update(pid, fn)`
   with exact substring replacements, failing loudly if a substring isn't found once, clearing the changed
   lines' `audio` and `duration`. Chapter summaries can carry an agent's guesses; fix those too.
4. Tell the user: chapters and words, medians against the density targets, what the checks found, and what was
   redone or trimmed and why. The next app steps are Voice and Render video (`run_steps` with `audio`, `video`).

## Reference

- `references/brief_template.md`: the per-chapter brief, with the user's settings.
- `references/youtube_wording.md`: the sex-scene section and black boxes for a YouTube-safe project.
- `series/`: the filled-in briefs of series narrated before, by project id (`<project id>-<name>.md`); the
  app's own Claude Code writer uses a project's brief when it has one.

- The app's MCP server: `App\recap\mcp.py` (the tools), `mcp_stdio.py` (what `.mcp.json` starts), and
  `claude_code.py` (the bridge inside the app, and the agents the app starts itself).
- `references/file_bridge_loop.md`: the brief's sections for fork_bridge.py, when the app can't be open.
- `scripts/fork_bridge.py`: the bridge for the app closed (`--help`). Finds the app in `App` next to
  `.claude` (in `Recap Studio`); set `RECAP_APP` for another location.
- `scripts/bridge_next.py`: fork_bridge's wait helper for agents (`NEXT` / `DRAFT` / `READY` / `FIX` /
  `WAITING` / `CHAPTER DONE` / ...).
- `scripts/check_narration.py`: checks after a run.
- `scripts/scroll_time.py`: times a chapter's scenes against their lines as the app's scrolling video does
  (`video.StripSegment`); it's the app's own `recap/scroll_time.py`, which the in-app bridge uses too. With real voice lengths it matched the rendered subtitles of two projects exactly.

