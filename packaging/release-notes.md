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

- **Videos render several times faster.** Two things held the render back: the drawing processes took turns instead of drawing at the same time, and the page asking for a big project every 1.5 seconds starved the hand-off to ffmpeg. A 720p webtoon chapter that took 5 minutes on a project of 200 chapters now takes well under one. A render you stopped carries on from the chapters already made.
- **The app stays light while a job runs.** The page now asks only for the job's progress, and for the project itself only when something in it changed, so a long project no longer makes the app sluggish.
- **Recap Studio Server:** the same app with no window, on a Linux machine you reach from a browser (a NAS, a mini PC), behind a login, installed as a service by one script. It's a release of its own: [RecapStudio-Cloud](https://github.com/iEatNoodlez/RecapStudio-Cloud#readme). On the desktop nothing changes.
- **Download the video** from the Video tab, beside Show in folder.
- **Full Suite's plugins:** Post to YouTube 1.1.0, When a video is done 1.1.1 and Site check 1.0.1 (what they do on a server; the desktop is as before).
