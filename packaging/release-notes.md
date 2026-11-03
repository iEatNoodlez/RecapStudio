**Which file?** Recap Studio comes in two editions, both free. They keep the same projects and settings, and installing one over the other switches between them. Each copy updates to its own edition.

- **Recap Studio Lite**: `Recap-Studio-Lite-{version}-setup.exe` (installs it) or `Recap-Studio-Lite-{version}-portable.zip`. The whole app, with no plugins. You can add single plugins in Settings → Plugins.
- **Recap Studio Full Suite**: `Recap-Studio-Full-Setup-{version}.exe` or `Recap-Studio-Full-Portable-{version}.zip`. Lite with [Recap Studio's plugins](https://github.com/iEatNoodlez/RecapStudio-Plugins#readme) built in: finding the series, automation, posting to YouTube, branding, more languages and file types.

Copies on 0.1.x update to Lite. To move to Full Suite, install its setup over Lite once.

## What's new

- **Plugins** (Settings → Plugins): install plugins from Recap Studio's own list, switch them on and off, update or remove them, then **Restart now** to use the changes. Plugins can add steps, settings, buttons, languages and file types. What each one does: https://github.com/iEatNoodlez/RecapStudio/blob/main/docs/plugins.md
- **Full Suite's plugins**:
  - Find the series;
  - New chapters on a schedule;
  - the Overnight queue;
  - the Watched folder;
  - When a video is done (copy it, run a command, tell your phone, Discord or Slack);
  - Post to YouTube;
  - Branding;
  - Recaps in Spanish;
  - Reading Spanish comics;
  - EPUB comics.

  The series sources Find the series searches aren't built in yet: add AniList, Kitsu or MangaUpdates in Settings → Plugins.
- **The edition in the window's title**, the sidebar and Settings → Updates, for example "Recap Studio Lite {version}". Each copy updates to its own edition's file.
- **Text read the right way up:** English and Korean lines are no longer turned over and read backwards ("NO" read as "ON"). Chapters already read keep their old text until Read text is run again with Redo.
- **Tall webtoon pieces:** a line that crosses the edge between two reading chunks is now read once and in full.
- **Claude Code as the script writer** no longer fails on translated Korean, Chinese and Japanese comics.
- **Make the recap's last note** keeps the earlier steps' notes, and the scan group's pages are noted once.
- **Scan group pages in Spanish** (traducción, reclutamiento and the like) are found too.
- **YouTube titles** that run past YouTube's 100 characters lose their "| Manga Recap" ending, or are cut after a whole word, instead of ending partway through a word ("| Resumen de m").
- **Classic captions:** the word being said no longer runs into the words beside it, and a long word at the edge of a line is no longer clipped.
- **Pause during the last chapter** now holds the render before the chapters are joined, instead of carrying on to the end. The Overnight queue's stop time pauses this way too.
