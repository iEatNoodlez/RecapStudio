# Video

## Rendering chapter by chapter

The video is made one chapter at a time. Each chapter is kept as a finished piece (its video, narration and subtitles) in the project's `render` folder, under a fingerprint of everything it depends on: its pages or panel pictures, the voice files, the narration and the video settings. Then the pieces are joined into the final video without encoding the picture again (the narration is encoded once, as AAC).

- **Picks up where it stopped.** Cancel a render, close the app or lose power, and the next Render video reuses every chapter already finished and only makes the rest. **Pause** works too, between chapters.
- **Only what changed is made again.** Edit a line and regenerate its voice, and only that chapter is rendered again; the others are reused. Changing a video setting (size, frame rate, style, timing, encoder) makes every chapter again, since they all look different.
- **Made while the script is written.** When Make the recap (or Continue) has an online writer write the script (Claude Code, Claude, or a cloud service), this PC mostly waits for it. So each chapter the writer finishes is voiced and rendered beside it, and the job's line says so ("Working ahead: rendering Chapter 4 (40%); 3 chapters voiced, 2 rendered"). Voice and Render video then only do what's left. A YouTube-safe chapter whose wording waits to be checked waits for Voice, as before. With the local writer (Ollama) the steps run one after another, since it needs this PC's graphics card itself.
- Chapters fade briefly to black between each other (over the crossfade time), so each can be made on its own. Scenes inside a chapter still crossfade.
- The pieces take about as much disk space as the video. Old versions of a chapter are deleted when it's made again, and a deleted chapter's pieces go with it.
- The time estimate for Render video counts the chapters still to make, plus joining them all into the video (about a minute and a half per hour of video, on one CPU thread; the app learns this computer's own speed). The progress bar gives each its share: it moves with ffmpeg's position while the chapters are joined, instead of waiting at the end.

**Split into parts** (the project's Video stage, under More settings): one video; a new video every N chapters; or videos of at most N hours (YouTube takes up to 12 hours per video; the default is 11). Parts are cut between chapters, from each chapter's exact length, and named `Solo Leveling - Part 2 (Chapter 21 to Chapter 40).mp4` and so on, each with its own subtitles, narration and chapter list. Changing the split only joins the pieces again, which takes about a minute and a half per hour of video.

**YouTube chapters**: next to each video is `<name> - YouTube chapters.txt`, one line per chapter with its start time (`0:00 Chapter 1`, `6:12 Chapter 2`, or `1:02:05 …` past an hour). Paste it into the video's description and YouTube shows the chapters on the timeline (it needs at least three, each 10 seconds or longer). Under the player, the app has a button for each part and links to its subtitles and chapter list.

## Video size, frame rate and style

Each project's **Video** tab sets how its video looks:

- **Format** and **Resolution**: landscape or portrait, at 1080p (1920×1080), 720p (1280×720), 480p (854×480) or 360p (640×360). Smaller videos are much quicker to make: 720p has under half the pixels of 1080p, 360p about a ninth. Scrolling keeps the same pace through the story at every size.
- **Frame rate**: 60 down to 5 fps. Scrolling and pan and zoom look smooth from about 24 fps; stills look the same at any rate.
- **Style**: *Automatic* scrolls webtoon chapters (when the project's Webtoon video is set to scroll) and pans and zooms other comics; *Pan and zoom, scene by scene*; or *Stills, scene by scene*. A still is drawn once and reused for every frame of its scene, so stills are the fastest style. With a style other than Automatic, the script writer is no longer told chapters scroll.
- **Pan and zoom toward**: the middle of the scene, or *the scene's main subject*. The subject is found by U2-Net-p (Xuebin Qin et al., Apache 2.0, as converted to ONNX by the rembg project), a 4.6 MB download into `data\addons\focus` from Settings, checksum-checked. It marks what stands out in a picture (usually the character; in a panel that's only a caption, the caption); a scene that zooms then zooms in toward it, up to 1.4 times past fitting the frame, so the subject fills about three quarters of it, with the picture staying inside the frame where it fits and covering it where it doesn't. A subject near the picture's edge gets less zoom, so it never ends further from the middle than it starts. Each scene is looked at once (about 0.03 s on an NVIDIA GPU, 0.2 s on the CPU) and remembered in the project's `focus.json`. On the test series it found a subject in 184 of 186 scenes, and all 62 zooms ended with their subject nearer the middle. Tall and wide panels still pan from end to end. The Resolve export moves the same way (its keyframes match the video exactly).
- **Remove the text from the pictures**: the text in speech bubbles and caption boxes is painted over in the video (and in the export for editors), so the art carries the scene while the narration tells it. Text is found by the same reader as Read text (no other model), grouped into bubbles, and painted out only where it sits on a plain background: the band around it nearly all one colour, and its own box mostly that colour too. Letters on a flat background are painted in that colour; on a shaded one they're filled in from around them. Sound effects and signs drawn over the art, and bubbles with busy surroundings, are left as they are (painting them out would leave a smudge). On a made-up picture the bubble and caption were cleared with their outlines kept and nothing else changed; on 10 test-series panels the text the reader still finds went from 47 lines to 22 (what's left is mostly sound effects). The first video made this way reads every picture again (about 0.1 s each on the GPU; the test series' 320 panels took 34 s); the cleaned copies are kept in the project's `textless` folder and reused, and copies no longer used are deleted when the whole project is rendered.

## Webtoon video: scrolling

For webtoon chapters, the video can scroll down the whole chapter as one continuous strip instead of showing scenes one at a time (the Chapters stage's panel → **A webtoon's video**; scrolling is the default). Each scene's narration plays while the middle of the screen passes through that scene, so the scroll slows down for long lines and moves faster through quiet stretches, with a speed limit so it never races. Scenes left out of the video (such as the scan group's pages) aren't shown at all: the strip is the scenes used, each stretch joined straight onto the next. The cuts between pieces never show, because the video shows the real strip.

Chapters detected before this feature get their positions in the strip recovered automatically when the video is rendered (detection is re-run and matched to the existing panels; narration and voice are kept). A chapter that can't be matched is shown as scenes, and the job says which ones; detecting its panels again makes it scroll.

When Claude writes a scrolling chapter, it's told the chapter is one continuous scroll, so it can write lines that cover a few quick panels instead of narrating every fragment.

## Background music

Turn on **Background music** in New project (The video), or **Play music under the narration** in the Video stage's panel, and add your own tracks there with **Add music…** (MP3, WAV, M4A, AAC, FLAC, OGG, Opus or WMA). They play in the order listed (↑ and ↓ change it), then again from the first until the video ends; each part of a split video starts the list again. The music fades in at the start and out at the end.

- **Even loudness.** Each track is converted once (48 kHz stereo, kept in the project's `render\music` folder) with its loudness evened out to the same level, so a loudly mastered song doesn't drown the narration and a quiet one doesn't vanish, and its ends fade so one track gives way to the next. **Volume** then sets the music against the voice: 25% (the default) puts it about 13 dB under the narration.
- **Quick to change.** The music is mixed in when the chapters are joined, so turning it on or off, changing the tracks or the volume, then **Make the video again** only joins the video again (about half a minute for an 11-minute video), without drawing anything.
- **Left out:** the narration file (the video's name with `.wav`) and **Export for editors** stay voice only, so the music can be laid in the editor instead.
- **Your music, your rights.** Nothing is bundled. Use music you're allowed to put on YouTube, such as tracks from the YouTube Audio Library, or the video may get a Content ID claim.
- Tracks are part of the project: **Save as file** takes them along. Ones under 6 seconds aren't taken.

## Captions in the video

**Captions in the video** (New project, The video; or the Video stage's panel → Captions and Shorts) draws the narration into the picture a few words at a time, the way CapCut's auto-captions look on Shorts and TikTok, with the word being said picked out. The subtitles file (`.srt`) is made either way.

- **CapCut classic**: bold white capitals with a black outline and a soft shadow, up to three words at a time, the word being said in colour and popping up a little; each new set of words rises into place.
- **Highlight box**: the same capitals, up to four words, with a rounded box in colour behind the word being said.
- **Minimal**: the words as written, up to six on two lines, on a dark see-through band, the word being said in colour. Calmer, for long videos.
- **Colour** of the word being said: yellow (the default), green, red, purple, or white to pick out nothing. **Letters**: Arial Black (the default), Segoe UI Black, Impact or Comic Sans, all in Windows. A picture of the style chosen shows under the choice, over one of the project's pictures.
- **Where**: in the lower middle of the picture, about two thirds down in portrait (clear of the Shorts and TikTok buttons) and four fifths down in landscape. A page of words that's too wide goes on two lines, then gets smaller.
- **Timing**: a new set of words starts after a sentence, a pause, or about a second and a half of speech. When each word is said comes from the line's sound: each comma and full stop is lined up with a pause in it, and the words between are spread over the speech between those pauses by their length. On the 11-minute sample, its 211 commas and full stops landed on their pauses (half exactly, 9 in 10 within 0.02 s), where spreading the words alone was off by 0.36 s in the middle and up to 0.8 s by the end of a long line. With the speech recognizer installed (Settings → Voice, the add-on that lines up your own recordings) each word is heard where it's said instead. The times are kept next to each line's audio and worked out again only when the line changes.
- **Making it**: captions are drawn into each chapter's video, so turning them on, or changing the style, draws the video again. They add well under a millisecond a frame (0.6 ms at 1080p). The 11-minute sample took about 5 minutes with them on the GPU encoder. With captions off, chapters already made are reused as before.

## Shorts

**Shorts** (New project, The video: **Also cut it into Shorts**; or the Video stage's panel → Captions and Shorts → **Cut into Shorts**) cuts the finished video into parts of up to 1 minute, 90 seconds or 3 minutes, saved in the output folder's `shorts` folder (`Solo Leveling - Short 01.mp4` and on); the Video tab lists them under the player to watch.

- **Where it's cut**: each Short is as long as it can be and ends in the middle of a pause between two narration lines, never mid-sentence (and never in its first 40%, so none is very short; the last one is what's left). The 11-minute sample made 13 one-minute Shorts: 12 of 48 to 59 seconds, and a last one of 20.
- **"Part 1", "Part 2"…** in big white capitals at the top of each, unless turned off in the Video tab.
- They're cut from the finished video, so the captions and background music come with them. Each is encoded again (the cuts don't fall on the video's keyframes): about a minute for the 13. Shorts made before for the same video are replaced.
- **Portrait** suits Shorts, TikTok and Reels best; a landscape video's Shorts stay landscape. YouTube takes Shorts of up to 3 minutes.

## Export for video editors

**Export for video editors…** (the Video stage, beside Open the output folder) saves the ticked chapters (none ticked = all) as an OpenTimelineIO timeline to finish the recap in a video editor: `recap (for DaVinci Resolve).otioz` or `recap (for editors).otioz` in the output folder. An `.otioz` is a zip holding the timeline and all its media, so it can be moved to another computer. The timeline has:

- a clip for each scene, timed like the rendered video (a scrolling chapter is cut into a clip per scene, at points where the cut doesn't show);
- the narration as a clip for each line, on Audio 1; the subtitles are saved next to the bundle as `<name>.srt`;
- a cross dissolve wherever the video crossfades (starting on the cut, like the video's), and a marker at the start of each chapter.

Choose what it's made for:

- **DaVinci Resolve**: each scene's picture on Video 2, over its blurred backdrop on Video 1, with the video's pan, zoom or scroll as keyframes of Resolve's Transform effect (zoom and position), so the motion can be changed in the Inspector. Pan and zoom ease in and out like the video's (Bezier handles); a scroll gets a keyframe wherever its speed changes. Very long scrolls and pans are cut into clips of at most 7 screens, since Resolve's position only goes 4 picture-heights either way. No video is rendered, so it's quick: for the 3-chapter test series (186 scenes), about 4 seconds and 13 MB at 360p, or 10 seconds at 1080p. Other editors don't read Resolve's keyframes and show the pictures still.
- **Any editor**: each chapter rendered once with the motion drawn in, every scene running on for the length of a crossfade (the frames an editor's crossfade needs), and the timeline cuts it into a clip per scene. It takes about as long as Render video. These videos are kept in the project's `editors` folder and reused while their chapter doesn't change, so exporting again only renders the chapters that changed.

**Transitions** (for DaVinci Resolve, in the export dialog): *Cross dissolve* like the video, or one of Resolve's own: *Slide*, *Push*, *Blur dissolve*, *Smooth cut*, or *Mixed* (dissolve, slide, push and blur in turn, the same on both video tracks at each cut). They come in with Resolve's default settings, to change in the Inspector; other editors may show them as a plain dissolve or a cut. They're written the way Resolve itself exports a transition (OpenTimelineIO's sample of a Resolve export has one), with Resolve's numbers for each transition taken from RecapSynth's export. The rendered video and the *Any editor* export keep cross dissolves.

To open it in Resolve, first set the project's timeline resolution and frame rate to the video's (the project's Video tab) and keep **Image scaling** on *Scale entire image to fit* (the default; the keyframes are worked out from it). Then use **File → Import → Timeline** and pick the `.otioz`. Premiere Pro opens OpenTimelineIO from version 25.6. For an editor that can't open `.otioz`, rename it to `.zip`, unzip it and open `content.otio` (its media is next to it in `media\`).

How it was checked, on the test series:

- Every object in the timeline has exactly the fields of the same kind of object in a timeline DaVinci Resolve exported itself (from OpenTimelineIO's test files). Every clip's media is in the bundle, the crossfades have the frames they need, and each narration line starts on the frame the video plays it.
- The official OpenTimelineIO library (0.18.1) opens every kind of bundle and finds each clip's media in it. It also places the crossfades on the cuts, keeps Resolve's keyframes, reads the chapter markers, and writes the timeline back out unchanged, using the same schema versions as the export. It caught one mistake, since fixed: markers were written in an old format that every release from 0.14 on misreads.
- The Resolve keyframes follow what Resolve writes: zoom relative to the picture fitted into the frame, position as a fraction of that fitted picture's size (up is positive), keyframes counted from the clip's first frame. Applied that way, a scene's pan and zoom put the picture exactly where the video does (to within 0.0001 px, at every frame checked). A scroll lands within half a pixel, because the video rounds the scroll to whole pixels while an editor places it exactly. Frames put together from the bundle match the video's.
- For any editor, the clips are the video's own frames (frame for frame), and exporting again reuses every chapter that didn't change.
- Resolve's own transitions: each is laid out exactly like the transition in Resolve's sample export, at the same frames and lengths as the cross dissolves, and nothing else in the timeline changes; the official library reads them (as custom transitions, keeping Resolve's block) and writes them back unchanged.

It hasn't been opened in Resolve or Premiere Pro themselves yet: how Resolve reads keyframes and its own transitions when it imports a timeline comes from its own exports and from other programs that write them, not from Blackmagic's documentation.
