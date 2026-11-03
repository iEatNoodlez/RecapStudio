# Plugins

Plugins need Recap Studio 0.2.0 or later. They add features to Recap Studio: so far **Find the series**, the **Overnight queue**, the **Watched folder**, **New chapters on a schedule**, **When a video is done**, **Post to YouTube**, **Branding**, **Recaps in Spanish**, **Reading Spanish comics** and **EPUB comics**. They come from Recap Studio's own plugin list, [RecapStudio-Plugins](https://github.com/iEatNoodlez/RecapStudio-Plugins), made alongside the app. The list also has sources for series' details: AniList, Kitsu and MangaUpdates. Getting chapters from sites is a different kind of plugin that members bring themselves: see [Getting chapters from sites](sources.md).

## Installing

**Settings → Plugins → Show the plugins** shows Recap Studio's list, with **Install** and, when the list has a newer version, **Update**. Each file is checked against the list's SHA-256 before it's installed.

- **When it starts working:** a plugin that adds features starts the next time Recap Studio starts. Settings → Plugins says so, with **Restart now**: the app closes and opens again (a running job stops where it is, as when the app is closed, and **Continue** picks it up; it asks first). Sources from the list, such as AniList, work at once and show up in Settings → Sources.
- **Switching off or removing:** each plugin can be switched off. Plugins installed from the list can also be removed. Both take effect from the next start.
- **Where they go:** plugins that add features go in `data\plugins\<id>\`, and the files they keep in `data\plugin-data\<id>\`.
- **Why only this list:** a plugin that adds features runs inside the app, with all its access. So only Recap Studio's own list can install one, and another list's are never shown. Lists members share install sources only.
- **Full Suite:** the edition that comes with every plugin that adds features already installed, in the app's own `plugins\` folder. They're switched on and off like the others, and come up to date with the app (or sooner, from the list: when both places have a plugin, the newer version is used). Lite, the other edition, comes with none. The sources (AniList, Kitsu, MangaUpdates) are installed from the list in either edition. See [Install](install.md#download-and-install).

## Find the series

Search for a series instead of typing its name. It names the project, sets its kind and fills in its characters. It searches every source that can search: AniList, Kitsu and MangaUpdates from the list, and any source plugins of your own.

**A new project**
1. With the plugin on, the box at the top left says **Search term** and its button **Search** (in place of New project name and Create). Type the series' name and press **Search**: the New project dialog opens and searches for it straight away. Type something else there and press **Search** to try again.
2. Results are grouped by source, with sources that list characters first.
3. Picking a series names the project after it and picks what kind of comic it is: manga for a series from Japan; webtoon for one from Korea, China or Taiwan; comic for any other. Check it: a Korean series printed as pages exists too. **Pick another** goes back to the results.
4. The dialog's **Characters** page lists the series' characters, with their pictures and roles (main, supporting, background). The main characters are ticked, and supporting ones up to 15 in all. **Main and supporting**, **All** and **None** change the ticks.
5. The ticked characters are added when the project is created, each with:
   - its name
   - its other names (as aliases)
   - its gender
   - a short description
   - its picture
   - a mark saying where it came from ("From AniList" in the Characters tab)
6. **The character scan still runs**, if it's ticked on the same page. Characters marked as preloaded don't stop Make the recap's scan, as names typed in by hand do. The review then matches what the scan found to them:
   - **By picture:** a group that looks like a preloaded character's picture says "Looks like …: add to them".
   - **By name:** Suggest names gets the preloaded cast as the character list, so the writer can say a group is one of them ("The writer says …: add to them").

   Adding a group to a preloaded character gives it the group's best views from the chapters as more pictures. Groups that aren't one of them get names suggested as usual. With **Trust them**, the writer's matches are added without asking.

**A project that already exists:** the Characters tab → **Find the series…** searches for the project's name.
- Characters already in the list (by name) are marked and left as they are.
- **Add N characters** adds the ticked ones.

**Good to know**
- **The descriptions are the site's, and the narrator goes by them.** Look them over in the Characters tab. AniList's marked spoilers are left out, but unmarked ones can slip through.
- **The pictures do two jobs.** They let the [character scan](characters.md) match the groups it finds to these characters, and they're what [Magi's speaker detection](characters.md#speaker-detection-with-magi-optional-add-on) compares against.
- **How well a site's picture matches:** tried once so far, on the Black Jack sample's scan.
  - AniList's one picture of Saito came out as the same character as his group from the chapters (122 appearances), at a difference of 0.172. Under 0.21 counts as the same character, and under 0.26 is suggested.
  - It didn't match the hospital director's group (0.294).
  - Other series' art, and characters whose picture is cover art in another style, may match less well. The writer's names are the second check.
- **It needs a source that can search.** Without one, it says to install AniList.

## Overnight queue

Make the recap for several projects, one after another, while you're away. **Overnight queue…** is in the sidebar, under Open project file.

1. **Pick the projects:** tick them (projects with chapters are listed) and put them in order with ↑ and ↓. Each gets Make the recap: whatever isn't done yet, as its own button does.
2. **When it starts:** now, or **Start at** a time. It waits until then, and the app has to stay open.
3. **When it stops:** **Stop at** a time pauses the project running then, and no other one starts. Resume it when you're back. Without a stop time, it runs until every project has had its turn.
4. **When a project stops to ask you something** (names to check, the scan group's pages, a problem with your script writer or voice):
   - **Leave it for the morning** (the default): its job is cancelled where it waited, and the next project starts. Nothing it did is lost; answer it, then **Continue the recap**.
   - **Wait for me:** the queue waits until you answer.

While it runs, the sidebar button shows how far it is ("Queue: 2 of 5"). The dialog shows each project's state: waiting, running (with its step), done, left for you (with what it asked), or failed (with why). **Stop the queue** starts no other project and lets the running one finish its job.

The queue is kept in `data\plugin-data\overnight-queue\queue.json`. If the app closes, the queue carries on when it opens again, and the project that was running starts again where it stopped.

## Watched folder

Drop chapters into a folder, and they're added to their project and, if you like, recapped. It's set up in **Settings → Watched folder**.

1. **Choose a folder of its own** (not the projects folder).
2. **Make a folder in it for each project, named after the project.** Drop that project's chapters in there: folders of pictures, `.zip` or `.cbz` (or `.epub`, with EPUB comics). For example: `D:\Comics to recap\Give My Regards to Black Jack\Chapter 12.cbz`.
3. **It looks every 30 seconds while the app is open.** A chapter is added once it has stayed the same for 20 seconds, so one still copying waits. It's numbered and placed as Add chapters does it, and it isn't added again: a chapter already in the project, from the same place or with the same name, is passed over.
4. **Make the recap for the chapters it adds** (on by default) starts the project's recap once it has no other job running.
5. **A folder named after no project makes a new one** (off by default), with the last project's choices.

**Nothing is moved or deleted.** Chapters made of pictures are read from where they are, as with Add chapters, so leave them there. Settings shows when it last looked, what's still copying, and what it has added. **Look now** looks straight away.

## New chapters on a schedule

Projects made from a site (Get chapters from a site) are checked for new chapters on the days and at the time you choose. It gets nothing by itself: it checks, and gets chapters, through the source the project's chapters came from (your own plugin, gallery-dl or Suwayomi), as **Check for new chapters** and **Get chapters** do.

1. **When:** Settings → New chapters on a schedule: the days (all by default, or the day a series comes out) and the time. Recap Studio has to be open then; if it isn't, it checks when it next opens. **Check now** checks straight away.
2. **What each project does with its new chapters,** in that same section or in its Project settings:
   - **Don't check.**
   - **Check, and tell me:** the count shows, as with the check when the app starts.
   - **Get them** (the default): a "Getting chapters" job, placed by their numbers.
   - **Get them, and recap just them:** then Make the recap for the new chapters alone, so the video is of them ("recap - Chapter 12 to 14.mp4"). The script writer still gets the earlier chapters' summaries.
   - **Get them, and make the whole recap:** then Make the recap for the whole project.
3. **A project with a job running** gets its chapters once that job has finished. A chapter is never got twice: one the project has, or was told to pass over, isn't new.

A new project starts from the last project's choice, as it does for the kind of comic. Settings lists what each check found and did. The settings and that list are kept in `data\plugin-data\new-chapters\`.

## When a video is done

What happens each time a project's video is made (by Make the recap or Render video), set in **Settings → When a video is done**:
- **Copy it to a folder:** each part of the video, in a folder named after the project if you like, with its subtitles, its thumbnail and its Shorts when ticked. A file is copied under a passing name and renamed when it's complete, so a program watching that folder never takes a half-copied file.
- **Run a command:** run in the project's output folder, with these filled in, each in quotes: `{video}` (the first part), `{videos}` (every part), `{srt}`, `{thumbnail}`, `{folder}`, `{project}`, `{title}` (the suggested YouTube title) and `{copy}` (the copied video). They're also in the variables `RECAP_VIDEO`, `RECAP_PROJECT` and so on. The last line it prints is shown in Settings. It can only be set in the app's own window, never by a web page.
- **Tell your phone:** through [ntfy](https://ntfy.sh), a free app for Android and iPhone: install it, subscribe to the topic Settings makes up, and this PC posts to it. Anyone who knows a topic's name can read it, so keep the made-up one (or run your own ntfy server and give its address). And/or a webhook: Discord's and Slack's get a message; any other gets the details as JSON (the project's and files' names, never this PC's paths). **Send a test** checks it.
- **Also tell me** when a recap stops to ask you something (names to check, the scan group's pages, a problem with your script writer or voice), once each time, and when a recap fails.

The copying and the command run on their own, one video after another, so the next job doesn't wait for them. A project can be left out in its Project settings. **Copy, run and tell now** with the finished video's buttons (the Video tab) does it for that video straight away.

## Branding

Your channel's logo in a corner of every video, and an intro and an outro before and after each part. Set once in **Settings → Branding**; each project chooses in its Project settings: the logo, intro and outro (the default), the logo only, the intro and outro only, or none.

- **The logo:** a picture (a PNG with a see-through background looks best), in a corner, small, medium or large, and how opaque. It's drawn into each chapter as it's rendered, so the Shorts have it too, and changing it renders the chapters again.
- **The intro and outro:** a clip of up to 2 minutes with its sound (`.mp4`, `.mov`, `.webm` and the like), or a picture shown for a few seconds. Each is fitted to the project's video (black bars where the shapes differ, as a 4:3 clip in a 16:9 video) and added to each part once the parts are joined. The recap's own picture isn't encoded again: the intro is made with the same encoder and the three are joined as they are (checked at each join; encoded again only if a join doesn't play cleanly, and the job says so). The subtitles and the YouTube chapter list move along with the intro (the first chapter still starts at 0:00, as YouTube wants).
- **Not in:** the Shorts (cut before) and Export for editors.

Its files are kept in `data\plugin-data\branding\`.

## Post to YouTube

Posts a project's video to your YouTube channel, and its Shorts if you like: when the video is made, or when you say.

**Setting it up, once** (Settings → YouTube). Google lets a program upload to a channel only through an OAuth client of your own. It's free, and made in [Google Cloud Console](https://console.cloud.google.com). The wiki's [step-by-step walkthrough](https://github.com/iEatNoodlez/RecapStudio-Plugins/wiki/Connecting-your-YouTube-channel) has every click, the audit, and what to do when something goes wrong (Settings links to it too). In short:
1. Make a project, and turn on the **YouTube Data API v3**.
2. Set up its sign-in (**Google Auth Platform**, External), then **publish it** (Audience → Publish app). Left in Testing, Google signs you out every 7 days. Published but not verified by Google is fine for your own use: Google warns at sign-in that it isn't verified, and you continue past it.
3. Make an **OAuth client** of the type **Desktop app**. Add its JSON file in Settings → YouTube, or paste its client ID and secret.
4. **Connect your channel.** Google's sign-in page opens in your browser. Recap Studio never sees your password: Google sends the answer to a one-time address on this PC, and only the lasting sign-in (a refresh token) is kept, in `data\plugin-data\youtube-post\token.json`. **Disconnect** takes the access back.

**Two limits that are YouTube's, not the app's:**
- **Private until audited.** Until your Google project passes YouTube's API compliance audit (a form, reviewed by Google), YouTube keeps every video uploaded through it private, whatever privacy you choose. YouTube's [own documentation](https://developers.google.com/youtube/v3/docs/videos/insert) says so for projects made since July 2020.
- **About 6 uploads a day.** A Google project gets a daily quota, and an upload uses most of it. When it runs out, posting stops with a note and the rest go up after it resets, the next day. A project's Shorts can take a few days.

**Choosing, per project**
- **In New project:** a last step, **Posting to YouTube**: don't post it, or post it as soon as Make the recap (or Render video) finishes it; who sees it; and its Shorts.
- **Later:** the Video tab → **Post to YouTube…** shows what the project posts and where each stands: not posted yet, posted (with its link), made again since it was posted, or the day a Short goes up. You can change the title (the suggested one by default), the description, who sees it, a time to make it public (Private only), the Shorts, and whether it posts by itself. **Post now** posts what isn't posted yet. **Also post again what was made again** re-posts a video rendered since.

**What's posted**
- **Each part of the video**, with its title ("(Part 2)" when there are parts), a description from the template in Settings with the part's YouTube chapter list (`{chapters}`), the project's thumbnail, the category and tags from Settings, and not made for kids.
- **Its Shorts**, either with the video or one a day: the first at the time you choose after the video goes up, then one each day. Recap Studio has to be open then, or it posts it the next time it is. Each is titled "… (Part N)", with `#Shorts` in its description. **Only portrait projects post Shorts:** a landscape video's Shorts would be ordinary videos to YouTube.
- **Its subtitles**, if ticked: the `.srt` the app made, uploaded as the video's English captions, so YouTube shows the narration word for word instead of its automatic captions.
- **A playlist**, if chosen: one of the channel's playlists, or one named after the series (made the first time, then reused). The video's parts go in, in order, and its Shorts too if ticked.
- **Nothing is posted twice.** A video made again is only posted again when you ask. Subtitles and playlists are added later to videos posted without them, the next time the project posts.

