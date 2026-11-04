**Which file?** Recap Studio comes in two editions, both free. They keep the same projects and settings, and installing one over the other switches between them. Each copy updates to its own edition.

- **Recap Studio Lite**: `Recap-Studio-Lite-{version}-setup.exe` (installs it) or `Recap-Studio-Lite-{version}-portable.zip`. The whole app, with no plugins. You can add single plugins in Settings → Plugins.
- **Recap Studio Full Suite**: `Recap-Studio-Full-Setup-{version}.exe` or `Recap-Studio-Full-Portable-{version}.zip`. Lite with [Recap Studio's plugins](https://github.com/iEatNoodlez/RecapStudio-Plugins#readme) built in: finding the series, automation, posting to YouTube, branding, more languages and file types.

Copies on 0.1.x update to Lite. To move to Full Suite, install its setup over Lite once.

## What's new

- **Site check: pass a site's Cloudflare check yourself** (a new plugin: built into Full Suite; in Lite, Settings → Plugins). Some sites answer your sources with Cloudflare's "Just a moment…" check instead of their pages. Get chapters now says so, and Site check offers **Open the site**: a small window where you pass the check yourself (wait while it runs, or tick its box). Your own source plugins then reach that site with your pass until it runs out. **Settings → Site check** lists the passes and the sites that have asked. The window only opens when you ask, and nothing in it is done for you. Some sites also look at how the connection itself looks, and those still refuse: Site check says so when one does.
- **For plugin makers:** a source plugin's requests (through `requests`) that get Cloudflare's check are noticed by the app, and carry a site's pass once the member has one, with nothing to change in the plugin. Feature plugins get `plugin.site_pass(...)` and the `site_check` and `site-check` events.
