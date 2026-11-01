# Getting chapters from sites

Recap Studio comes with no scrapers and no links to any site. Members bring their own source. It can be a plugin (a Python file for one site), [gallery-dl](#gallery-dl), or a [Suwayomi](#suwayomi) server running Mihon/Tachiyomi extensions. With one set up, a project's **Chapters** tab has **Get chapters from a site…**, and a project made that way can **Check for new chapters**.

> A plugin is a program. It runs on your PC with your permissions, so it can read and change your files. Add only plugins from people you trust, and only get chapters you're allowed to.

## Setting up a source

Everything is in **Settings → Sources**:

- **Add plugin file…** takes a plugin's `.py` file and copies it into `data\plugins`. The app reads its name, sites and lists from the file without running it, and refuses a file that isn't a plugin, saying why. A plugin with the same file name is replaced. Each plugin can be switched off or removed, and what it prints goes to `data\plugins\<plugin>.log`.
- **Plugin lists**: paste a link to a list someone shares (an `index.json`, see [below](#plugin-lists)) and **Add list**. **Plugins…** shows what's on it, with **Install** and, when the list has a newer version than yours, **Update**. Each file is checked against the list's checksum before it's installed.
- **gallery-dl**: found on PATH, or give its program's path.
- **Suwayomi**: the server's address (`http://127.0.0.1:4567` by default). Its installed sources show up in Get chapters.
- **Check projects for new chapters when the app starts**: each project made from a source is checked, and the sidebar shows how many new chapters it has.

## Getting chapters

**Get chapters from a site…** (the Chapters tab) finds a series three ways:

- **Link**: paste a series' page. The plugin for that site opens it; gallery-dl takes any other link when it's installed.
- **Search**: search one source by title (plugins that have search, and Suwayomi's sources).
- **Lists**: a source's lists, a page at a time with **More**: Popular and Latest for Suwayomi's sources, and whatever lists a plugin offers (trending, new, completed…).

Picking a series shows its chapters, first chapter first, each ticked unless the project already has it. **Get chapters** runs as a job (with progress and **Cancel**, like the other steps). Each chapter's pictures are downloaded into the project's `pages` folder, four at a time and retried when the site is busy. Pictures that aren't JPEG, PNG or WebP are saved as PNG. A chapter is added only once all its pages are in, placed by its number unless you choose otherwise, and the project takes the series' title if it had none. Cancelled or stopped by an error, run it again: chapters already in are passed over.

The project remembers the series. **Check for new chapters** (above the chapter list) lists the chapters the site has that the project doesn't, with **Don't offer the unticked chapters again** for ones you don't want.

## Writing a plugin

A plugin is one Python file. [plugin-example.py](plugin-example.py) is a complete one for a made-up site, to copy from. It runs on the app's own Python 3.12 with the standard library and `requests`; nothing else is installed for it. Files in `data\plugins` whose names start with `_` aren't plugins: plugins can share helpers through them.

The settings are plain values at the top of the file, read without running it:

| Setting | What it is |
|---|---|
| `RECAP_PLUGIN = 1` | Required: the plugin interface it's written for. |
| `NAME` | Required: the name the app shows. |
| `VERSION` | Its version, compared with a plugin list's to offer updates. |
| `SITES` | The sites whose links it opens, like `["example.com"]` (subdomains count). |
| `LANGUAGE`, `NSFW` | Shown next to its name. |
| `LISTS` | The lists `browse()` gives, like `[{"id": "popular", "name": "Popular"}]`. |
| `HEADERS` | Sent with every picture the app downloads for it (and covers), like `{"Referer": "https://example.com/"}`. |
| `DELAY` | Seconds to wait between pictures, for sites that limit how fast you ask: pictures then come one at a time. |

The functions get and return plain data (dicts, lists, text, numbers). Raise an exception with a readable message when something's wrong: the app shows it.

| Function | Gets | Returns |
|---|---|---|
| `series(url)` (required) | A series' link (from a pasted link or a search or list result). | `{"title", "cover", "summary", "chapters": [{"url", "name", "number", "date"}]}`. Only `title` and each chapter's `url` are required. Chapters are sorted by `number` when every one has one, else kept in the order given (first chapter first). |
| `pages(chapter_url)` (required) | A chapter's `url`. | Its pictures in reading order: links, or `{"url", "headers"}` for a picture that needs headers of its own. Relative links are taken from the chapter's page. |
| `search(query, page)` | The title typed, and the page of results (from 1). | `{"items": [{"url", "title", "cover", "summary"}], "more": True when there's another page}` (or just the list of items). |
| `browse(list_id, page)` | One of `LISTS`' ids, and the page. | The same as `search()`. |

Each plugin runs in a process of its own, started the first time it's used and kept while the app runs (a new version replaces it). A call that takes more than 90 seconds stops it. What a plugin prints goes to its log, not to the app.

## Plugin lists

A plugin list is a JSON file anyone can host (a GitHub repository's raw files work):

```json
{
  "name": "My plugins",
  "plugins": [
    {"id": "example", "name": "Example Comics", "version": "1.0.0", "file": "example.py",
     "sha256": "<the file's SHA-256, in hex>", "sites": ["example.com"], "language": "en",
     "nsfw": false, "summary": "A made-up site"}
  ]
}
```

`file` is relative to the list's link (or a full link). `sha256` is required: a file that doesn't match isn't installed. In PowerShell, `(Get-FileHash example.py).Hash.ToLower()` gives it. The plugin is saved as `<id>.py`.

## gallery-dl

[gallery-dl](https://github.com/mikf/gallery-dl) is a command-line downloader that knows many sites. It's GPL-2.0, so it's never part of the app. Install it yourself (`pip install gallery-dl`, or the standalone `gallery-dl.exe` from its releases). **Settings → Sources → gallery-dl** then finds it on PATH, or give its program's path. Tested with 1.32.19.

- **Link** takes any link no plugin claims.
  - A series' link lists its chapters, numbered from the site's chapter numbers (12, 12.5, 13…) with their titles.
  - A chapter's own link is one chapter.
  - Links starting `generic:` (gallery-dl's way of taking the pictures from any page) go straight to it.
- gallery-dl downloads each chapter itself, so its own settings apply: a config file with your cookies or login for a site, and its waits and retries. A run that ends with an error adds nothing.
- Keep its post-processors (zipping, converting) off for the sites you use with it: the app expects the pictures as they are.
- It has no search or lists: find the series on the site and paste its link.

## Suwayomi

[Suwayomi-Server](https://github.com/Suwayomi/Suwayomi-Server) runs Mihon/Tachiyomi extensions on a PC. It's MPL-2.0 and not part of the app. If you already use those extensions, run Suwayomi with your extension repositories, and **Settings → Sources → Suwayomi** connects to it. The address is `http://127.0.0.1:4567` unless you changed it. Tested with v2.4.2366.

- **What shows up:** every source installed in Suwayomi appears in Get chapters, named "… (Suwayomi)" with its language. Its Local source (the server's own folder) appears too.
- **Lists:** each source's Popular, and Latest when the source has one.
- **Search:** searches one source.
- **Link:** Suwayomi has no way to open a link.
  - A link to a series it has already shown you (in a search or a list) opens.
  - Any other link on one of its sources' sites searches that source for the link's last words.
- **Chapters and pages:** chapters come oldest first, and their pages are downloaded through the server.
- **A login:** when the server has one turned on, enter the user name and password under Suwayomi and press **Save login**. All three of its kinds work (basic auth, simple login and UI login). They're kept in `data\suwayomi_login.json`.
- **What Suwayomi downloads by itself:** on its first start, it downloads a 268 MB Chromium for its built-in browser, plus its web interface. That's Suwayomi, not Recap Studio; its `kcefEnabled` and `webUIEnabled` settings turn them off.

## What isn't there yet

- Sites that need a login or a browser to get past their protection work only as far as the plugin (or gallery-dl, or the Suwayomi extension) handles it.
- Suwayomi's search filters (genres, status and the like) and its extensions' own settings stay in Suwayomi: the app searches by title only.
- New chapters are found when the app starts or when you check, not on a schedule; and found chapters aren't recapped on their own: **Make the recap** does them.
- The phone page can't get chapters or add plugins (installing code from a phone is left to the PC).
