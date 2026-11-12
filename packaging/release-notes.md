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

- **Voice and video start while the script is being written.** With Claude Code, Claude or a cloud service writing the script, Make the recap voices each chapter as soon as it's written, then renders its video, so Voice and Render video only have what's left at the end. The job's line shows what's done ahead ("Working ahead: rendering Chapter 4 (40%); 3 chapters voiced, 2 rendered"). With the local writer (Ollama) the steps still run one after another, since it needs your graphics card itself.
- **The narrator's persona in New project.** Who the narrator is and how they sound, set as you make the project. It starts from your last project's, with **Use the built-in persona** to go back to the app's own; the rules and style stay at Script and voice.
- **Fixed:** picking a project or the Library in the rail while Settings was open left Settings on top. Cancelling Voice could lose up to 19 lines it had already made; now every line made is kept.
- **The plugins:** Overnight queue 1.1.1: its panel on the home page draws the first time (it stayed empty until the library refreshed).
