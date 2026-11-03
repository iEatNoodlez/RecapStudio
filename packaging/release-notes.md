**Which file?** Recap Studio comes in two editions, both free. They keep the same projects and settings, and installing one over the other switches between them. Each copy updates to its own edition.

- **Recap Studio Lite**: `Recap-Studio-Lite-{version}-setup.exe` (installs it) or `Recap-Studio-Lite-{version}-portable.zip`. The whole app, with no plugins. You can add single plugins in Settings → Plugins.
- **Recap Studio Full Suite**: `Recap-Studio-Full-Setup-{version}.exe` or `Recap-Studio-Full-Portable-{version}.zip`. Lite with [Recap Studio's plugins](https://github.com/iEatNoodlez/RecapStudio-Plugins#readme) built in: finding the series, automation, posting to YouTube, branding, more languages and file types.

Copies on 0.1.x update to Lite. To move to Full Suite, install its setup over Lite once.

## What's new

- **The Trending finder** (a plugin: built into Full Suite; in Lite, Settings → Plugins → Show the plugins). **Trending…** in the sidebar lists what's trending on AniList and what's looked at most on MangaUpdates this week, month or year. Narrow it by kind (manga, manhwa, manhua), genre and, on AniList, whether it's finished. Series you already have a project of are marked.
- **Make a project from it:** opens New project named after the series and set to its kind. With Find the series and the AniList source installed, the series is already picked there, so its characters are on the Characters page.
- **For plugin makers:** `RecapStudio.newProject({name, kind, series})` opens New project, and `new-project-open` passes the `series` on.
