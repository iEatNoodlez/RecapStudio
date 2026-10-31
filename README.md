# Recap Studio

A local, account-free take on RecapSynth. It turns comic, manga or webtoon pages into a narrated recap video. Everything runs on your machine: no login, credits, watermark or server.

| Step | What it does | Uses (all open source / open weights) |
|---|---|---|
| 1. Detect panels | Cuts pages into panels. Webtoons are stitched into one strip and cut at the gutters, so panels that cross page breaks stay whole. | OpenCV |
| 2. Read text | Reads speech bubbles and captions in each panel; optionally guesses who says each line. | RapidOCR (PaddleOCR models on ONNX Runtime); Magi v2, an optional download |
| 3. Write script | Writes narration for each scene, carrying the story forward. Online writers read a whole chapter, pictures included. | Your Ollama model, or Claude, OpenRouter, Google Gemini, DeepSeek, Groq or Mistral with your own key |
| 4. Voice | Speaks each line, or splits your own recording of the chapter into its lines. | Kokoro TTS; or Microsoft Edge's online voices, or ElevenLabs with your own key; Vosk for your own recording, an optional download |
| 5. Render video | Pans tall/wide panels, slowly zooms others (toward the main subject, optionally), can paint the text out of speech bubbles, adds crossfades, mixes the narration, and writes `.srt` subtitles. | OpenCV + ffmpeg; U2-Net-p, an optional download |

## Install

Download from the [Releases](../../releases) page (Windows 10 or 11, 64-bit):

- **`Recap-Studio-<version>-setup.exe`**: installs into `C:\Program Files\Recap Studio` for everyone on the PC (Windows asks for admin rights, and again for each update), or, if you pick **Install for me only** at the start, into `%LOCALAPPDATA%\Programs\Recap Studio` without them. It adds a Start menu shortcut (and a desktop one if you tick it). Installing a newer version over it keeps the way it was installed, and your projects and settings.
- **`Recap-Studio-<version>-portable.zip`**: unzip it anywhere you can write to (not Program Files) whose path is under about 90 characters (Windows' 260-character limit on file paths, with the app's deepest files and room to unpack an update), and run `Recap Studio.exe`.

Both carry their own Python with every package, so there's nothing else to install. The first run downloads the voice model (354 MB). Where your files go (uninstalling and updating leave all of them):

- **Projects**: one folder per project, named after it, in `Documents\Recap Studio`. **Settings → Projects folder** moves them anywhere (another drive, say), and **⋯ → Show in folder** opens a project's folder. First-run setup shows the folder, and warns when OneDrive or another cloud service syncs it: a recap runs to several GB.
- **Settings, API keys and logs**: `%APPDATA%\Recap Studio`.
- **The voice model and the add-ons** (GPU support among them): `%LOCALAPPDATA%\Recap Studio` (gigabytes, so kept out of roaming profiles).
- **The portable copy** keeps all of it next to itself instead: `data` (with `data\projects`) and `models`.

