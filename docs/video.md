# Video

## Rendering chapter by chapter

The video is made one chapter at a time. Each chapter is kept as a finished piece (its video, narration and subtitles) in the project's `render` folder, under a fingerprint of everything it depends on: its pages or panel pictures, the voice files, the narration and the video settings. Then the pieces are joined into the final video without encoding the picture again (the narration is encoded once, as AAC).

- **Picks up where it stopped.** Cancel a render, close the app or lose power, and the next Render video reuses every chapter already finished and only makes the rest. **Pause** works too, between chapters.
- **Only what changed is made again.** Edit a line and regenerate its voice, and only that chapter is rendered again; the others are reused. Changing a video setting (size, frame rate, style, timing, encoder) makes every chapter again, since they all look different.
- Chapters fade briefly to black between each other (over the crossfade time), so each can be made on its own. Scenes inside a chapter still crossfade.
- The pieces take about as much disk space as the video. Old versions of a chapter are deleted when it's made again, and a deleted chapter's pieces go with it.
- The time estimate for Render video counts the chapters still to make, plus joining them all into the video (about a minute and a half per hour of video, on one CPU thread; the app learns this computer's own speed). The progress bar gives each its share: it moves with ffmpeg's position while the chapters are joined, instead of waiting at the end.

**Split into parts** (the project's Video tab, advanced mode): one video; a new video every N chapters; or videos of at most N hours (YouTube takes up to 12 hours per video; the default is 11). Parts are cut between chapters, from each chapter's exact length, and named `recap - Part 2 (Chapter 21 to Chapter 40).mp4` and so on, each with its own subtitles, narration and chapter list. Changing the split only joins the pieces again, which takes about a minute and a half per hour of video.

**YouTube chapters**: next to each video is `<name> - YouTube chapters.txt`, one line per chapter with its start time (`0:00 Chapter 1`, `6:12 Chapter 2`, or `1:02:05 …` past an hour). Paste it into the video's description and YouTube shows the chapters on the timeline (it needs at least three, each 10 seconds or longer). Under the player, the app has a button for each part and links to its subtitles and chapter list.

## Video size, frame rate and style

Each project's **Video** tab sets how its video looks:

- **Format** and **Resolution**: landscape or portrait, at 1080p (1920×1080), 720p (1280×720), 480p (854×480) or 360p (640×360). Smaller videos are much quicker to make: 720p has under half the pixels of 1080p, 360p about a ninth. Scrolling keeps the same pace through the story at every size.
- **Frame rate**: 60 down to 5 fps. Scrolling and pan and zoom look smooth from about 24 fps; stills look the same at any rate.
- **Style**: *Automatic* scrolls webtoon chapters (when the project's Webtoon video is set to scroll) and pans and zooms other comics; *Pan and zoom, scene by scene*; or *Stills, scene by scene*. A still is drawn once and reused for every frame of its scene, so stills are the fastest style. With a style other than Automatic, the script writer is no longer told chapters scroll.
- **Pan and zoom toward**: the middle of the scene, or *the scene's main subject*. The subject is found by U2-Net-p (Xuebin Qin et al., Apache 2.0, as converted to ONNX by the rembg project), a 4.6 MB download into `data\addons\focus` from Settings, checksum-checked. It marks what stands out in a picture (usually the character; in a panel that's only a caption, the caption); a scene that zooms then zooms in toward it, up to 1.4 times past fitting the frame, so the subject fills about three quarters of it, with the picture staying inside the frame where it fits and covering it where it doesn't. A subject near the picture's edge gets less zoom, so it never ends further from the middle than it starts. Each scene is looked at once (about 0.03 s on an NVIDIA GPU, 0.2 s on the CPU) and remembered in the project's `focus.json`. On the test series it found a subject in 184 of 186 scenes, and all 62 zooms ended with their subject nearer the middle. Tall and wide panels still pan from end to end. The Resolve export moves the same way (its keyframes match the video exactly).
- **Remove the text from the pictures**: the text in speech bubbles and caption boxes is painted over in the video (and in the export for editors), so the art carries the scene while the narration tells it. Text is found by the same reader as Read text (no other model), grouped into bubbles, and painted out only where it sits on a plain background: the band around it nearly all one colour, and its own box mostly that colour too. Letters on a flat background are painted in that colour; on a shaded one they're filled in from around them. Sound effects and signs drawn over the art, and bubbles with busy surroundings, are left as they are (painting them out would leave a smudge). On a made-up picture the bubble and caption were cleared with their outlines kept and nothing else changed; on 10 test-series panels the text the reader still finds went from 47 lines to 22 (what's left is mostly sound effects). The first video made this way reads every picture again (about 0.1 s each on the GPU; the test series' 320 panels took 34 s); the cleaned copies are kept in the project's `textless` folder and reused, and copies no longer used are deleted when the whole project is rendered.

## Webtoon video: scrolling

For webtoon chapters, the video can scroll down the whole chapter as one continuous strip instead of showing scenes one at a time (Project settings → **Webtoon video**; scrolling is the default). Each scene's narration plays while the middle of the screen passes through that scene, so the scroll slows down for long lines and moves faster through quiet stretches, with a speed limit so it never races. Scenes left out of the video (such as the scan group's pages) aren't shown at all: the strip is the scenes used, each stretch joined straight onto the next. The cuts between pieces never show, because the video shows the real strip.

Chapters detected before this feature get their positions in the strip recovered automatically when the video is rendered (detection is re-run and matched to the existing panels; narration and voice are kept). A chapter that can't be matched is shown as scenes, and the job says which ones; detecting its panels again makes it scroll.

When Claude writes a scrolling chapter, it's told the chapter is one continuous scroll, so it can write lines that cover a few quick panels instead of narrating every fragment.

## Export for video editors

**Export for editors…** (after the step buttons, in advanced mode) saves the ticked chapters (none ticked = all) as an OpenTimelineIO timeline to finish the recap in a video editor: `recap (for DaVinci Resolve).otioz` or `recap (for editors).otioz` in the output folder. An `.otioz` is a zip holding the timeline and all its media, so it can be moved to another computer. The timeline has:

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
