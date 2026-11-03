# The app's tests

Run them with the app's own Python (in a copy run from source, its `.venv`):

```powershell
.venv\Scripts\python tests\run.py            # every test, each in a process of its own
.venv\Scripts\python tests\run.py hooks      # only the tests whose names have these words
```

Nothing here touches your projects. Each test makes a scratch data folder of its own (`RECAP_DATA`, in the temp folder: removed when every check passed, kept and named when one failed), with made-up projects (`helpers.make_project`: coloured panels, a tone for each narration line) and a made-up comic site (`fakesite.py`). No real site, script writer or online voice is used, and gallery-dl and Suwayomi aren't needed.

| Test | What it covers |
|---|---|
| `test_hooks.py` | Every plugin hook: project settings, the settings filter (never saved), the writers' instructions, languages and their readers, and the scenes and the overlay in a real render (checked in its frames); a plugin whose setup fails leaves nothing behind |
| `test_file_types.py` | Plugin file types: a file read as one chapter or a chapter for each part (in the file's order), in Import folder and dropped files; what can't be read listed with why; each part its own source; types a plugin can't take |
| `test_reading.py` | Reading text on made-up pictures: no turning over lines taken for upside down; a tall webtoon piece's line across a chunk's edge read once and whole, a line too tall for the overlap still read; a plugin language's fix; the scan group's pages found in a Latin-alphabet raw's own text, Spanish words included, and the job's note on them |
| `test_claude_code.py` | Claude Code as the script writer, without Claude Code: a narration request read back into its pieces with their pictures, a translated comic's included (each of its chapters failed before its agent started) |
| `test_plugins.py` | Feature plugins end to end: loading, routes, steps, events, page files; Recap Studio's own list (made up) and another list; installing, switching off, removing; unsafe or broken plugin zips |
| `test_restart.py` | Settings → Plugins → **Restart now**: the app closes and opens again on the same address, asking first while a job runs |
| `test_sources.py` | Getting chapters through source plugins (from a file and a plugin list), on the made-up site: links, search, lists, covers, downloads, new chapters, and plugins that break, hang or crash (takes about 4 minutes: it waits out a hanging plugin) |
| `test_folders.py` | Named project folders, moving the projects folder (also to "another drive", and back when it fails), project files |
| `test_pause.py` | Pause during a real render: held before the next chapter, and pressed (or the Overnight queue's stop time come) during the last chapter, held before the join with nothing joined; Resume joins it |
| `test_video_settings.py` | Video settings per project, moved from the old app-wide ones; New project's choices |
| `test_updates.py` | Updates by edition, through a made-up GitHub release: Lite, Full Suite and 0.1.x copies, installed and portable, each pick their own edition's file; a release without it offers none; the label the window, sidebar and Settings show |
| `test_unicode.py` | A project and source folder with a non-ASCII character in their names ("꞉", the colon look-alike download tools use): panels, credits, pictures and uploads still saved and read |

The plugins on Recap Studio's own list have tests of their own, in [their repository](https://github.com/iEatNoodlez/RecapStudio-Plugins/tree/main/tests), run against this app.
