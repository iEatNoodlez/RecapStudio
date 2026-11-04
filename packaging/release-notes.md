**Which file?** Recap Studio comes in two editions, both free. They keep the same projects and settings, and installing one over the other switches between them. Each copy updates to its own edition.

- **Recap Studio Lite**: `Recap-Studio-Lite-{version}-setup.exe` (installs it) or `Recap-Studio-Lite-{version}-portable.zip`. The whole app, with no plugins. You can add single plugins in Settings → Plugins.
- **Recap Studio Full Suite**: `Recap-Studio-Full-Setup-{version}.exe` or `Recap-Studio-Full-Portable-{version}.zip`. Lite with [Recap Studio's plugins](https://github.com/iEatNoodlez/RecapStudio-Plugins#readme) built in: finding the series, automation, posting to YouTube, branding, more languages and file types.

Copies on 0.1.x update to Lite. To move to Full Suite, install its setup over Lite once.

## What's new

- **Search all your sources at once:** Get chapters from a site → Search has **All your sources**, with the results grouped by source.
- **The Trending finder looks for the chapters too** (Trending finder 1.1.0, a plugin: built into Full Suite; in Lite, Settings → Plugins). After **Make a project**, it searches all your own sources for the series. When one of them has a series by that exact title, its chapters open ready to get. **Settings → Trending finder** can have them got straight away instead, or not looked for. The chapters come only from sources you added yourself in Settings → Sources.
- **Reading Spanish comics 1.0.2:** its **Remove** deletes only the downloaded reader and leaves the plugin installed.
- **For plugin makers:** `RecapStudio.getChapters({titles, auto})` opens Get chapters from a site for the open project, searching all of the member's sources for a series.