**Playlists and subtitles need a wider permission.** Uploading only needs "upload videos"; these need "manage your YouTube videos" (YouTube's `youtube.force-ssl` permission). Settings → YouTube → **Also allow playlists and subtitles** asks Google for it the next time you connect. Without it, the job posts the video and notes that they need it. They use quota too: 400 units for subtitles, 50 for each video added to a playlist.

**How it posts:** posting is a job of its own, "Posting to YouTube", with progress, Pause and Cancel. Uploads go up in 8 MB pieces, and a piece that fails is retried from where YouTube says it got to.

**Good to know**
- **Thumbnails:** YouTube takes custom thumbnails only from a verified channel; otherwise the video is posted and the job says the thumbnail wasn't set.
- **YouTube-safe projects:** post them as Private, set the age restriction in YouTube Studio, then publish.
- **The age restriction** can't be set through YouTube's API (its field can be read, not set): set it in YouTube Studio.
- **How far it's been tried:** on a real channel, the setup in the wiki, connecting, and posting a video with its custom thumbnail. Subtitles, playlists, Shorts, the quota and errors were tested against a stand-in for Google's services on this PC.

## Recaps in Spanish

A project's recap narrated in Spanish, Latin American or from Spain, whatever language the comic is in.

- **Choosing it:** New project → **Language of the recap** (under the comic's own language), or later Project settings → Plugins. New projects start from the last project's choice, as they do for the kind of comic.
- **What changes, for that project only:**
  - **The narration:** the script writer is told to write every line in Spanish, natural and spoken, keeping the characters' names as the character list spells them.
  - **The voice:** Kokoro speaks with its Spanish voice (Dora, or Alex for a male English voice); Microsoft Edge's with the region's (Dalia or Jorge for Latin America, Elvira or Álvaro for Spain); ElevenLabs keeps your voice and uses its multilingual model. Settings → Recaps in Spanish can fix the voice as female or male. Your app-wide voice doesn't change, for other projects.
  - **The subtitles and the captions** come from the narration, so they're in Spanish. The captions' words are timed by the voice's pauses, since the speech recognizer only knows English.
  - **The suggested YouTube title** and the thumbnail's words are in Spanish ("| Resumen de manga").
- **Good to know:** YouTube-safe's check of the narration's wording only reads English, so read a Spanish script yourself before an age-restricted upload.

## Reading Spanish comics

A comic in Spanish (a raw) is read in Spanish, then your script writer translates it into English before the narration is written, as Korean, Chinese and Japanese ones are (the Translate step). The English can be lettered into the pictures too.

- **Choosing it:** the comic's language, **Spanish**, in New project or Project settings.
- **Its text reader:** PaddleOCR's PP-OCRv5 (Apache 2.0): the text finder the Korean, Chinese and Japanese reader uses, and the recognizer for Latin-alphabet languages, which reads accents, ñ and ¿. Its alphabet has no ¡: on lettering in capitals it reads one as a small "i", which is turned back into ¡ ("iSI NO QUIERES" becomes "¡SI NO QUIERES"); elsewhere it's left out ("¡Ay, no!" is read as "Ay, no!"), and the translator, who sees the picture too, isn't misled by it. It's downloaded the first time a Spanish project's text is read (about 13 MB, into `data\addons\reading-spanish`, pinned and checked against its checksums), or from Settings → Reading Spanish comics, which can remove it too.
- **Tried on a real Spanish webtoon:** three chapters of a Spanish scanlation (698 pieces, 910 bubbles, read in under a minute on the CPU). Dialogue reads well, accents and ñ included. Two mistakes it made every time are put right: RapidOCR's check for upside-down words is off, since it turned "NO" into "ON" (both letters look the same upside down) and "ESTAMOS" into "SOROSS"; and in capitals, a small letter read in place of a capital ("AH, sí.") is made a capital. Sound effects drawn into the art come out as scraps ("J 7 1"). The scan group's pages are left out by their Spanish words too (traducción, "estamos reclutando", "lee antes en"). Then the rest of the recap, with Claude Code (Opus) writing: Translate (about 12 minutes a chapter) read the misread lines right from the pictures and marked the scraps and the scan site's watermarks as sound effects or not text; the English lettered in fitted the bubbles in their colours, with the signs and the series' title logo in caption boxes; then the script (173 scenes), the voice and a 26-minute video. Speech on dark, textured bubbles stays in Spanish: the lettering paints out plain backgrounds only.
- **Together with Recaps in Spanish**, a Spanish comic can be recapped in Spanish: it's still read, translated into English for the script writer to understand, and narrated in Spanish.

## EPUB comics

Chapters imported from a comic's `.epub` as from a `.cbz`: with Import folder, by dropping the file on a project, and by the Watched folder.

- **A volume is split into its chapters** when its table of contents names two or more. Each becomes a chapter of the project, named as the contents name it ("Chapter 3"), in the book's order; an entry for the volume above its chapters gives way to them.
- **Pages that aren't the story:** the cover, title and colour pages before the first chapter go with it. The contents, copyright, newsletter and advertisement pages, and a preview of another volume, are left out, and so is a picture the book shows three times or more (the divider pages between episodes, say).
- **Otherwise the book is one chapter,** named after the file: when its contents name no chapters, or list every page.
- **The pages** are its pictures, in the book's reading order. A text page with no picture (a contents list, say) has nothing to import.
- **Left out, saying why** (in the import list, or when the file is dropped): an EPUB locked with DRM, a novel (pages of text, not pictures), and a file that isn't an EPUB. Only DRM-free EPUBs can be read, such as ones you make yourself (with Calibre or Kindle Comic Converter, say) or ones a store sells without DRM. One that only its store's own app opens can't be.
- **Each chapter remembers its part of the file,** so importing the folder again marks them already imported, and the Watched folder doesn't add them twice, even after one is renamed.
- **Good to know:** tested on made-up EPUBs (EPUB 2 and 3, and the different ways pages and contents are written) and on one real volume, Give My Regards to Black Jack 1 (the sample's source, an EPUB 2 from Calibre): its seven episodes, the cover with the first, and its contents page and twelve divider pages left out. Contents entries are recognised by their English names.

## AniList

AniList is a source for series' details only, with no chapters, from [AniList's public API](https://docs.anilist.co) (no account or key).

- **What it searches:** comics, manga, manhwa and manhua, but not light novels.
- **Characters:** up to 100 per series, main ones first. Each comes with its name, other names (including its name in the original language), gender, role, a picture, and a description: the first paragraph of AniList's, without spoilers or lines like "Age: 17".
- **How complete it is:** AniList is written by volunteers, so older or less-read series can be thin. Give My Regards to Black Jack lists one character; Solo Leveling lists 100.
- **AniList's terms:** the API is free for non-commercial use, and it allows about 30 to 90 requests a minute. A search and a series' characters take a few.

## Kitsu

Kitsu is a source for series' details and characters, with no chapters, from [Kitsu's public API](https://kitsu.docs.apiary.io) (no account or key). Much of its character data came from MyAnimeList, so it stands in for it: Jikan, the public API that read MyAnimeList (whose own API has no characters), shut down on 1 October 2026.

- **What it searches:** comics of every kind (manga, manhwa, manhua, one-shots), but not novels.
- **Characters:** up to 100 per series, main ones first, then supporting. Each comes with its name, other names (its Japanese name, and nicknames, which can give away later chapters, such as Griffith's "Femto"), role, a picture, and a description: the first paragraph of Kitsu's, without lines like "Age: 17". Kitsu has no genders. Its descriptions mark no spoilers, so look them over.
- **How complete it is:** thorough for older and well-known Japanese manga (Berserk lists 70, One Punch Man 75, Jujutsu Kaisen 27), and empty for most series since about 2019 and for the webtoons tried (Solo Leveling, Tower of God, Omniscient Reader, SPY×FAMILY, Frieren). AniList is the better first choice for those.

## MangaUpdates

MangaUpdates is a source for series' details only, with no chapters and no characters, from [MangaUpdates' public API](https://api.mangaupdates.com) (no account or key).

- **What it's for:** it lists nearly every series that has been scanlated, under all its names, so Find the series finds many that AniList doesn't have. Picking one names the project and sets its kind (Manga as manga, Manhwa and Manhua as webtoon, the rest as comic). Its characters still come from the scan, or from AniList when AniList has the series.
- **What it searches:** comics of every kind, by any of their titles; novels and drama CDs are left out.
- **MangaUpdates' terms:** its acceptable use policy asks for credit, which Find the series gives by naming each result's source, and for spacing and caching requests: the plugin waits a second between them and keeps answers for an hour.
- **Its characters:** none. Pick the series from AniList or Kitsu to get them, when either lists it.

## How plugins are made

This part is for people adding to Recap Studio's plugin list. A plugin that adds features is a folder named after its id:

| File | What it is |
|---|---|
| `plugin.json` | Required: `{"id", "name", "version", "api": 1, "summary"}`. `api` is the plugin version it's written for; an app that gives an older one refuses it with a readable message. |
| `__init__.py` | Optional: `setup(plugin)`, called once as the app starts, registers what it adds (below). |
| `web\plugin.js`, `web\plugin.css` | Optional: loaded by the page after the app's own script, with `window.RecapStudio` (below). Other files in `web\` are served beside them. |

**What `setup(plugin)` is given**

| | |
|---|---|
| `plugin.router` | A FastAPI router, served under `/api/plugins/<id>/`. |
| `plugin.step(name, label, run)` | A step of its own, `<id>.<name>`, run on its own as a job like the extra steps: `run(job)`, with `job["project"]` the project's id. |
| `plugin.on(event, function)` | Called with keyword arguments: `project_created` (`project`), `job_done` (`job`, whose `status` is `done`, `error` or `cancelled`), `video_rendered` (`project`, `parts`: the video files, `shorts`). A failing handler is logged and passed over. |
| `plugin.every(seconds, function)` | `function()` again and again, that many seconds apart, on a thread of its own, for as long as the app runs. A failure is logged and it runs again next time. |
| `plugin.make_recap(project_id)` | Starts Make the recap for a project (what isn't done yet) and gives its job. |
| `plugin.run(project_id, name)` | Starts one of its own steps for a project, as a job of its own, and gives the job. |
| `plugin.job(project_id)` | The project's latest job (`id`, `status`, `step`, `message`, `progress`, `pause_reason`), or `None`. |
| `plugin.project_setting(name, default)` | A setting each project keeps, `<id>.<name>`: offered to New project (its page script sets it in `state.settings`), saved by Project settings, carried in project files. |
| `plugin.settings_filter(function)` | `function(settings)` gives what it changes in a project's settings (`{key: value}`, or `None`), app-wide ones too, such as the voice. Applied each time a project is loaded, and never saved into it. |
| `plugin.script_instructions(function)` | `function(settings)` gives more instructions for the narration's writer (`""` for none), added before the answer's format. |
| `plugin.title_instructions(function)` | The same for the YouTube title and thumbnail's writer. |
| `plugin.language(code, name, names=None, reader=None, ready=None, fix=None)` | A language a comic can be in, read and then translated into English: `names`, how its names are written; `reader()`, RapidOCR's arguments for its own models (`None`: the built-in reader; its check for upside-down lines is off, as for the app's own readers, unless they say `"use_cls": True`); `ready(job)`, run before its text is read, to download them; `fix(text)`, each bubble's text as read, with what its reader always gets wrong put right. |
| `plugin.scenes(function)` | `function(project, chapter, items)` gives the chapter's scenes to render, changed (order, length, what's shown), or `None` to leave them. Each item is a scene (`{"type": "scene", "images", "audio", "narration"}`) or a webtoon strip. Also used by Export for editors. |
| `plugin.overlay(function)` | `function(settings)` gives a picture to lay over every frame, or `None`: `{"image": a file (PNG with transparency, or any picture), "where": "top-left", "top-right", "bottom-left" or "bottom-right", "width": a share of the frame's width (0.12), "margin" (0.03), "opacity" (1)}`. It's part of each chapter piece's fingerprint, so changing it renders the chapters again. |
| `plugin.file_type(ext, name, chapters, pages)` | A kind of file chapters are imported from (`".epub"`), wherever the app imports them: Import folder, files dropped on a project, and so the Watched folder. `chapters(path)` gives `[{"name", "pages"}]`, one entry for each chapter in the file (a single entry with `"name": None` is named after the file), `pages` being strings of its own (its pictures' names in it, say). Raising `ValueError` leaves the file out, with its message as the reason shown. `pages(path, pages)` yields `(name, bytes)`, each page's picture, in order. Pictures, `.zip`, `.cbz` and a type another plugin reads can't be taken. |
| `plugin.data_dir` | `data\plugin-data\<id>`, for its own files. |
| `plugin.id`, `plugin.name`, `plugin.version`, `plugin.folder` | |

Anything else a plugin needs, it imports from the app (`from recap import store, importer`). Those can change between versions, so the list's plugins are tested with each release.

**What the page script is given (`window.RecapStudio`)**

| | |
|---|---|
| `api(path, opts)`, `el(tag, attrs, ...children)`, `ask`, `notify`, `plural`, `showTab(name)` | The app's own helpers. |
| `current()`, `reload()` | The open project, and opening it again after the plugin changed it. |
| `on(event, function)` | `new-project-open` (`{name}`), `new-project-page` (`{page, body, state}`: add to `body`; `state.name`, `state.kind`, `state.setName()`, `state.setKind()`, `state.redraw()`), `project-created` (`{project}`, awaited before the project opens), `project-opened` (`{project}`). |
| `addButton(where, label, onclick)` | A button in the Characters tab (`"characters"`), beside Get chapters from a site (`"chapters"`), with the finished video's buttons in the Video tab (`"video"`), or in the sidebar under Open project file (`"sidebar"`, for what's about every project). |
| `addNewProjectPage({id, title, render})` | A page of its own at the end of the New project dialog (so its Create project button moves there). `render({body, state})` draws it; the plugin keeps the choices and applies them on `project-created`. |
| `pickFolder(start)` | A folder from the window's folder picker (typed, in a browser). |
| `setNewProjectBox({placeholder, button, title})` | The words of the sidebar's box for a new project and its button (what's typed there still opens New project). |
| `addSettings(title, render)` | A section of its own in Settings; `render(box)` runs each time Settings is drawn. |
| `addProjectSetting({key, label, options, hint})` | A choice in Project settings (a Plugins section) for a setting registered with `plugin.project_setting`: `options` is `[[value, text]]`. Saved as soon as it changes, like the rest. |
| `addLanguage(code, name)` | A comic language registered with `plugin.language`, offered in New project and Project settings. |

**Where plugins load from**
- The app's own `plugins\` folder: Full Suite's.
- `data\plugins\<id>\`: those installed from the list.
- When both places have one, the newer version is used.
- A plugin that fails to start is listed in Settings → Plugins with its error, and the app runs without it.

**In the list:** a plugin that adds features goes in as `{"id", "kind": "feature", "name", "version", "api", "file", "sha256", "summary"}`, where `file` is a zip of the plugin's folder (its files at the zip's top). The plugin list's `tools/build.py` zips each one and writes `index.json` with the checksums.

**If its setup fails partway**, everything it registered is taken back (its steps, hooks, settings and languages), and it's listed in Settings → Plugins with its error.

**Tests:** the plugin list's own (`tests/run.py` in its repository) run each plugin against a Recap Studio checkout, on scratch data. The app's tests (`tests/run.py` here) cover the hooks themselves, with a test plugin.
