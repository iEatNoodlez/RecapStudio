# Using Recap Studio

## Projects and the main button

A project opens on an overview: four tiles saying how far the chapters, script, voice and video are, and one main
button that does whatever comes next (**Make the recap**, **Continue the recap**, or **Make the video again**), with
the time it takes beside it. Below are tabs: **Chapters**, **Script** (the scenes and their narration),
**Characters** and **Video** (the player, its files, the project's video settings and YouTube-safe). Showing the
project's folder, saving the project as a file, removing all chapters and deleting the project are in the **⋯** menu.

**New project**: type a name at the top left and press **Create**. Four pages of questions follow, starting from your
last project's answers: what kind of comic it is (webtoon, manga, a left-to-right comic, or not sure); its
characters (whether Make the recap finds them, in the first 5 chapters or all, and whether you check the suggested
names or trust them: see [Characters](characters.md)); how its video looks (landscape or portrait, automatic, pan and zoom
or stills, resolution and frame rate, with how long an hour of it takes to make on this computer); and YouTube
(whether a title and thumbnail are suggested after the script, and whether it's YouTube-safe). The video settings
are the project's own from then on: its **Video** tab changes them, and its **Project settings** the characters. (Until 0.1.1 they were app-wide; each project made before keeps the
choices it was made with.) The encoder is the one app-wide video setting (Settings → Video).

**Advanced mode** (the switch at the bottom of the sidebar, remembered) shows everything else: the step buttons,
ticking chapters to work on just those, each chapter's counts, reordering and your own voice-over, Export for
editors, the **Prompt** and **Project settings** tabs, the text read from each scene, split and join, re-running
selected scenes, and the rest of Settings (performance, text reading, the add-ons, Magi, the finer video options).
In simple mode, long help text shows two lines: hover it for the rest, or click it.

1. Create a project (the New project questions above) and click **Add chapters** or **Import folder…**. Pick the series folder; every subfolder (at any depth) that contains images becomes a chapter, and so does every `.cbz`/`.zip` file (and, with the EPUB comics plugin, every `.epub`, split into a volume's chapters). Files that can't be read are listed with why (an EPUB with DRM, say). Review the list, untick anything you don't want, and import.
   - Chapters and pages are sorted naturally (`Chapter 2` before `Chapter 10`, `144.5` after `144`).
   - A chapter is named after its folder. Generic image folders like `pages` or `images` are skipped (`Chapter 3/pages` is named "Chapter 3"), and parent folders are added only when two chapters would otherwise share a name (`Season 1 / Chapter 1`).
   - Images in folders are read where they are, not copied, so keep the source folder in place (re-import if you move it). Pages inside `.cbz`/`.zip` files are extracted into the project.
   - Chapters already in the project are flagged and left unticked, so you can re-import a folder after new chapters come out.
   - **Put them** says where new chapters go: in order by chapter number (the default: each goes after the last chapter whose number isn't above its own, so a late `Chapter 45.5` lands after `Chapter 45`; a name without a number goes at the end), at the start, at the end, or after a chapter you pick. Files dropped onto the window go by number too.
   - You can import while a job runs (a render, say), in this project or another: the job carries on with the chapters it started with.
   - A chapter with the same pages as another (one put in the folder twice, as "34-5" and "34.5") is pointed out once it's imported ("Imported twice?"), so you can delete one before it's narrated twice: pages of the same sizes in the same order, with the first, middle and last nearly the same.
   - You can also drop images or `.cbz`/`.zip` files onto the window; each archive becomes a chapter, loose images become one chapter.
2. Click **Make the recap** (later **Continue the recap**): it runs every step on every chapter. In advanced mode, tick the chapters you want to work on (none ticked = all), or run the steps one by one. Rename or remove chapters in the Chapters tab (reorder them in advanced mode).
   - The main button picks up where you left off: it only detects panels in chapters that have none, reads text and writes narration only for panels that don't have them yet, and voices only lines without audio, then renders. It never redoes or overwrites finished work, including narration you edited by hand.
   - A single step that's already partly done asks whether to **Continue** (only what's left) or **Redo all**; one that's fully done asks before redoing.
3. Edit any narration line, untick panels you don't want, or select panels and **Re-run script + voice for selected**. Changing a line clears its audio; run **Voice** again to regenerate it.
4. Run **Render video**. It renders the ticked chapters, in order, into the project's `output` folder: `recap.mp4` for the whole project, or `recap - Chapter 1 to Chapter 10.mp4` for a range, each with matching `.srt` subtitles, `.wav` narration and a YouTube chapter list (see [Video](video.md)).

## Scenes

Panel detection cuts webtoon strips finely, at every gap between panels, including gaps crossed by a speech bubble, so it doesn't miss panel boundaries. The pieces of each detected panel start out joined into one **scene**: one narration line, shown in the video as the pieces stacked back together. In the scene list, **✂ split here** makes a piece start its own scene, and **⤒ Join with scene above** merges two scenes (their narration is combined). Claude regroups the pieces itself when it writes a chapter.

## Settings

- **⚙ Settings** (bottom of the sidebar) holds the app-wide choices shared by every project: the script writer (a local model, Claude Code, Claude, OpenRouter, Google Gemini, DeepSeek, Groq or Mistral, with models, API keys and the Ollama connection), the voice, the video encoder and the thumbnail redraw. **Run setup again** at its top reopens the first-run setup. They're stored in `data\settings.json`. The line under the step buttons shows what's in use.
- **Project settings** (a tab of each project) holds what differs per series: layout, reading direction, the language of its text, how characters and the scan group's pages are found, and each plugin's choice for the project (smallest panel size in advanced mode). The **Prompt** (advanced mode) and **Characters** tabs are per project too, and so is YouTube-safe (the **YouTube** tab).

## Pause and resume

**Pause** (next to Cancel while a job runs) holds the job after the item it's working on: the current panel or line, or for an online writer or a video render the current chapter, which finishes first. **Resume** carries on from exactly that point, so a redo of every chapter doesn't start over. Cancel works while paused.

When writing with Claude or another online service, the job pauses by itself instead of failing, keeping its place:

- **Out of credit**: add credit (for Claude at console.anthropic.com, Settings → Billing; the message names the place for the others), then press Resume. The chapter that hit the limit is sent again; nothing is skipped or handed to the local model.
- **API key rejected or missing**, or no model chosen: fix it in Settings → Script writer (the settings stay open to edit while paused), then Resume.
- **No allowance left on the key's plan** for the model (for example a free key and a bigger model), or **a model the key can't use**: it pauses straight away (waiting wouldn't help), saying so. Choose another model, or check your plan or billing, then Resume. A rate limit that says when to retry is waited out instead.
- **Service busy or unreachable** (rate limit, overloaded, network, or the connection dropping partway through a reply): it waits and tries again on its own, after 30 seconds, 2 minutes and 5 minutes, with a countdown. If it still fails, it pauses so you can Resume later.

A paused job lives in the running app: closing the app ends it. Finished chapters are kept (hover a chapter's stats in the list to see which model wrote it), so tick the rest and run again.

## Time estimates

Next to the main button, a line says how long what's left takes; in advanced mode another breaks it down for the ticked chapters, step by step; hover a step button for its own estimate (what's left, and to redo everything), and the Continue / Redo choice shows both. While a job runs, its progress line says about how long is left in the current step (time spent paused doesn't count).

Each estimate is the work a step would do (pages, panels, chapters or scenes, narration lines, video frames) times how fast that work goes. The speeds start from measurements on an RTX 5060 Ti with 12 CPU threads and are replaced by your computer's own as steps finish (blended with what was known, and saved in `data\speeds.json`), per device, script writer and model, and video size, style and encoder. The video's length is worked out from the narration (its audio, or before there's a voice, its length at the voice's pace: about 17.5 characters a second). When the main button will write the script first, chapters without narration are counted as getting a typical amount (about one line per three panels).

## Saving a project as a file

**Save as file…** (at the top of a project) writes the whole project into one `.recap` file in its output folder, to keep as a backup or move to another computer. It holds the pages (pages imported from a folder are copied in, so the file doesn't need that folder), panels, script, voice lines, character pictures, the character scan, your recordings and the scenes' subjects. The rendered video, its pieces, the videos for editors and the pictures without text are left out, since the app makes them again. Pictures and sound go in as they are, so the file is about the size of those folders (the 3-chapter test series: 90 MB).

**Open project file…** (in the sidebar) takes the file's path and opens it as a new project (named "… (2)" next to one of the same name), with everything in its own folder. Files that aren't project files, or that hold anything outside the project's folders, are refused, and a file from a newer version of the app asks for an update.

## Cutting webtoons around speech bubbles

A webtoon strip is cut at its gutters and across the white space inside panels, never through a line of text (see [Limits](#limits)). That still let a cut pass between a bubble's or caption box's outline and its text. The top of the box then ends one scene, and the rest of it, open at the top, starts the next. On 11 test chapters (10 English webtoons, 1 Korean), 467 of 1,885 cuts did that: a quarter.

**Settings → Cutting webtoons → Download the bubble finder** (11.1 MB) fixes that; first-run setup offers it too. It's comic-text-and-bubble-detector by ogkalu, an open model (Apache 2.0) trained on about 11,000 manga, webtoon, manhua and western comic pictures. It finds speech bubbles, the text in them and the text outside them (captions, signs, sound effects). The app uses its small 8-bit version, downloaded into `data\addons\bubbles` (pinned and checksum-checked), not part of the app. With it, **Detect panels** keeps every cut clear of what it finds:

- On the same chapters, 7 cuts went through a bubble's box and none through the text it found. 4 of them run along a row of one colour (the empty edge of a borderless or doubtful bubble, where nothing shows). 3 cut a bubble below its text, in a stretch over 3.3 widths of strip tall with nowhere else to cut: the app cuts through a bubble there rather than make a scene taller than it did before.
- Scenes are fewer and a little bigger (1,896 → 1,443), since the white space beside a bubble isn't cut any more. Where a bubble sits across the whole gutter between two panels, they stay one scene (44 of 1,122 panel breaks).
- It takes about 0.13 s for each width of strip on the CPU (0.2 s for strips 1,000 pixels wide or more): 10 to 40 seconds more per test chapter. The 8-bit model runs no faster on an NVIDIA GPU, so it stays on the CPU.
- Sound effects drawn as part of the art can still be cut through, as before: the model finds lettering, not drawn effects.
- Chapters already cut keep their scenes; it's used for chapters cut from now on. **Detect panels** with **Redo** cuts a chapter again (its text, script and voice are then made again too). Comic and manga pages are cut at their gutters as before.

Why not a trained panel cutter, as some apps advertise? None exists for webtoon strips. The AI cutters other apps use are YOLO models trained on manga pages, run on whole pages, and anything trained with Ultralytics' YOLO is under the AGPL; the other open panel models (Magi, Koharu) are for non-commercial use only. The strongest open recap tool also cuts webtoon strips by rules, with models marking only the text, bubbles and faces not to cut through, which is what the bubble finder does here.

## Limits

- Panel detection is rule-based, with an optional trained model that keeps a webtoon's cuts out of speech bubbles ([Cutting webtoons around speech bubbles](#cutting-webtoons-around-speech-bubbles)). It handles clean gutters and webtoon strips well. Borderless or overlapping art may come out as one large panel, which the video still pans across.
- A webtoon strip is cut into pieces at its gutters and across the white space inside panels, but not through lettering: a caption's or bubble's lines stay in one piece, so a still never shows half a caption and the text is read whole. On seven test chapters (six English, one Korean) this took the cuts through a line of text from 144 to 33; what's left is mostly sound effects, titles drawn as art and scan sites' watermarks, plus a few lines whose letters touch their bubble's outline. Projects detected with an earlier version keep their pieces until **Detect panels** runs again. Cuts can still go through a bubble between its outline and its text, unless the bubble finder is downloaded (above).
- Text is read in English, and in Korean, Chinese and Japanese with an optional add-on (see [Korean, Chinese and Japanese comics](translation.md)). Comics in other languages aren't read yet.
- Without an NVIDIA GPU, the Kokoro voice and text reading run on the CPU: expect a couple of seconds per line or panel. Without any GPU encoder (NVIDIA, AMD or Intel), video export runs at roughly half speed.
- Not tried for real: AMD's and Intel's video encoders (no such card here), Resolve's own transitions in Resolve itself, and Groq, Mistral and ElevenLabs with real keys (stand-in servers only).
