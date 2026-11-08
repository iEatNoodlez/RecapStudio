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

- **A new icon:** an open book, with lines of text on the left page and the play button on the right. The comic you read, as a video you watch.
- **Autopilot 1.0.1** (Full Suite, or Settings → Plugins): it keeps its state whole when the page, its minute's look at the schedule and a run read and write it at the same moment, which could lose the run under way or Post to YouTube's choices for other projects.
- **Recap Studio Server for 64-bit ARM and QNAP.** The server now also comes for a Raspberry Pi 4 or 5, an ARM NAS or an ARM server, as an AppImage and in the same Docker image tags, and as a package for QNAP's App Center. It's a release of its own: [RecapStudio-Cloud](https://github.com/iEatNoodlez/RecapStudio-Cloud#readme). On the desktop nothing else changes.
