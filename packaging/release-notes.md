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

- **Fixed: whole chapters left out as a notice.** A short chapter whose characters say "notice" and "sorry" could be taken for a "this chapter is only a notice" page (a hiatus, a break) and left out entirely. Now it takes a notice's own words ("hiatus", "postponed", "readers", "sorry for the delay", "no chapter this week"…). A chapter with every piece left out now says **All left out** in the chapter list, not "Not started"; The scan group's pages → **Check what was left out…** puts it back.
- **Fixed:** the chapter list's headings (Pages, Scenes, Script, Voice) sat to the right of their columns.