Copies installed by 0.1.0 kept everything next to the app; the first start after updating moves it to these places. The window uses WebView2, which comes with Windows 11 and current Windows 10. The packaged app carries the CPU build of ONNX Runtime, the engine text reading and the voice run on; with an NVIDIA card, **GPU support** (offered in first-run setup and Settings → Performance) downloads the GPU build, 1.3 GB, so they run on the card instead (see [GPU](#gpu)).

**Updates**: once per start the app asks GitHub whether a newer release is out (one request to GitHub's API, with nothing about you in it; Settings → Updates turns it off, or checks now). When there is one, **⬆ Update to x.y.z** appears at the bottom of the sidebar. It shows what's new, then **Update now** downloads the release's file, checks it against the SHA-256 GitHub lists for it, closes the app, installs it and opens it again: an installed copy runs the new setup, and a portable copy swaps in the new program files, putting the old ones back if anything goes wrong. Your projects, settings and models are never touched. It waits while a job is running. **Skip this version** hides the button until the next release. A copy run from source isn't checked: `git pull` updates it.

## Run from source

### Setup (once)

Needs Python 3.10+. For the free local script writer, also Ollama with at least one model pulled (for example `ollama pull gemma3`); the online writers only need an API key.

```powershell
powershell -ExecutionPolicy Bypass -File setup.ps1
```

This creates `.venv`, installs packages, and downloads the Kokoro voice model (about 354 MB) into `models\`.

### First-run setup

The first time the app opens, a short setup walks through everything you'd otherwise look for in Settings, saving each choice as you make it:

1. **This computer**: where projects are saved (with **Change…**), and whether the voice model, ffmpeg, an NVIDIA GPU and a GPU video encoder (NVIDIA, AMD or Intel) are there. If the voice model is missing (setup.ps1 skipped or failed), it downloads it (354 MB from the kokoro-onnx project's GitHub release, checked against its size and SHA-256). The installed app reads text and voices lines on the CPU even with an NVIDIA card (it carries the CPU build of ONNX Runtime); the card still encodes the videos, and setup says which is which. With a card, the CPU line has a **Download GPU support** button (1.3 GB, see [GPU](#gpu)), with Cancel; it's used from the next start.
2. **Script writer**: Claude, Claude Code (your Claude plan, with its sign-in), OpenRouter, Google Gemini, DeepSeek, Groq, Mistral or a local model, with the API key and model for the one you pick (or how to install Ollama).
3. **Voice**: pick a Kokoro voice and speed, and play a sample (Settings → Voice also has Microsoft Edge's online voices and ElevenLabs).
4. **Video encoding**: the GPU's encoder or the CPU, with how long an hour of 1080p video takes on this computer. How each video looks is chosen per project, when it's created.
5. **Optional downloads**: the English text-reading model (ticked; 9 MB), the character scan models (586 MB) and Magi (4.9 GB, after agreeing to its license). They download in the background.
6. A summary.

**Skip setup** closes it for good; **Settings → Run setup again** brings it back any time.

### Run

Double-click `run.bat`. It opens a desktop window (WebView2, the same engine RecapSynth uses).

To use your normal browser instead:

```powershell
.venv\Scripts\python -m recap --browser
```

### Building the installer

`packaging\build.ps1 -Version 1.0.0` builds `dist\Recap Studio\` (the app with its own Python from [python-build-standalone](https://github.com/astral-sh/python-build-standalone), the packages in `requirements.txt` plus imageio-ffmpeg, and `Recap Studio.exe`, a small launcher compiled from `packaging\launcher`), zips it as the portable download, and, when [Inno Setup 6](https://jrsoftware.org/isinfo.php) is installed, compiles `packaging\installer.iss` into the setup. Pushing a tag such as `v1.0.0` to GitHub does the same in GitHub Actions (`.github\workflows\build.yml`) and attaches both to a release; running that workflow by hand keeps them as the run's artifacts instead. The app's update check (`recap/updates.py`) reads the latest release of `iEatNoodlez/RecapStudio` and picks its file by name (`-setup.exe` for installed copies, `-portable.zip` for portable ones), so keep those names. Tags are versions (`v1.2.0`, or `v1.3.0-beta.1` for a pre-release, which GitHub's "latest" leaves out).

## Using it

A project opens on an overview: four tiles saying how far the chapters, script, voice and video are, and one main
button that does whatever comes next (**Make the recap**, **Continue the recap**, or **Make the video again**), with
the time it takes beside it. Below are tabs: **Chapters**, **Script** (the scenes and their narration),
**Characters** and **Video** (the player, its files, the project's video settings and YouTube-safe). Showing the
project's folder, saving the project as a file, removing all chapters and deleting the project are in the **⋯** menu.

**New project**: type a name at the top left and press **Create**. Four pages of questions follow, starting from your
last project's answers: what kind of comic it is (webtoon, manga, a left-to-right comic, or not sure); its
characters (whether Make the recap finds them, in the first 5 chapters or all, and whether you check the suggested
names or trust them: see Scan for characters); how its video looks (landscape or portrait, automatic, pan and zoom
or stills, resolution and frame rate, with how long an hour of it takes to make on this computer); and YouTube
(whether a title and thumbnail are suggested after the script, and whether it's YouTube-safe). The video settings
are the project's own from then on: its **Video** tab changes them, and its **Project settings** the characters. (Until 0.1.1 they were app-wide; each project made before keeps the
choices it was made with.) The encoder is the one app-wide video setting (Settings → Video).

**Advanced mode** (the switch at the bottom of the sidebar, remembered) shows everything else: the step buttons,
ticking chapters to work on just those, each chapter's counts, reordering and your own voice-over, Export for
editors, the **Prompt** and **Project settings** tabs, the text read from each scene, split and join, re-running
selected scenes, and the rest of Settings (performance, text reading, the add-ons, Magi, the finer video options).
In simple mode, long help text shows two lines: hover it for the rest, or click it.

1. Create a project (the New project questions above) and click **Add chapters** or **Import folder…**. Pick the series folder; every subfolder (at any depth) that contains images becomes a chapter, and so does every `.cbz`/`.zip` file. Review the list, untick anything you don't want, and import.
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
4. Run **Render video**. It renders the ticked chapters, in order, into the project's `output` folder: `recap.mp4` for the whole project, or `recap - Chapter 1 to Chapter 10.mp4` for a range, each with matching `.srt` subtitles, `.wav` narration and a YouTube chapter list (see below).

### Rendering chapter by chapter

The video is made one chapter at a time. Each chapter is kept as a finished piece (its video, narration and subtitles) in the project's `render` folder, under a fingerprint of everything it depends on: its pages or panel pictures, the voice files, the narration and the video settings. Then the pieces are joined into the final video without encoding the picture again (the narration is encoded once, as AAC).

- **Picks up where it stopped.** Cancel a render, close the app or lose power, and the next Render video reuses every chapter already finished and only makes the rest. **Pause** works too, between chapters.
- **Only what changed is made again.** Edit a line and regenerate its voice, and only that chapter is rendered again; the others are reused. Changing a video setting (size, frame rate, style, timing, encoder) makes every chapter again, since they all look different.
- Chapters fade briefly to black between each other (over the crossfade time), so each can be made on its own. Scenes inside a chapter still crossfade.
- The pieces take about as much disk space as the video. Old versions of a chapter are deleted when it's made again, and a deleted chapter's pieces go with it.
- The time estimate for Render video counts the chapters still to make, plus joining them all into the video (about a minute and a half per hour of video, on one CPU thread; the app learns this computer's own speed). The progress bar gives each its share: it moves with ffmpeg's position while the chapters are joined, instead of waiting at the end.

**Split into parts** (the project's Video tab, advanced mode): one video; a new video every N chapters; or videos of at most N hours (YouTube takes up to 12 hours per video; the default is 11). Parts are cut between chapters, from each chapter's exact length, and named `recap - Part 2 (Chapter 21 to Chapter 40).mp4` and so on, each with its own subtitles, narration and chapter list. Changing the split only joins the pieces again, which takes about a minute and a half per hour of video.

**YouTube chapters**: next to each video is `<name> - YouTube chapters.txt`, one line per chapter with its start time (`0:00 Chapter 1`, `6:12 Chapter 2`, or `1:02:05 …` past an hour). Paste it into the video's description and YouTube shows the chapters on the timeline (it needs at least three, each 10 seconds or longer). Under the player, the app has a button for each part and links to its subtitles and chapter list.

### Export for video editors

**Export for editors…** (after the step buttons, in advanced mode) saves the ticked chapters (none ticked = all) as an OpenTimelineIO timeline to finish the recap in a video editor: `recap (for DaVinci Resolve).otioz` or `recap (for editors).otioz` in the output folder. An `.otioz` is a zip holding the timeline and all its media, so it can be moved to another computer. The timeline has:

- a clip for each scene, timed like the rendered video (a scrolling chapter is cut into a clip per scene, at points where the cut doesn't show);
- the narration as a clip for each line, on Audio 1; the subtitles are saved next to the bundle as `<name>.srt`;
- a cross dissolve wherever the video crossfades (starting on the cut, like the video's), and a marker at the start of each chapter.

Choose what it's made for:

- **DaVinci Resolve**: each scene's picture on Video 2, over its blurred backdrop on Video 1, with the video's pan, zoom or scroll as keyframes of Resolve's Transform effect (zoom and position), so the motion can be changed in the Inspector. Pan and zoom ease in and out like the video's (Bezier handles); a scroll gets a keyframe wherever its speed changes. Very long scrolls and pans are cut into clips of at most 7 screens, since Resolve's position only goes 4 picture-heights either way. No video is rendered, so it's quick: for the 3-chapter test series (186 scenes), about 4 seconds and 13 MB at 360p, or 10 seconds at 1080p. Other editors don't read Resolve's keyframes and show the pictures still.
- **Any editor**: each chapter rendered once with the motion drawn in, every scene running on for the length of a crossfade (the frames an editor's crossfade needs), and the timeline cuts it into a clip per scene. It takes about as long as Render video. These videos are kept in the project's `editors` folder and reused while their chapter doesn't change, so exporting again only renders the chapters that changed.

**Transitions** (for DaVinci Resolve, in the export dialog): *Cross dissolve* like the video, or one of Resolve's own: *Slide*, *Push*, *Blur dissolve*, *Smooth cut*, or *Mixed* (dissolve, slide, push and blur in turn, the same on both video tracks at each cut). They come in with Resolve's default settings, to change in the Inspector; other editors may show them as a plain dissolve or a cut. They're written the way Resolve itself exports a transition (OpenTimelineIO's sample of a Resolve export has one), with Resolve's numbers for each transition taken from RecapSynth's export. The rendered video and the *Any editor* export keep cross dissolves.

To open it in Resolve, first set the project's timeline resolution and frame rate to the video's (the project's Video tab) and keep **Image scaling** on *Scale entire image to fit* (the default; the keyframes are worked out from it). Then use **File → Import → Timeline** and pick the `.otioz`. Premiere Pro opens OpenTimelineIO from version 25.6. For an editor that can't open `.otioz`, rename it to `.zip`, unzip it and open `content.otio` (its media is next to it in `media\`).

How it was checked, on the test series:

- Every object in the timeline has exactly the fields of the same kind of object in a timeline DaVinci Resolve exported itself (from OpenTimelineIO's test files). Every clip's media is in the bundle, the crossfades have the frames they need, and each narration line starts on the frame the video plays it.
- The official OpenTimelineIO library (0.18.1) opens every kind of bundle and finds each clip's media in it. It also places the crossfades on the cuts, keeps Resolve's keyframes, reads the chapter markers, and writes the timeline back out unchanged, using the same schema versions as the export. It caught one mistake, since fixed: markers were written in an old format that every release from 0.14 on misreads.
- The Resolve keyframes follow what Resolve writes: zoom relative to the picture fitted into the frame, position as a fraction of that fitted picture's size (up is positive), keyframes counted from the clip's first frame. Applied that way, a scene's pan and zoom put the picture exactly where the video does (to within 0.0001 px, at every frame checked). A scroll lands within half a pixel, because the video rounds the scroll to whole pixels while an editor places it exactly. Frames put together from the bundle match the video's.
- For any editor, the clips are the video's own frames (frame for frame), and exporting again reuses every chapter that didn't change.
- Resolve's own transitions: each is laid out exactly like the transition in Resolve's sample export, at the same frames and lengths as the cross dissolves, and nothing else in the timeline changes; the official library reads them (as custom transitions, keeping Resolve's block) and writes them back unchanged.

It hasn't been opened in Resolve or Premiere Pro themselves yet: how Resolve reads keyframes and its own transitions when it imports a timeline comes from its own exports and from other programs that write them, not from Blackmagic's documentation.

### Saving a project as a file

**Save as file…** (at the top of a project) writes the whole project into one `.recap` file in its output folder, to keep as a backup or move to another computer. It holds the pages (pages imported from a folder are copied in, so the file doesn't need that folder), panels, script, voice lines, character pictures, the character scan, your recordings and the scenes' subjects. The rendered video, its pieces, the videos for editors and the pictures without text are left out, since the app makes them again. Pictures and sound go in as they are, so the file is about the size of those folders (the 3-chapter test series: 90 MB).

**Open project file…** (in the sidebar) takes the file's path and opens it as a new project (named "… (2)" next to one of the same name), with everything in its own folder. Files that aren't project files, or that hold anything outside the project's folders, are refused, and a file from a newer version of the app asks for an update.

### Time estimates

Next to the main button, a line says how long what's left takes; in advanced mode another breaks it down for the ticked chapters, step by step; hover a step button for its own estimate (what's left, and to redo everything), and the Continue / Redo choice shows both. While a job runs, its progress line says about how long is left in the current step (time spent paused doesn't count).

Each estimate is the work a step would do (pages, panels, chapters or scenes, narration lines, video frames) times how fast that work goes. The speeds start from measurements on an RTX 5060 Ti with 12 CPU threads and are replaced by your computer's own as steps finish (blended with what was known, and saved in `data\speeds.json`), per device, script writer and model, and video size, style and encoder. The video's length is worked out from the narration (its audio, or before there's a voice, its length at the voice's pace: about 17.5 characters a second). When the main button will write the script first, chapters without narration are counted as getting a typical amount (about one line per three panels).

### Scenes

Panel detection cuts webtoon strips finely, at every gap between panels, including gaps crossed by a speech bubble, so it doesn't miss panel boundaries. The pieces of each detected panel start out joined into one **scene**: one narration line, shown in the video as the pieces stacked back together. In the scene list, **✂ split here** makes a piece start its own scene, and **⤒ Join with scene above** merges two scenes (their narration is combined). Claude regroups the pieces itself when it writes a chapter.

### Settings

- **⚙ Settings** (bottom of the sidebar) holds the app-wide choices shared by every project: the script writer (local model, Claude, OpenRouter, Google Gemini or DeepSeek, with models, API keys and the Ollama connection), the voice and the video. **Run setup again** at its top reopens the first-run setup. They're stored in `data\settings.json`. The line under the step buttons shows what's in use.
- **Project settings** (a tab of each project, in advanced mode) holds what differs per series: layout, reading direction and smallest panel size. The **Prompt** and **Characters** tabs are per project too, and so is YouTube-safe (the **Video** tab).

### The scan group's pages: left out automatically

Scanlated chapters often open and close with the group's own pages: its banner and credits (translator, proofreader…), Patreon and Discord notices, "read early" ads, the other series it does and recruiting. These are left out of the script and the video on their own (**Project settings → Leave out the scan group's pages at the start and end of chapters**, on by default). The series' own cards and notes stay: its title and "To be continued" cards, the creators' credits (story, art, colouring, adaptation), warnings and season notes.

- **How they're found**: a piece within 30 of either end of a chapter that is the same picture as one near the ends of at least two other chapters (one other, in a project of up to three chapters), or, once text has been read, whose text is a credit or a notice (Patreon, Discord, "read early", translator, proofreader, recruiting, "other series we do", a collab…). Story panels aren't repeated like that; the one exception, a chapter opening on the previous one's last panels, repeats in a single other chapter, so it doesn't count. Pictures are compared at 64 × 64 in grey and must be nearly the same: with a looser comparison, a series' look-alike speech bubbles counted as repeats. A web address or the group's name alone doesn't count, since groups put their watermark on story panels too; nor do "join us" or "recruit" alone, which characters say.
- **The series' cards stay**: once text has been read, a piece whose words are the series' own ("To be continued", "Art / …", adaptation, episode 12, a warning, "season 2"…) stays even though it repeats, and so do its copies in other chapters (one copy's text can be garbled where another's isn't), and the rest of a card cut into pieces. The series' title alone isn't enough, since the group's header names the series too: a piece with the title stays when it's next to the series' words, or not next to the group's, and never when it carries the group's name or address. When the group's banner is drawn into the same piece as "To be continued", the piece goes.
- **What's left out**: from each end, everything up to the last of the group's pages, except the series' cards, carrying on past at most two other pieces between them (a chapter number or a credits list that changes each time), or four once their text has been read and none could be dialogue (the covers of the group's other series between its ads). Blank pieces don't count either way. Then a little more of that piece's scene: blank pieces and up to two more that are the group's, or have no text at all (not even the watermark, which marks a story panel). Panel detection sometimes joins a chapter's last lines to the notice after them, so the rest of a scene isn't taken whole.
- **Chapters that are only a notice** (postponed, a hiatus, a season break, the creators' notes) are left out whole once text is read, so they get no video: a chapter of 40 pieces or fewer with a notice's words in its text ("postponed", "hiatus", "season 2", "will return", "circumstances", "readers"…; an apology counts only as a second sign, since characters apologize too). Story chapters had 55 pieces or more in all three test series. On an 84-chapter test series it finds exactly its four ("34-5", "34.5", "71.5" and "78.5").
- **Your choices carry over**: after you put back or leave out a piece (**Use it** / **Leave out** in the dialog below, **Use it** on a piece, or **Use in video** on a scene), if the same picture is at the start or end of other chapters, it asks whether to do the same there: **Just this one** or **Use them too** / **Leave them out too**. Either way the choice is remembered, and when the pages are found again (Read text, Find them now, chapters added later), copies of a piece you put back stay and copies of one you left out go. The first look for copies reads every chapter's ends (about 8 seconds on an 84-chapter series); after that it's under half a second. On that series, your earlier choices alone put back 215 copies of the series' cards before any text is read.
- **When**: after **Detect panels** (by the pictures alone), and again after **Read text**, which reads the left-out pieces too, so their words can tell the group's pages from the series' cards and put the cards back. **Find them now** (Project settings) runs it on the ticked chapters (none ticked = all), for chapters already detected. In a project read before this, **Read text** (Continue) reads the left-out pieces it skipped before.
- Left-out pieces are unticked in the scene list and marked "left out: credits"; tick **Use in video** to keep one, and it stays kept when they're found again (so does one you untick yourself). When a scene mixes story and the group's pages (panel detection sometimes joins a chapter's last lines to the notice after them), its left-out pieces are shown dimmed, outlined and tagged, with **Use it** to put one back; ticking the scene's **Use in video** again brings back its story pieces but leaves those out (a scene made only of them comes back whole). A scrolling chapter doesn't show them at all: the scroll starts with the first scene used, stops with the last, and jumps past any left out between (a Discord notice between the last story panel and a kept "To be continued" card). Chapters where nothing is left out keep their rendered video pieces. A chapter with every piece left out (one that's only a notice, say) gets no video at all.
- **All in one place**: **Left out: N pieces…** above the scene list (and **See what was left out…** in Project settings) opens every left-out piece of the ticked chapters (none ticked = all), chapter by chapter, to check them at a glance. **Use it** puts one back, **Leave out** undoes that, and a click shows a piece full size. **Show the ones you put back** lists those too. On the 84-chapter test series it opens 973 pieces from 81 chapters in under a second.
- The character scan looks only at pieces that are used. Groups from an earlier scan that were seen mostly on left-out pages (characters from the group's ads for its other series) are set aside in the review, marked as such; **Reopen** brings one back.

On an 84-chapter test series (scanned by one group whose banner changes every few chapters) it left out 991 pieces at the ends of 81 chapters in 9 seconds before text was read, and set aside 21 of the character scan's 49 groups; the main cast stayed. By pictures alone the series' "To be continued" cards go too, since they repeat in every chapter, and a few closing notices cut differently in each chapter stay; Read text sorts both out.

Measured after text is read, against every chapter's start and end checked by hand:
- **The 84-chapter series**: 979 of the 998 pieces you leave out are found, and none that you keep is taken. The ones not found are 18 near-white slivers and one header piece ("Chapters 2 & 3 are…"). Before this, 104 pieces you keep were taken (91 of them the series' cards) and the four notice-only chapters were missed.
- **A 55-chapter series**: the ads still in its video before are now found: 26 pieces in 7 chapters, the covers of the group's other series between its Patreon pages and the Discord notice before the "To be continued" card. Its title card, "To be continued" cards, the creators' credits and the studio's logo stay (98 pieces), and so do 3 story panels taken before (two with the group's watermark). 2 scraps of garbled credits are taken.
- **A third series** (detection never run on it): its title and "To be continued" cards stay, once its thin pieces are read with the margin. It leaves out 4 pieces: the group's "read it on our site" card, and 3 opening panels with no words that the series repeats from earlier chapters. Before, it took 175, nearly all the series' cards, and the story before the title card in chapter 2.

### Webtoon video: scrolling

For webtoon chapters, the video can scroll down the whole chapter as one continuous strip instead of showing scenes one at a time (Project settings → **Webtoon video**; scrolling is the default). Each scene's narration plays while the middle of the screen passes through that scene, so the scroll slows down for long lines and moves faster through quiet stretches, with a speed limit so it never races. Scenes left out of the video (such as the scan group's pages) aren't shown at all: the strip is the scenes used, each stretch joined straight onto the next. The cuts between pieces never show, because the video shows the real strip.

Chapters detected before this feature get their positions in the strip recovered automatically when the video is rendered (detection is re-run and matched to the existing panels; narration and voice are kept). A chapter that can't be matched is shown as scenes, and the job says which ones; detecting its panels again makes it scroll.

When Claude writes a scrolling chapter, it's told the chapter is one continuous scroll, so it can write lines that cover a few quick panels instead of narrating every fragment.

### Video size, frame rate and style

Settings → **Video** applies to every project:

- **Format** and **Resolution**: landscape or portrait, at 1080p (1920×1080), 720p (1280×720), 480p (854×480) or 360p (640×360). Smaller videos are much quicker to make: 720p has under half the pixels of 1080p, 360p about a ninth. Scrolling keeps the same pace through the story at every size.
- **Frame rate**: 60 down to 5 fps. Scrolling and pan and zoom look smooth from about 24 fps; stills look the same at any rate.
- **Style**: *Automatic* scrolls webtoon chapters (when the project's Webtoon video is set to scroll) and pans and zooms other comics; *Pan and zoom, scene by scene*; or *Stills, scene by scene*. A still is drawn once and reused for every frame of its scene, so stills are the fastest style. With a style other than Automatic, the script writer is no longer told chapters scroll.
- **Pan and zoom toward**: the middle of the scene, or *the scene's main subject*. The subject is found by U2-Net-p (Xuebin Qin et al., Apache 2.0, as converted to ONNX by the rembg project), a 4.6 MB download into `data\addons\focus` from Settings, checksum-checked. It marks what stands out in a picture (usually the character; in a panel that's only a caption, the caption); a scene that zooms then zooms in toward it, up to 1.4 times past fitting the frame, so the subject fills about three quarters of it, with the picture staying inside the frame where it fits and covering it where it doesn't. A subject near the picture's edge gets less zoom, so it never ends further from the middle than it starts. Each scene is looked at once (about 0.03 s on an NVIDIA GPU, 0.2 s on the CPU) and remembered in the project's `focus.json`. On the test series it found a subject in 184 of 186 scenes, and all 62 zooms ended with their subject nearer the middle. Tall and wide panels still pan from end to end. The Resolve export moves the same way (its keyframes match the video exactly).
- **Remove the text from the pictures**: the text in speech bubbles and caption boxes is painted over in the video (and in the export for editors), so the art carries the scene while the narration tells it. Text is found by the same reader as Read text (no other model), grouped into bubbles, and painted out only where it sits on a plain background: the band around it nearly all one colour, and its own box mostly that colour too. Letters on a flat background are painted in that colour; on a shaded one they're filled in from around them. Sound effects and signs drawn over the art, and bubbles with busy surroundings, are left as they are (painting them out would leave a smudge). On a made-up picture the bubble and caption were cleared with their outlines kept and nothing else changed; on 10 test-series panels the text the reader still finds went from 47 lines to 22 (what's left is mostly sound effects). The first video made this way reads every picture again (about 0.1 s each on the GPU; the test series' 320 panels took 34 s); the cleaned copies are kept in the project's `textless` folder and reused, and copies no longer used are deleted when the whole project is rendered.

### YouTube-safe (age-restricted videos)

The **Video** tab → **YouTube** → *YouTube-safe* is set per project, for a recap that goes on YouTube as an age-restricted video: as far as such a video can go, and no further.

- **The script**: the writer (Claude, another online service or the local model) is told to tell sex scenes the way an R-rated film shows them. Kissing, undressing, being naked and that they had sex are said plainly, in a line or two; named acts, genitals, nipples, hands or mouths on breasts, orgasms and fluids are left out, and the rest of a long scene's lines carry what the characters say and feel. Forced or pressured sex is named once in a plain, non-sexual sentence and told through what it leaves behind. Switching it on doesn't change a script already written: redo those chapters for the milder wording.
- **Check wording**: after Claude, another online service or Claude Code writes a chapter, a word check (`recap/wording.py`) looks for what YouTube-safe leaves out: named acts, body parts and orgasms in any scene, and in a forced or pressured scene the act told step by step (taking her clothes off in more than one line, his excitement in more than two, her body reacting or her seeming to enjoy it). The Claude Code writer asks its agent once to reword what it finds. Whatever is still there shows as **Check wording** next to the chapter (and on the Script tile), with "⚠ check wording" on those scenes in the Script tab. Click it to read the lines, then edit them (an edited line is no longer flagged), **Write the chapter again**, or **Keep it as it is**. Voice won't voice flagged lines without asking: **Make the recap** (or Voice by itself) pauses before voicing them, with **Check wording** in the job box, and carries on by itself once they're checked. **Write the chapter again** there writes that chapter again and then carries on with the rest of the recap; **Resume** voices the lines as they are. It's only a word search, so it can flag a line that's fine and miss one that isn't. On a 5-chapter test sample it flagged the two chapters that had to be redone (5 and 18 lines) and Claude Code's first try at chapter 5 (12 lines), and none of the chapters that were kept.
- **Black boxes**: the video (and the export for editors) covers nudity with black boxes. Each piece keeps its boxes, which come from three places:
  - *The box finder*, an optional add-on: anime_censor_detection v1.0 (deepghs, MIT), a model trained on drawn art to find exposed nipples, penises and vulvas; a 44.6 MB download into `data\addons\boxes` from Settings → Video, checksum-checked. **Find boxes now** (Project settings) runs it on the ticked chapters, and Render video runs it first on any piece it hasn't seen. A webtoon chapter is searched along its whole strip, in square windows that overlap by half, each looked at as it is and mirrored (on one test webtoon a nipple scored 0.16 to 0.35 as the window moved a few pixels, and mirrored looks found ones the plain ones missed), so a spot a cut between two pieces splits is still seen whole; each piece then gets its share of the boxes. Small pictures are never enlarged for it (enlarged scraps of speech bubbles gave false finds). Boxes get a margin, more round genitals than nipples, and two genital finds close together are bridged. On three chapters of that webtoon (469 pieces, 37 s on an RTX 5060 Ti) it covered nipples and genitals in ordinary shots well; it left part of one extreme close-up uncovered, and put a few small stray boxes on chins and mouths in kisses. So look the boxes over.
  - *The script writer*: in a YouTube-safe project each request also asks for boxes over nudity, which are saved on the pieces. They're rougher; where the finder boxed the same spot, the finder's box is used, and the writer's catch what the finder misses.
  - *You*: click any piece and drag over what to cover; ✕ removes a box (any of them). **See the boxes…** lists every piece with boxes, chapter by chapter, and **Find again in these chapters** replaces the finder's boxes (yours and the writer's stay). Scenes with boxes show "■ N boxes" in the scene list, and the boxes are drawn on their pieces.
  The boxed pictures are copies in the project's `covered` folder, named after the picture and its boxes, so changing a box re-renders only that chapter.

**It can make mistakes, so double-check all of it before uploading**: watch the whole video and read the script. On a 5-chapter test sample, 67 of the finder's 204 boxes were on things that needed none (clothed crotches, faces, shoes, a door, the studio logo), all on pieces the script writer had left unboxed; a fix is planned.

### Script writer: local model, Claude or another online service

Settings → **Script writer** picks who writes the narration (with an online writer selected, the local-model options are hidden except the one used when it can't write a chapter):

- **Local model (Ollama)**: free, runs on your GPU, a few scenes at a time.
- **Claude**: sends a whole chapter per request. Claude sees every piece in order with its text, groups pieces into scenes (fixing pieces the detector cut too finely), writes the narration, and keeps a running story summary that is carried into the next chapter. Pieces are sent at most 720 px wide, and tall ones are cut into slices so text and small details stay legible. Claude is also told the series title (the project's name; importing a folder into an "Untitled" project names it after the folder) and how to read a comic: whose voice the caption boxes are, who each bubble belongs to, thoughts versus speech, and daydreams or flashbacks versus what really happens.
  - Choose Haiku 5.5, Sonnet 5.5 or Opus 5.5; Settings shows the cost and how each did under the picker. On a 105-piece webtoon chapter (about 77K input tokens) they measured about 1¢ (Haiku), 20¢ (Sonnet) and 40¢ (Opus) per chapter, each in under a minute. Haiku mixed up who was speaking and narrated a daydream as real events; Sonnet and Opus read it correctly, Opus with the fewest slips. The tokens each request used are written to `data\app.log`. It needs an API key from console.anthropic.com, billed separately from a Claude subscription; paste it in Settings, where it's stored only on this PC (`data\claude_key.txt`). Very long chapters are split so each request stays under 100K tokens. If Claude declines a chapter (it won't process explicit images), that chapter is written by the local model instead and marked "local fallback" in the chapter list. Sonnet and Opus first retry a declined request on another Claude model.
- **OpenRouter**, **Google Gemini** and **DeepSeek** work the same way as Claude: a whole chapter per request, the same instructions, pictures, regrouping, story summary, pause on credit or key problems, waiting when busy, and the local model for a chapter they can't do. Each takes its own API key (stored only on this PC, in `data\openrouter_key.txt` and so on; the `OPENROUTER_API_KEY`, `GEMINI_API_KEY` or `GOOGLE_API_KEY`, and `DEEPSEEK_API_KEY` environment variables also work).
  - **OpenRouter** gives one key and one balance for hundreds of models from many companies. Settings lists them, marks which read images, and shows each one's price and roughly what a chapter costs (based on a chapter here being about 70K tokens in and 5K out). Pick a model that reads images. Each request's actual cost is written to `data\app.log`.
  - **Google Gemini** (key from aistudio.google.com) defaults to `gemini-3.8-flash`; Settings lists the Gemini text models. There's a free tier with lower limits. Tested with a real key (October 2026): the list includes models a key can't actually use (the Pro models had no free-tier allowance, and `gemini-2.5-flash` is "no longer available to new users"), and the Flash models were often "experiencing high demand". `gemini-3.5-flash` wrote Chapter 1 of a webtoon correctly in about a minute (132K input tokens as Google counts them, 1K out), with half as many narration lines as Claude Opus.
  - **Test this model** (next to the model, and in first-run setup) sends it a one-word request, so a model that's overloaded, has no allowance on your key or isn't available shows up before a run.
  - **DeepSeek** (key from platform.deepseek.com) defaults to `deepseek-flash`. Its models may not read images: the first time one turns pictures down, the app sends that chapter again as text only and remembers it, which loses who is where and anything shown without words.
  - **Groq** (key from console.groq.com) is very fast and has a free tier with low limits (the job waits when it reaches them). Its image models take only 3 pictures a request (Qwen 3.8 27B, the default; Muse Spark Open takes 5), so a chapter is sent in many small parts, a few scenes each; a scene with more pictures than that is sent with its first 3. A reasoning model's thinking, when it shows it, is skipped when reading the answer.
  - **Mistral** (key from console.mistral.ai) defaults to `mistral-medium-latest`; Medium, Small, Large and Ministral read images (its model list says which), at most 8 pictures a request. It has a free plan with lower limits.
  - They all speak OpenAI's chat-completions format. The answer's shape is asked for as a JSON schema (DeepSeek and Groq: JSON mode); a model that doesn't support that, or a long output limit, is asked again in a way it accepts. Groq and Mistral were tested against a stand-in server that answers like theirs, not with real keys.
- **Claude Code**: uses your Claude subscription through Claude Code instead of API credit (`recap/claude_code.py`). The app builds every request exactly as for Claude, then starts a fresh Claude Code agent per chapter (`claude -p`, with only the Read tool and the app's own MCP tools), a few chapters at a time (**Chapters at once**, default 4). Each agent gets its chapter's requests, reads every picture sheet and answers; the answer is checked at once (every piece in one scene, a summary, black boxes, no long silences in a scrolling video, as the narration skill's bridge checks it, and for a YouTube-safe project the wording check, asked once) and saved as Claude's would be. A first narration goes in order: the next chapters' agents draft from the pictures meanwhile, then get the story so far once the chapter before is saved. The agents follow the brief in the recap-studio-narration skill (`.claude\skills`, next to this `App` folder): a series' own saved brief when it has one, else the template, with the YouTube-safe wording for a YouTube-safe project. Suggest names uses Claude Code too.
  - Claude Code has to be on this PC (the Claude desktop app includes it; the app finds that copy) and logged in: **Log in to Claude Code** in Settings opens its sign-in once. If it isn't logged in, or your plan's usage limit is reached, the job pauses with the reason; Resume carries on. An agent that stops early is started again once. Each run's requests, the agents' logs and their reports (`reports.md`) are kept in the project's `claude_code` folder; the picture sheets are deleted once answered.

### Claude Code: the MCP server

Claude Code can work with Recap Studio while it's open, through the app's MCP server, `recap-studio` (`recap/mcp.py`). Register it with Claude Code once, from PowerShell (for a copy run from source, use its `.venv\Scripts\python.exe` and its own folder instead):

```powershell
$app = "$env:LOCALAPPDATA\Programs\Recap Studio"
claude mcp add --scope user recap-studio -- "$app\python\python.exe" "$app\recap\mcp_stdio.py"
```

That starts `recap/mcp_stdio.py`, which finds the open app through `data\app.json` (written while the app runs) and passes each call to its `/mcp`; with the app closed, the tools say so, and `open_app` starts it. Everything goes through the app's own code and store, so the app never needs closing for Claude Code to change a project.

The tools: `list_projects`; `project_status` (per chapter: pieces, text read, scenes narrated, lines voiced, summary); `run_steps` (any step: panels, ocr, script, audio, video, scan, names, credits, boxes, editors); `write_script` (with Claude Code agents: the app's own, or the session's, which then answer with `next_request` and `submit_answer`); `detect_characters` (the character scan, then Suggest names); `job_status` and `cancel_job`; `get_narration` and `set_narration` (one scene's line, its voice cleared). The narration skill uses them: it ships in `.claude\skills\recap-studio-narration` inside the app's folder (the app's own Claude Code writer reads its brief from there), and copying that folder into `%USERPROFILE%\.claude\skills` makes it available in your own Claude Code sessions.

`python -m recap --no-window` runs just the server (for testing); it writes `data\app.json` too.

### Voice: Kokoro, Microsoft Edge or ElevenLabs

Settings → **Voice** picks who reads the narration (**Play a sample** tries the choice):

- **Kokoro**, on this computer: free and private (the default).
- **Microsoft Edge voices** (online, free, no account): the voices Edge reads web pages aloud with, through the edge-tts package (LGPL-3.0). 322 voices in many languages, English first in the list. It isn't an official service, so Microsoft can change or limit it, and the narration is sent to Microsoft. Four lines are read at once: 8 lines took about 2 seconds. A line it doesn't answer is tried again after 2, 5 and 15 seconds.
- **ElevenLabs** (online, paid): with your own API key (Settings, stored only on this PC in `data\elevenlabs_key.txt`, or the `ELEVENLABS_API_KEY` environment variable). Settings lists your voices and its models (with how many credits a character costs), and **Test key** shows how many characters your plan has left this month. It's billed per character: a chapter's narration is typically about 5,000. Two lines are read at once; it takes speeds from 0.7 to 1.2. Tested against a stand-in server that answers like its API, not with a real key.

With an online voice, problems pause the Voice job instead of failing it: a missing or rejected key, no characters left, a voice it doesn't have, or the service not answering after a few tries. Fix it in Settings (or edit the line), then Resume. Speed applies to all three. Changing the voice doesn't redo lines already voiced: run Voice with Redo for that.

### Your own voice-over

Each chapter's **🎙** button (in the chapter list) takes your own recording of the chapter instead of a generated voice. It shows the chapter's script to read (with **Copy**); record yourself reading it in order, as one file (WAV, MP3, M4A, FLAC, OGG and others), and upload it. Speech recognition on this computer, Vosk with its small English model (Apache 2.0; a 41 MB download into `data\addons\vosk`, offered the first time, checksum-checked and unpacked), writes down what was said with each word's time. Those words are lined up with the script's, so each line gets its part of the recording (with a little room at each end), and the clips are used in the video and the export.

- The dialog shows how many lines were found and what share of the script's words were heard, lists the lines to check (heard poorly, or not found), and anything said that isn't in the script. Names and unusual words aren't in the recognizer's vocabulary, so a line with them shows fewer words heard even when it was read right. Under each scene, a recorded line shows 🎙 and its share.
- A line that wasn't read keeps the voice it had (the gap where it would be is used only if there's speech in it), so run Voice for it, or record again. **Voice** never replaces lines from your recording, even with Redo. Editing a recorded line's narration drops its clip, since it no longer matches. **Remove the recording** deletes it and its clips.
- Tested with a made-up recording (Kokoro reading a 12-line chapter as an MP3, one line left out and a sentence added, with room noise): it was split in about 5 seconds; the 11 lines read were found, 89–100% of their words heard, each clip within 0.25 s of the line's length and holding its own line when listened to again; the line left out was listed as not found, and the added sentence as said but not in the script.

### Pause and resume

**Pause** (next to Cancel while a job runs) holds the job after the item it's working on: the current panel or line, or for an online writer or a video render the current chapter, which finishes first. **Resume** carries on from exactly that point, so a redo of every chapter doesn't start over. Cancel works while paused.

When writing with Claude or another online service, the job pauses by itself instead of failing, keeping its place:

- **Out of credit**: add credit (for Claude at console.anthropic.com, Settings → Billing; the message names the place for the others), then press Resume. The chapter that hit the limit is sent again; nothing is skipped or handed to the local model.
- **API key rejected or missing**, or no model chosen: fix it in Settings → Script writer (the settings stay open to edit while paused), then Resume.
- **No allowance left on the key's plan** for the model (for example a free key and a bigger model), or **a model the key can't use**: it pauses straight away (waiting wouldn't help), saying so. Choose another model, or check your plan or billing, then Resume. A rate limit that says when to retry is waited out instead.
- **Service busy or unreachable** (rate limit, overloaded, network, or the connection dropping partway through a reply): it waits and tries again on its own, after 30 seconds, 2 minutes and 5 minutes, with a countdown. If it still fails, it pauses so you can Resume later.

A paused job lives in the running app: closing the app ends it. Finished chapters are kept (hover a chapter's stats in the list to see which model wrote it), so tick the rest and run again.

### YouTube title and thumbnail

After the script, the main button has your script writer suggest a YouTube title (on by default; New project, or the **Video** tab → YouTube, turns it off). The writer gets the chapter summaries, the character list and 24 numbered pictures spread over the story, and answers with a clickbait title in the style of manhwa recap channels (under 90 characters, true to the story, without the ending), three others, a few words for the thumbnail and which picture to use. Recap Studio draws the thumbnail, 1280×720, into the project's `output\thumbnail.jpg`: the picture over a blurred, darkened copy of itself, with the words in big outlined letters beside it (or under it, for a wide picture), the last line yellow. The **Video** tab → YouTube shows both: edit the title (Copy copies it), click another title to use it, change the words and **Redraw**, click another of the pictures to redraw with it, or **Suggest again**. With Claude Code, one agent answers, as for Suggest names. The writer is told never to pick a picture with nudity, and in a YouTube-safe project pictures with black boxes aren't offered and the title stays clean. It's suggested once; the main button doesn't replace it later.

### Prompt and characters

The **Prompt** tab (advanced mode) and the **Characters** tab control how the script is written, per project:

- **Persona**: who the narrator is and how they sound.
- **Rules**: hard requirements, numbered in the prompt (third person, past tense, paraphrase instead of quoting, trust the art for actions and the text for feelings when they disagree, numbers as words so the voice reads them well, and so on). Edit, remove or add your own.
- **Style**: softer guidance on how the narration should read (focus on motivations and stakes, fold small actions together, bridge scene changes, keep characters consistent, close each chapter on its strongest moment). The model is told where each chapter starts and ends.
- **Note for the next run**: anything specific to the chapters you're about to run. It's sent as an important instruction, so clear it when you're done.
- **Reset persona, rules and style** restores the built-in defaults; the note and characters are kept.
- **Characters**: name, gender, the ways the name shows up in the text (OCR often misreads names, e.g. "SEYOLNG"), and a short appearance/role description that helps vision models tell characters apart.
- **Other looks** (under a character): an alter ego, disguise, transformation, younger self or costume, with a short label, a note on what it is and its own pictures. The script writer is told the character "also appears as: pink-haired princess with a crown (her fantasy version of herself)" and that it is still them; Magi counts the look's pictures as that character; and the character scan recognises the look in later scans. A scanned group becomes one with **As another look** in the review (the Name box names the look), or add a look by hand with **+ Another look**.
- **Preview prompt** shows exactly what the model receives for the first batch.

For each batch of panels the model gets those instructions, the last few narration lines for continuity, the text read from each panel and (with **Show panel images to the model** on, and a vision model like gemma) the panel images. Everything goes in one message: in testing, gemma ignored rules such as past tense when they were sent as a separate system message.

Project settings (per project, in advanced mode) cover layout (auto / webtoon / pages), reading direction and the webtoon video; the project's Video tab covers the video's format, resolution, frame rate, style, timing and crossfades; Settings (app-wide) cover the script writer, voice and speed, and the video encoder.

To delete a project, hover it in the sidebar and click the trash icon (or use **Delete** at the top of an open project). This removes the project's panels, audio and videos; folders you imported from are never touched. A project can't be deleted while a job is running on it.

### Text reading

Read text finds each line of text with RapidOCR, then groups the lines into speech bubbles and captions and reads them in order: top to bottom, and left to right for bubbles side by side. Each bubble becomes one line of the panel's text. (Earlier versions merged lines row by row across the whole panel, which wove the sentences of neighbouring bubbles together.) Text that is nearly all capitals is made all capitals, and inside words 5 and 0 are read as S and O ("YE5" → "YES"); numbers and words like "1ST" are left alone.

RapidOCR's built-in recognition model reads Chinese and English, and on English comic lettering it runs words together and mixes up letters ("APAIROFSLIPPERS", "SEYOLNG"). **Settings → Text reading** offers its English model, a 9 MB download into `data\addons\ocr_en` (Apache 2.0, pinned and checksum-checked, not part of the app). On a test chapter it cut run-together words from 46 to 12 and misread words from 30 to 2, at the same speed. Once downloaded it's used for Read text unless switched off. Text already read stays as it is: run Read text with **Redo** on the chapters you want read again.

A thin piece (one line of lettering: a title, "To be continued") is read with a 24-pixel margin around it. Words touching its edges came out garbled: "To be continued" as "0 e e 0", a series' title as "S". On 90 thin pieces from three series, the margin read every such card right and 3 more words of dialogue; on taller pieces it was a wash (some words gained, some lost), so they're read as they are.

### Scan for characters

**Characters → Find characters…** finds the characters in your chapters, groups each one's appearances and suggests what they look like, so you can fill in the character list without typing it all yourself.

Once the scan models are installed, **Characters** also appears as a step of its own, after Read text (the steps are renumbered: 1 Detect panels, 2 Read text, 3 Characters, 4 Write script, …). It opens the scan, or the review when groups are still waiting for names.

The main button finds the characters too, before the script, so the narrator knows who is who. In a project that's never been scanned and has no named characters, it scans the first five chapters it's about to write and suggests names with your script writer, then pauses with the review open. Add or ignore each character and the script is written straight after; **Resume** writes it without them. It pauses like this once per scan, also for groups left from a scan you started yourself (to scan more chapters, use **Scan again…**). Each project chooses (New project, or Project settings → Characters): whether the main button looks for the characters at all, whether it scans the first 5 chapters it writes or all of them, and whether it waits for you to check the names or **trusts** them. Trusting adds the suggestions as **Add all suggested** does (extras ignored, groups without a name left for the review) and writes the script straight away, so look the Characters tab over afterwards. Write script on its own reminds you instead when the character list is empty or scanned groups are unnamed, with **Write anyway**, which isn't asked again for that project until that changes. Without the scan models the step bar is as before, and the main button's job notes that characters weren't looked for.

- The first time, it offers to download its models (about 586 MB into `data\addons\charscan`, not part of the app): DeepGHS's anime person and head detectors (MIT) and CCIP (OpenRAIL), and SmilingWolf's WD tagger v3 (Apache 2.0). All allow commercial use. Each file is pinned to a version and checked against its checksum. Settings → Character scan models can remove them.
- Pick the chapters: the ticked ones, the first five, or all. It runs as a normal job (Pause and Cancel work) at roughly 0.05 s per panel on a GPU; Chapters 1–3 of a webtoon (320 panels) took about 20 seconds.
- Each group shows its clearest views (not explicit ones, when there are any), how often it appears, suggested traits (hair colour and style, eye colour, glasses, accessories, clothing) and a description built from them. Click a trait to leave it out. Name the group and **Add as new character**, or add it to an existing character, merge it with another group, or ignore it. Adding gives the character the group's four best views as pictures, which Magi uses.
- Expect a character to come back as more than one group now and then (close-ups, back views, dark scenes): "Same as group …?" suggests likely pairs, and merging keeps the bigger group. Characters seen fewer than three times are hidden under "Show small groups". Groups that look like a character you already added say so ("Looks like Seyoung: add to them"), so scanning later chapters picks up where you left off.
- On Chapters 1–3 of a colour webtoon it found the three main characters as clean groups, plus a fourth who first appears in Chapter 3. Body type ("petite", "tall") was never suggested in testing, and clothing rarely, since outfits change between scenes.

**Suggest names** (in the review, or ticked as *Then suggest names* when starting a scan, so finding and naming the characters is one job) asks your script writer (Settings → Script: Claude, Claude Code, another online service, or the local model if it reads pictures) who each group is. With Claude Code, one agent reads the sheets and answers. It's sent the biggest 30 groups still to review, as picture sheets with one labelled row per group, and for each group up to 18 lines read around its appearances: lines with a likely name first (an honorific such as -ssi or -nim, Mr./Ms., "my name is", a profile card), then panels where the group is seen alone, spread over the story. The character list and, once the script is written, the story so far go with them. One request (services that take few pictures get a few), run as a normal job; afterwards the review opens again.

- Each card then shows the suggestion ("Suggested: Choi Ha-eun · also Dr. Lee", or "A guess" when the writer wasn't sure) and has the name, other names, gender and a description of who they are in the story filled in, ahead of the traits. Change anything before adding. A group the writer calls an extra (background people, ads for the group's other series) gets **An extra: ignore**; one it says is another look of a bigger group gets "Same as group …?" and its name; one it matches to a listed character gets "The writer says …: add to them".
- **Add all suggested** adds every suggested card that has a name, as the cards say now, biggest group first, after listing them for you to confirm. Cards with the same name become one character with all their pictures, a name already on the list (or among its other names) adds the group to that character, and the other names are added to the character's own. Extras and cards without a name stay.
- Matching names to groups from the text alone was tried first and wasn't enough: on a 55-chapter test series it found 4 of the 5 main characters and no one else. With Claude Opus 5.5, as if freshly scanned (no characters, no script yet), one request of 30 groups took 65 seconds and named all six main characters (two with their other names: a fake title, an online name), matched 9 smaller groups to them, and called 16 groups extras, including every ad for another series. It disagreed with an earlier hand naming on one big, mixed group, marking its answer a guess.

### Speaker detection with Magi (optional add-on)

Magi v2 (Ragav Sachdeva, Gyungin Shin and Andrew Zisserman, University of Oxford) is a model built to transcribe manga: it finds the speech bubbles and characters on a page and guesses who says each line, naming characters by comparing them with pictures you give it. It is not part of the app; download it from **Settings → Speaker detection** if you want it.

- **License: personal, research, non-commercial and not-for-profit use only**; anything else needs the author's permission. The app asks you to agree before downloading.
- The download is about 4.9 GB with an NVIDIA GPU (PyTorch 2.75 GB, the Magi model 2.06 GB, the rest small), about 2.3 GB for the CPU version, which is slow. It goes into its own Python environment in `data\addons\magi`, separate from the app's (Settings shows its size on disk once installed), and **Remove Magi** deletes it. Package versions are pinned, and so is the exact Magi code release that was reviewed, so an update on Hugging Face can't change what runs.
- Add 2–5 pictures of each character in the **Characters** tab (pictures show in advanced mode): from image files (or drop them on the character), or click any panel, drag a box around the character and **Add picture**.
- With **Find speakers with Magi when reading text** on, **Read text** also runs Magi on each chapter (about a minute for a 105-piece chapter on an RTX 5060 Ti, about 6.6 GB of video memory). Webtoon strips are cut into page-sized chunks in the gaps between panels; where a cut has to go through artwork, neighbouring chunks overlap so speech bubbles stay whole. Its guesses show under each scene and are passed to the script writer, labelled as guesses: on a colour webtoon chapter it named the right speaker for about 6 lines in 10, and it tends to give captions, off-panel speakers and sounds from TVs or videos to whoever is drawn nearby. Continue skips chapters Magi has already read; Redo runs it again (do that after adding pictures). Detecting a chapter's panels again clears its guesses.

## Where things live

`recap\paths.py` decides: an installed copy uses Windows' places (see Install), a portable copy or one run from source keeps everything next to itself, as listed here (`RECAP_DATA` points it all at another folder, for testing). A project is found by the id saved first in its `project.json`, so its folder can be renamed or moved by hand while the app is closed.

- `data\projects\<project name>\` (or the projects folder chosen in Settings) holds `project.json`, `pages\`, `panels\`, `audio\`, `output\`, `characters\` (character pictures), `render\` (each chapter's finished video piece, reused by the next render; safe to delete, at the cost of rendering everything again), `editors\` (each chapter's video for **Export for editors** → Any editor, reused the same way), `textless\` (the pictures without their text, reused the same way), `voiceover\` (your recordings) and `focus.json` (each scene's subject).
- `data\settings.json` holds the app-wide settings, `data\speeds.json` the speeds learned for time estimates, and `data\*_key.txt` the API keys (the script writers' and ElevenLabs').
- `data\addons\magi\`, `data\addons\charscan\`, `data\addons\ocr_en\`, `data\addons\focus\`, `data\addons\boxes\`, `data\addons\vosk\` and `data\addons\gpu\` hold the Magi add-on, the character scan models, the English text model, the subject finder, the box finder, the speech recognizer and GPU support when downloaded; each project's `scan\` holds the last scan's snapshots, and `models\` the voice model.
- `recap\` holds the code: `importer.py` (folder scan and chapter import), `panels.py`, `ocr.py`, `script.py`, `claude.py` and `cloud.py` (the online script writers), `tts.py` and `online_voices.py` (the voices), `voiceover.py` (your own recording), `video.py`, `focus.py` (the scenes' subjects), `textless.py` (the pictures without text), `cover.py` (black boxes and the box finder), `editors.py` (export for video editors), `package.py` (project files), `credits.py` (the scan group's pages), `estimates.py` (time estimates), `magi.py` and `magi_worker.py` (the Magi add-on), `charscan.py` (character scan), `gpu.py` (the NVIDIA GPU, and GPU support), `pipeline.py` (steps and the background worker), `server.py` (local API, 127.0.0.1 only) and `web\` (the interface).

### Shipping the app

The app is shipped without models. `setup.ps1` downloads the Kokoro voice into `models\` (first-run setup can also download it), Ollama models come from Ollama, and Magi, the character scan models, the English text model, the subject finder, the box finder, the speech recognizer and GPU support are downloaded from inside the app. When packaging, leave out `models\`, `data\` (projects, settings, API keys, learned speeds, add-ons) and `.venv\`. A fresh install then opens with the first-run setup.

## GPU

With an NVIDIA graphics card and the GPU build of ONNX Runtime (with NVIDIA's CUDA and cuDNN libraries, about 1.3 GB), text reading and the voice run on the GPU. On an RTX 5060 Ti that measured about 0.28 s per panel for text reading (vs 0.81 s on the CPU) and 0.39 s per narration line for the voice (vs 2.11 s). Settings → Performance turns it off; if the GPU can't be used, both quietly run on the CPU, and the sidebar shows which. AMD and Intel graphics cards aren't used for text reading or the voice (they run on the CPU), but they do encode video (below).

Where the GPU build comes from:

- **From source**, `setup.ps1` installs it into `.venv` when it finds an NVIDIA card (`onnxruntime-gpu[cuda,cudnn]==1.31.0`).
- **The installer and the portable zip** carry the CPU build in their own Python, which an installed copy can't change (Program Files is read-only). **GPU support** is the GPU build as an optional download, offered when there's an NVIDIA card and the copy has only the CPU build: a **Download GPU support** button on first-run setup's CPU line, and a box under Settings → Performance (advanced mode). It's the same wheels `setup.ps1` installs, for the app's Python 3.12: ONNX Runtime GPU 1.31.0, NVIDIA's CUDA 13 libraries (runtime, cuBLAS, cuFFT, cuRAND, NVRTC, nvJitLink) and cuDNN 9.27, 1.33 GB from PyPI's file server, each wheel pinned in `recap\gpu.py` with its size and SHA-256 and checked as it comes in, with progress and Cancel (nothing half-downloaded is kept). They're unpacked into `addons\gpu` (`%LOCALAPPDATA%\Recap Studio\addons\gpu` for an installed copy), 1.9 GB on disk; about 3.2 GB must be free while they unpack. On the RTX 5060 Ti the download and unpacking took under a minute.
- GPU support is used from the next start: the app puts `addons\gpu` first on Python's import path before anything loads ONNX Runtime, so the GPU build is loaded instead of the CPU one, and it loads its CUDA and cuDNN libraries from beside it. Until then Settings, setup and the sidebar say it's downloaded and needs a restart. CUDA 13 needs NVIDIA driver 580 or newer; when the driver is older, Settings and setup say so before you download.
- **Remove GPU support** (Settings → Performance) puts text reading and the voice back on the CPU. Once loaded, its libraries can't be deleted while the app runs, so the app forgets it at once and deletes its files on the next start; removed before a restart, it's deleted straight away.

Tested on a copy of the portable build made to look installed, with its own `%APPDATA%` and `%LOCALAPPDATA%`: before the download both ran on the CPU (a voice sample in about 2.2 s); after it and a restart, both reported GPU (the same sample in 0.33 s); after Remove and a restart, the folder was gone and both were on the CPU again.

### Video export

Two settings under Settings → Performance control how fast video is made:

- **Video encoder**: *Automatic* uses the first GPU encoder that works with your ffmpeg build and graphics card: NVIDIA's (NVENC), AMD's (AMF) or Intel's (Quick Sync), each tried on a few frames first, and the CPU (libx264) otherwise; *CPU only* always uses the CPU. On an RTX 5060 Ti the NVIDIA encoder matched the source slightly more closely than the CPU one, with a ~13% smaller file. AMD's and Intel's run at about the same quality setting (constant quality, no bitrate target), but they haven't been tried on those cards: this was built on a PC with only an NVIDIA card, where it was checked that Automatic picks NVIDIA, and that an AMD or Intel encoder that won't start falls back to the CPU, for the video and the editors' clips. If an encoder fails to start, the export switches to the CPU on its own.
- **Draw video frames in this many processes**: frames are drawn by several processes at once, each taking turns of 30 frames, and handed to one ffmpeg in order, so the video is the same whichever number you pick (tested frame by frame). *Automatic* uses 2 (1 on computers with fewer than 4 cores for the NVIDIA encoder, or 6 for the CPU one). 1 draws everything in the app's own process. If the extra processes can't start, it draws in one.

The job message shows what's being used, e.g. "Rendering video (GPU encoder, 2 drawing processes): 40%", and the Performance section says what Automatic means on your computer.

Measured on an RTX 5060 Ti with 12 CPU threads, exporting two scrolling webtoon chapters (12 minutes of video, 30 fps):

| Export | Frames per second | Speed vs. real time |
|---|---|---|
| 1080p, CPU encoder, before the export was sped up | 113 | 3.8× |
| 1080p, CPU encoder, 1 process | 157 | 5.2× |
| 1080p, CPU encoder, 2 processes | 169 | 5.6× |
| 1080p, NVIDIA encoder, 1 process | 227 | 7.6× |
| 1080p, NVIDIA encoder, 2 processes | 255 | 8.5× |
| 720p, NVIDIA encoder | 471 | 15.8× |
| 480p, NVIDIA encoder | 608 | 20.3× |
| 360p, NVIDIA encoder | 673 | 22.5× |
| 1080p stills (scenes one at a time), 30 fps | 330 | 11.0× |
| 1080p stills, 10 fps | (216) | 21.6× |

So an hour of 1080p video takes about 7 minutes with the NVIDIA encoder, and about 3 at 720p. More than 2 drawing processes, and faster NVENC presets (p1–p4), made no real difference at 1080p: by then the encoder itself is the limit. The NVIDIA encoder runs at preset p5, which gave the closest match to the source and the smallest file of the five presets.

On the GPU, several panels (or narration lines) are worked on at once, each worker with its own copy of the model. Text reading measured about 2x faster with 4 workers (0.14 s per panel) and no faster beyond; the voice gains about 1.2x with 2. Each worker needs roughly 1.5 GB of video memory, so **Automatic** (Settings → Performance) checks how much is free when a job starts and uses fewer workers when, say, Ollama's model is loaded. On the CPU it uses one worker, since extra workers there made reading slower. Results are identical to one-at-a-time, progress is still saved every 20 items in order, and Cancel stops within about a second.

## Limits

- Panel detection is rule-based, not a trained model. It handles clean gutters and webtoon strips well. Borderless or overlapping art may come out as one large panel, which the video still pans across.
- OCR is tuned for English and Chinese text. Other languages may need a different RapidOCR model.
- Without an NVIDIA GPU, the Kokoro voice and text reading run on the CPU: expect a couple of seconds per line or panel. Without any GPU encoder (NVIDIA, AMD or Intel), video export runs at roughly half speed.
- Not tried for real: AMD's and Intel's video encoders (no such card here), Resolve's own transitions in Resolve itself, and Groq, Mistral and ElevenLabs with real keys (stand-in servers only).
