<p align="center">
  <img src="docs/images/icon.png" width="96" height="96" alt="Recap Studio's icon: a strip of panels beside a play button">
</p>

<h1 align="center">Recap Studio</h1>

<p align="center">
  <b>Turn comics, manga and webtoons into narrated recap videos, on your own PC.</b>
</p>

<p align="center">
  <a href="https://github.com/iEatNoodlez/RecapStudio/releases/latest"><img alt="Latest release" src="https://img.shields.io/github/v/release/iEatNoodlez/RecapStudio?style=flat-square&color=4f6bed"></a>
  <img alt="For Windows 10 and 11" src="https://img.shields.io/badge/Windows-10%20%7C%2011-4f6bed?style=flat-square">
  <a href="LICENSE"><img alt="License: MIT" src="https://img.shields.io/badge/license-MIT-4f6bed?style=flat-square"></a>
</p>

<p align="center">
  <a href="https://github.com/iEatNoodlez/RecapStudio/releases/latest"><b>Download</b></a> ·
  <a href="#make-a-recap">Make a recap</a> ·
  <a href="#how-it-works">How it works</a> ·
  <a href="#costs-and-privacy">Costs and privacy</a> ·
  <a href="docs/README.md">Documentation</a>
</p>

<p align="center">
  <img src="docs/images/script.png" width="100%" alt="The Script tab of a manga project: each scene's pictures beside its narration, with a player for its voice line and a box to leave it out of the video">
</p>

<p align="center"><sub>The Script tab, with Episode 1 of <i>Give My Regards to Black Jack</i> by Shuho Sato: 56 pages became 61 narrated scenes and an 11-minute video. Sato lets anyone use the series freely (see <a href="#license-and-credits">credits</a>).</sub></p>

Add the chapters, press **Make the recap**, and Recap Studio cuts the pages into panels, reads the text, works out who's who, writes the narration, voices it and renders the video, with subtitles, YouTube chapters and a suggested title and thumbnail. No account, credits or watermark.

## Download

