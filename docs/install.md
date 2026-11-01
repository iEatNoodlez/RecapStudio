# Install, files and updates

## Download and install

Download from the [Releases](https://github.com/iEatNoodlez/RecapStudio/releases) page (Windows 10 or 11, 64-bit):

- **`Recap-Studio-<version>-setup.exe`**: installs into `C:\Program Files\Recap Studio` for everyone on the PC (Windows asks for admin rights, and again for each update), or, if you pick **Install for me only** at the start, into `%LOCALAPPDATA%\Programs\Recap Studio` without them. It adds a Start menu shortcut (and a desktop one if you tick it). Installing a newer version over it keeps the way it was installed, and your projects and settings.
- **`Recap-Studio-<version>-portable.zip`**: unzip it anywhere you can write to (not Program Files) whose path is under about 90 characters (Windows' 260-character limit on file paths, with the app's deepest files and room to unpack an update), and run `Recap Studio.exe`.

Both carry their own Python with every package, so there's nothing else to install. The first run downloads the voice model (354 MB). Where your files go (uninstalling and updating leave all of them):

- **Projects**: one folder per project, named after it, in `Documents\Recap Studio`. **Settings → Projects folder** moves them anywhere (another drive, say), and **⋯ → Show in folder** opens a project's folder. First-run setup shows the folder, and warns when OneDrive or another cloud service syncs it: a recap runs to several GB.
- **Settings, API keys and logs**: `%APPDATA%\Recap Studio`.
- **The voice model and the add-ons** (GPU support among them): `%LOCALAPPDATA%\Recap Studio` (gigabytes, so kept out of roaming profiles).
- **The portable copy** keeps all of it next to itself instead: `data` (with `data\projects`) and `models`.

Copies installed by 0.1.0 kept everything next to the app; the first start after updating moves it to these places. The window uses WebView2, which comes with Windows 11 and current Windows 10. The packaged app carries the CPU build of ONNX Runtime, the engine text reading and the voice run on; with an NVIDIA card, **GPU support** (offered in first-run setup and Settings → Performance) downloads the GPU build, 1.3 GB, so they run on the card instead (see [GPU](performance.md#gpu)).

**Updates**: once per start the app asks GitHub whether a newer release is out (one request to GitHub's API, with nothing about you in it; Settings → Updates turns it off, or checks now). When there is one, **⬆ Update to x.y.z** appears at the bottom of the sidebar. It shows what's new, then **Update now** downloads the release's file, checks it against the SHA-256 GitHub lists for it, closes the app, installs it and opens it again: an installed copy runs the new setup, and a portable copy swaps in the new program files, putting the old ones back if anything goes wrong. Your projects, settings and models are never touched. It waits while a job is running. **Skip this version** hides the button until the next release. A copy run from source isn't checked: `git pull` updates it.

## First-run setup

The first time the app opens, a short setup walks through everything you'd otherwise look for in Settings, saving each choice as you make it:

1. **This computer**: where projects are saved (with **Change…**), and whether the voice model, ffmpeg, an NVIDIA GPU and a GPU video encoder (NVIDIA, AMD or Intel) are there. If the voice model is missing (setup.ps1 skipped or failed), it downloads it (354 MB from the kokoro-onnx project's GitHub release, checked against its size and SHA-256). The installed app reads text and voices lines on the CPU even with an NVIDIA card (it carries the CPU build of ONNX Runtime); the card still encodes the videos, and setup says which is which. With a card, the CPU line has a **Download GPU support** button (1.3 GB, see [GPU](performance.md#gpu)), with Cancel; it's used from the next start.
2. **Script writer**: Claude, Claude Code (your Claude plan, with its sign-in), OpenRouter, Google Gemini, DeepSeek, Groq, Mistral or a local model, with the API key and model for the one you pick (or how to install Ollama).
3. **Voice**: pick a Kokoro voice and speed, and play a sample (Settings → Voice also has Microsoft Edge's online voices and ElevenLabs).
4. **Video encoding**: the GPU's encoder or the CPU, with how long an hour of 1080p video takes on this computer. How each video looks is chosen per project, when it's created.
5. **Optional downloads**: the English text-reading model (ticked; 9 MB), the character scan models (586 MB) and Magi (4.9 GB, after agreeing to its license). They download in the background.
6. A summary.

**Skip setup** closes it for good; **Settings → Run setup again** brings it back any time.

## Where things live

`recap\paths.py` decides: an installed copy uses Windows' places (see [Download and install](#download-and-install)), a portable copy or one run from source keeps everything next to itself, as listed here (`RECAP_DATA` points it all at another folder, for testing). A project is found by the id saved first in its `project.json`, so its folder can be renamed or moved by hand while the app is closed.

- `data\projects\<project name>\` (or the projects folder chosen in Settings) holds `project.json`, `pages\`, `panels\`, `audio\`, `output\`, `characters\` (character pictures), `render\` (each chapter's finished video piece, reused by the next render; safe to delete, at the cost of rendering everything again), `editors\` (each chapter's video for **Export for editors** → Any editor, reused the same way), `textless\` (the pictures without their text, reused the same way), `voiceover\` (your recordings) and `focus.json` (each scene's subject).
- `data\settings.json` holds the app-wide settings, `data\speeds.json` the speeds learned for time estimates, and `data\*_key.txt` the API keys (the script writers' and ElevenLabs'). `data\plugins\` holds the source plugins you added (each with the log of what it printed), and `data\suwayomi_login.json` the Suwayomi server's login, when it has one.
- `data\addons\magi\`, `data\addons\charscan\`, `data\addons\ocr_en\`, `data\addons\focus\`, `data\addons\boxes\`, `data\addons\bubbles\`, `data\addons\vosk\` and `data\addons\gpu\` hold the Magi add-on, the character scan models, the English text model, the subject finder, the box finder, the bubble finder, the speech recognizer and GPU support when downloaded; each project's `scan\` holds the last scan's snapshots, and `models\` the voice model.
- `recap\` holds the code: `importer.py` (folder scan and chapter import), `sources.py` and `plugin_host.py` (getting chapters from sites: plugins, gallery-dl and Suwayomi), `panels.py` and `bubbles.py` (cutting pages and webtoon strips, and the bubble finder), `ocr.py`, `script.py`, `claude.py` and `cloud.py` (the online script writers), `tts.py` and `online_voices.py` (the voices), `voiceover.py` (your own recording), `video.py`, `focus.py` (the scenes' subjects), `textless.py` (the pictures without text), `cover.py` (black boxes and the box finder), `editors.py` (export for video editors), `package.py` (project files), `credits.py` (the scan group's pages), `estimates.py` (time estimates), `magi.py` and `magi_worker.py` (the Magi add-on), `charscan.py` (character scan), `gpu.py` (the NVIDIA GPU, and GPU support), `pipeline.py` (steps and the background worker), `server.py` (local API, 127.0.0.1 only) and `web\` (the interface).

## Run from source

### Setup (once)

Needs Python 3.10+. For the free local script writer, also Ollama with at least one model pulled (for example `ollama pull gemma3`); the online writers only need an API key.

```powershell
powershell -ExecutionPolicy Bypass -File setup.ps1
```

This creates `.venv`, installs packages, and downloads the Kokoro voice model (about 354 MB) into `models\`.

### Run

Double-click `run.bat`. It opens a desktop window (WebView2, the same engine RecapSynth uses).

To use your normal browser instead:

```powershell
.venv\Scripts\python -m recap --browser
```

### Building the installer

`packaging\build.ps1 -Version 1.0.0` builds `dist\Recap Studio\` (the app with its own Python from [python-build-standalone](https://github.com/astral-sh/python-build-standalone), the packages in `requirements.txt` plus imageio-ffmpeg, and `Recap Studio.exe`, a small launcher compiled from `packaging\launcher`), zips it as the portable download, and, when [Inno Setup 6](https://jrsoftware.org/isinfo.php) is installed, compiles `packaging\installer.iss` into the setup. Pushing a tag such as `v1.0.0` to GitHub does the same in GitHub Actions (`.github\workflows\build.yml`) and attaches both to a release; running that workflow by hand keeps them as the run's artifacts instead. The app's update check (`recap/updates.py`) reads the latest release of `iEatNoodlez/RecapStudio` and picks its file by name (`-setup.exe` for installed copies, `-portable.zip` for portable ones), so keep those names. Tags are versions (`v1.2.0`, or `v1.3.0-beta.1` for a pre-release, which GitHub's "latest" leaves out).

## Shipping the app

The app is shipped without models. `setup.ps1` downloads the Kokoro voice into `models\` (first-run setup can also download it), Ollama models come from Ollama, and Magi, the character scan models, the English text model, the subject finder, the box finder, the bubble finder, the speech recognizer and GPU support are downloaded from inside the app. When packaging, leave out `models\`, `data\` (projects, settings, API keys, learned speeds, add-ons) and `.venv\`. A fresh install then opens with the first-run setup.
