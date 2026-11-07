**Which file?** Recap Studio comes in two editions, both free, for Windows and now for Linux. They keep the same projects and settings, and installing one over the other switches between them. Each copy updates to its own edition.

- **Recap Studio Lite**: the whole app, with no plugins. You can add single plugins in Settings → Plugins.
  - Windows: `Recap-Studio-Lite-{version}-setup.exe` (installs it) or `Recap-Studio-Lite-{version}-portable.zip`.
  - Linux: `Recap-Studio-Lite-{version}-x86_64.AppImage`.
- **Recap Studio Full Suite**: Lite with [Recap Studio's plugins](https://github.com/iEatNoodlez/RecapStudio-Plugins#readme) built in: finding the series, what's trending to make a project of, automation, posting to YouTube, branding, passing a site's Cloudflare check yourself in a window, more languages and file types.
  - Windows: `Recap-Studio-Full-Setup-{version}.exe` or `Recap-Studio-Full-Portable-{version}.zip`.
  - Linux: `Recap-Studio-Full-{version}-x86_64.AppImage`.

Copies on 0.1.x update to Lite. To move to Full Suite, install its setup over Lite once.

**On Linux:** an AppImage runs on Ubuntu 22.04, Debian 12, Fedora 36 or newer (64-bit). Make it executable (`chmod +x`, or Properties → Allow executing), then start it. [Linux](https://github.com/iEatNoodlez/RecapStudio/blob/main/docs/linux.md) in the docs covers where files go, fonts, video encoding and Ubuntu 24.04's sandbox.

## What's new

- **The video is named after the project.** `Moonlit Bakery.mp4`, and `Moonlit Bakery - Chapter 12 to Chapter 14.mp4` for a recap of some of its chapters, instead of `recap.mp4`; its subtitles, narration file and YouTube chapter list take the same name. Videos made before keep theirs.
- **Full Suite gains two plugins.** [Autopilot](https://github.com/iEatNoodlez/RecapStudio/blob/main/docs/plugins.md#autopilot) sits back for you: on the days and at the time you choose, it reads a chart of your own sources (Popular, Trending), takes the series still coming out, gets their newest chapters into projects and makes the recaps, one after another, within a budget of chapters; a dry run shows what it would do, and with Post to YouTube connected the videos go up by themselves. [Advanced Cutting](https://github.com/iEatNoodlez/RecapStudio/blob/main/docs/plugins.md#advanced-cutting) cuts a finished video into pieces by size, chapters or length, each from a chapter's start with its own subtitles and chapter list, without encoding anything again (a 25-hour video in a few minutes). Lite installs either from Settings → Plugins.
- **Recap Studio Server** now runs on any 64-bit Linux, old or new (it brings its own C library), and comes as a Docker image for a NAS's container app. It's a release of its own: [RecapStudio-Cloud](https://github.com/iEatNoodlez/RecapStudio-Cloud#readme). On the desktop nothing changes.
