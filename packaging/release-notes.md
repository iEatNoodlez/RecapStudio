**Which file?** Recap Studio comes in two editions, both free, for Windows and now for Linux. They keep the same projects and settings, and installing one over the other switches between them. Each copy updates to its own edition.

- **Recap Studio Lite**: the whole app, with no plugins. You can add single plugins in Settings → Manage plugins.
  - Windows: `Recap-Studio-Lite-{version}-setup.exe` (installs it) or `Recap-Studio-Lite-{version}-portable.zip`.
  - Linux: `Recap-Studio-Lite-{version}-x86_64.AppImage`.
- **Recap Studio Full Suite**: Lite with [Recap Studio's plugins](https://github.com/iEatNoodlez/RecapStudio-Plugins#readme) built in: finding the series, what's trending to make a project of, automation, posting to YouTube, branding, passing a site's Cloudflare check yourself in a window, more languages and file types.
  - Windows: `Recap-Studio-Full-Setup-{version}.exe` or `Recap-Studio-Full-Portable-{version}.zip`.
  - Linux: `Recap-Studio-Full-{version}-x86_64.AppImage`.

Copies on 0.1.x update to Lite. To move to Full Suite, install its setup over Lite once.

**On Linux:** an AppImage runs on Ubuntu 22.04, Debian 12, Fedora 36 or newer (64-bit). Make it executable (`chmod +x`, or Properties → Allow executing), then start it. [Linux](https://github.com/iEatNoodlez/RecapStudio/blob/main/docs/linux.md) in the docs covers where files go, fonts, video encoding and Ubuntu 24.04's sandbox.

## What's new

- **Sources that list a chapter twice.** Some sites list the same chapter more than once: uploaded again, or the whole chapter beside its halves. A source plugin can now say its site does (`CHECK_COPIES`). After Get chapters, Recap Studio then compares the new chapters' pictures with the chapters numbered near them. A new chapter whose pages are already in another one is taken out again and isn't offered again, and the job's note names it. Side stories with pages of their own stay, and other sources are left as they are.
- **Drop folders and files.** A folder dropped on a project becomes chapters, one per folder however deep, named and placed as Import a folder does. On the library, a `.recap` file opens, and anything else starts New project named after it; the files go in once it's made.
- **A project waiting its turn says Waiting…** in the library and the rail, not "Working".
- **The docs and the app's own words match the 0.5 layout:** the rail, the stages, Settings as a page and the one-page New project, with ten new screenshots of the Black Jack sample. Hints, job notes and errors that still named the old tabs now name the stages.
- **The narration skill for Claude Code** tells a nervous but willing scene (shyness, first-time nerves, "are you sure?") as willing, not as pressured.
- **The plugins:** Advanced Cutting 1.1.1, Autopilot 1.1.1, Find the series 1.2.3 and Trending finder 1.1.3 name the stages and Settings → Manage plugins.
