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

- **A new look, everywhere.** A rail with your library and the plugins' automation; a project as five stages (Chapters, Characters, Script and voice, Video, Publish) with one Make the recap button and the stage's settings beside it; the script as an editor with the scenes down the left and one open at a time (rewrite a scene or the chapter, read the chapter as one text, and scrolling past a scene's end rolls on to the next); New project on one page, with what you get beside it; Settings as a page with a list of sections and Find a setting; the chapters as a table; the phone page restyled. Advanced mode is gone: **One step ▾** beside the main button runs a single step, the chapters' ticks are always there, a row's tools show when you point at it, and the finer settings sit under Details.
- **Light and dark.** Settings → Look follows the system or picks one; the scrollbars and controls follow too.
- **The plugins look like the app.** Every plugin's windows and Settings sections were rebuilt on the app's own cards, status chips, pills and lists. The home page shows Autopilot's next flight and the Overnight queue's list; the rail's Automation items say where they are (the next flight's time, how many are waiting, on or off); Publish has **Where it goes**, with the posting services' buttons.
- **Post to TikTok 1.0.0** (new, Full Suite or Settings → Plugins): posts a project's video, or its parts, Shorts or Advanced Cutting's pieces as a numbered series joined by one hashtag, when the video is made or from Publish. Needs a TikTok developer app of your own; until TikTok audits it, only you see what it posts.
- **A character the story hasn't named yet** can be added under their role from the scan's review ("the Director"); the narrator calls them that and never makes a name up, and a later chapter's name renames them.
- **Fixed:** Cut the video's window could refuse to close over a number it didn't like, and looked a mess; the keyboard's Back key stranded the window on "Starting Recap Studio…"; the library's line said "title ready" (now "title and thumbnail ready", or "posted to YouTube and TikTok" once a plugin has put the video up).
- **The plugins:** Autopilot 1.1.0, Overnight queue 1.1.0, Watched folder 1.1.0, New chapters on a schedule 1.1.0, When a video is done 1.2.0, Branding 1.1.0, Advanced Cutting 1.1.0, Post to YouTube 1.2.0, Site check 1.1.0, Trending finder 1.1.2, Find the series 1.2.2, Reading Spanish comics 1.0.3, Recaps in Spanish 1.0.2. Ten of them now save their state whole, so a read at the same moment never loses a run or a choice. For plugin makers: `addLibraryPanel`, rail chips, `openSettings`, the `"publish"` place and the kit's classes, in docs/plugins.md.
- **Example Sources:** [RecapStudio-Sources](https://github.com/iEatNoodlez/RecapStudio-Sources) has every site checked and more sites added (its README lists them). It stays apart from the app.