For Windows 10 or 11 (64-bit), from the [Releases](https://github.com/iEatNoodlez/RecapStudio/releases) page:

- **`Recap-Studio-<version>-setup.exe`** installs it, for everyone on the PC or just for you.
- **`Recap-Studio-<version>-portable.zip`**: unzip it anywhere you can write to, on a short path, and run `Recap Studio.exe`.

Everything it needs is included. The first start downloads the voice model (354 MB) and walks you through a short setup. After that, the app checks for updates when it starts and installs them with one click.

## Make a recap

### 1. Create a project

Type a name at the top left and press **Create**, then answer a few questions: what kind of comic it is, how to find its characters, how the video looks, and the YouTube options.

<p align="center">
  <img src="docs/images/new-project.png" width="80%" alt="The New project dialog asking what kind of comic it is: Webtoon, Manga (picked), Comic or Not sure">
</p>

### 2. Add the chapters and press Make the recap

Press **Add chapters** and pick the series folder. Each subfolder of images, and each `.cbz` or `.zip`, becomes a chapter. You can also drop files on the window. **Make the recap** then runs every step on every chapter, and next time it carries on where it stopped. **Advanced mode** (bottom of the sidebar) shows every step and setting.

### 3. Check who's who

The character scan groups everyone who appears, and your script writer suggests their names from the story. Add them with a click, or let it add its own suggestions without asking.

<p align="center">
  <img src="docs/images/characters.png" width="80%" alt="Characters found: a group of 26 appearances of an older man in glasses, with the suggestion Director and a description, and buttons to add him, merge him or ignore him">
</p>

<p align="center"><sub>The hospital director appears 26 times but is never named in Episode 1, so he's left for you to name.</sub></p>

### 4. Read the script

The **Script** tab (at the top of this page) shows each scene's pictures, narration and voice line. Edit any line, play it, or leave a scene out.

### 5. Watch the video and take the title

The **Video** tab plays the recap. The project's `output` folder has `recap.mp4`, with its subtitles, the narration and a YouTube chapter list.

<p align="center">
  <img src="docs/images/video.png" width="100%" alt="The Video tab playing the finished recap at 2:30 of 11:13, on a page where Saito introduces himself at the hospital">
</p>

Your script writer also suggests a clickbait title, a few others, and a thumbnail with labels and arrows on the characters, a "?!" and a speech bubble. Change the words or the pictures and it's drawn again, or press **Redraw with AI** to have an image model repaint it.

<p align="center">
  <img src="docs/images/thumbnail.jpg" width="80%" alt="A thumbnail of two manga panels side by side: Saito labelled MC with an arrow and a ?!, and the night-shift doctor labelled DRUNK GENIUS with an arrow and the speech bubble SLEEP WHEN YA CAN.">
</p>

<p align="center"><sub>Suggested with the title <i>I Made 2x My Salary in ONE Night… Then They Left Me Alone With a Dying Patient | Manga Recap</i>, and three others to pick from.</sub></p>

## How it works

| Step | What it does | With |
|---|---|---|
| Panels | Cuts the pages into panels. Webtoons are stitched into one strip and cut at the gutters. | OpenCV |
| Text | Reads the speech bubbles and captions. | RapidOCR |
| Characters | Finds who's who and suggests their names. | DeepGHS's detectors and CCIP, the WD tagger (optional download) |
| Script | Writes the narration a chapter at a time, from the pictures and the text. | A local model (Ollama), Claude Code on your Claude plan, or Claude, Gemini, OpenRouter, DeepSeek, Groq or Mistral with your own key |
| Voice | Reads the narration aloud, or uses your own recording. | Kokoro on your PC, Microsoft Edge's voices, or ElevenLabs |
| Title | Suggests a clickbait title and draws a labelled thumbnail, which an image model can repaint. | Your script writer; Gemini or OpenRouter image models |
| Video | Scrolls webtoons, pans and zooms other comics, crossfades, and writes subtitles. | OpenCV and ffmpeg |

## Costs and privacy

Everything runs on your PC unless you choose an online service. A local model with Ollama and the Kokoro voice cost nothing. Online script writers, voices and the thumbnail redraw use your own API key, which is stored only on your PC, and that service bills you; with Claude, a chapter costs about 1¢ to 40¢ depending on the model. Claude Code uses your Claude subscription instead of API credit.

## Good to know

- **Speed**: with an NVIDIA card, text reading and the voice run several times faster (GPU support, a 1.3 GB download, offered in setup). An NVIDIA, AMD or Intel card also speeds up video encoding.
- **Space**: projects are saved in `Documents\Recap Studio`, one folder each. A recap takes several GB.
- **YouTube-safe**: for an age-restricted upload of an adult comic, this tones down sex scenes and covers nudity with black boxes. It can make mistakes, so check the video before you upload.

## Documentation

The [docs](docs/README.md) cover every feature in detail:

- [Install, files and updates](docs/install.md)
- [Using Recap Studio](docs/using.md)
- [Script writers and the prompt](docs/script-writers.md)
- [Characters](docs/characters.md)
- [Voice](docs/voice.md)
- [Video](docs/video.md)
- [YouTube title, thumbnail and YouTube-safe](docs/youtube.md)
- [The scan group's pages](docs/scan-group-pages.md)
- [Performance and the GPU](docs/performance.md)

## Run from source

With Python 3.10 or newer:

```powershell
powershell -ExecutionPolicy Bypass -File setup.ps1
```

Then double-click `run.bat`. Building the installer is covered in [Install, files and updates](docs/install.md#building-the-installer).

## License and credits

MIT. The models it downloads have their own licenses, listed where the app offers them. Magi is for non-commercial use only.

The screenshots show Episode 1 of *Give My Regards to Black Jack* (ブラックジャックによろしく) by Shuho Sato, from Denshobato's English edition. Sato lets anyone copy, adapt and publish the series, commercially or not, without asking ([his report on the terms](https://note.com/shuho_sato/n/neda36a41a7b3), in Japanese). The narration, voice, title and thumbnail in them were made by Recap Studio, with Claude Code writing.
