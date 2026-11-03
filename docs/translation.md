# Korean, Chinese and Japanese comics

A comic whose text is in Korean, Chinese or Japanese (a raw) is read in that language, translated into English by your script writer, and narrated in English. The English can also be put into the pictures, in place of the original text.

## Choosing the language

New project asks on its first page: **What language is its text in?** (English unless you choose otherwise). It can be changed later in Project settings → **Language of its text** (advanced mode); it applies the next time the text is read.

## Reading the text

- Korean, Chinese (simplified and traditional) and Japanese are read with PaddleOCR's PP-OCRv5 models (Apache 2.0): its text finder, which also finds vertical lines, a reader for Chinese and Japanese, and one for Korean. They're an optional download of 35 MB into the add-ons folder (`ocr_cjk`), not part of the app. **Read text** downloads them the first time a project in one of these languages is read; Settings → Text reading (advanced mode) can get them beforehand, or remove them.
- Each text box is kept with where it is and what it says, for the translation and for the English in the pictures. Vertical Japanese and Chinese is read column by column, right to left; on a right-to-left (manga) page, bubbles side by side are read right first.
- Lines the reader isn't sure of are kept in their box: on a test page the Korean reader read a clear 시작해요? as "8워", and left out, the line would show under the English. The translator checks every box against the picture.
- The Korean reader takes every line as upright. RapidOCR's check for upside-down lines turned real ones over and read them as scraps: on three chapters of a real Korean webtoon, 16% of the lines changed or went missing with it on, 마침내 ("finally") as "H기뭐", nearly every one for the worse. The Chinese and Japanese reader keeps the check until it's tried on a real raw.
- On test pages drawn in Windows' own fonts, every speech bubble, caption and sign was found. Most came out exactly; the rest had a character misread (へ as く, 悦 as its traditional form 悅) or the unsure Korean line above, and the translation read them right from the picture. Big sound effects aren't always found, which doesn't matter: they're left as drawn.

## Translate

**Translate** is a step of its own, after Read text (in advanced mode its own button; Make the recap runs it). Your script writer translates a chapter at a time:

- It sees each piece with text, its text boxes outlined and numbered on the picture, and the text read from each box, and answers each box's English and what kind of text it is: speech, a thought, a caption, a sign (part of the scene: signs, screens, messages, titles), a sound effect, or not text (a watermark, a site's name).
- It's asked for natural English that sounds like the character, about as short as the original so it fits the bubble. Names on the character list keep their spelling; others are romanized the usual way (Korean: Revised Romanization; Chinese: pinyin; Japanese: Hepburn). Honorifics become natural English (Mr. Kim, sir, senior) unless they're what a character is called.
- Boxes it leaves out are asked for again once; any still missing are counted in the job's note and show as not translated.
- Each panel's text becomes the English (thoughts, captions and signs marked as such, sound effects left out): that's what the script, the character names and the title work from. The original stays with each box.
- Any script writer can translate: Claude Code (one agent translates every chapter, on your Claude plan), Claude, Gemini, OpenRouter, DeepSeek, Groq, Mistral, or a local model (best one that can see pictures). It's one request per chapter, with its pictures, like a script request.
- On the test pages, Claude Code (Opus) translated every box correctly, including the misread ones, and told the speech, signs and sound effects apart. On three real chapters of a Spanish webtoon (with Reading Spanish comics, from the plugin list: 910 boxes, about 12 minutes a chapter for one agent) it read the misread lines right from the pictures ("IOTA, NO POLDES HACER TU TRABAJO" as "of crap, can't you do your job"), and marked the scan site's watermarks, and the scraps read off drawn sound effects, as sound effects or not text.

**Check it** in the Script tab: each scene has a **Translation** list with every box's kind, the original and the English. Change the English or the kind, and the text the script is written from follows (write the script again for the narration to change). Translate with Redo translates everything again, replacing your corrections.

## The English in the pictures

**Put the English in the pictures** (New project, How its video looks; or the project's Video tab) replaces the original text in the video:

- **Speech, thoughts and captions** on a plain background (a bubble, a caption box, a see-through bubble) are painted out, as Remove the text does, and the English is lettered in their place, in capitals (Comic Sans MS Bold, which every Windows PC has), in the original's colour. It's fitted to the bubble: its shape is found by filling from where the text was, so each line is as wide as the bubble is at that height, centred where the original text was and kept a little off the bubble's edge, at about the size of the original lettering or smaller to fit.
- A bubble's lines read as separate boxes are painted out and lettered together, as one. Bubbles drawn touching (a chain) share their inside, so each gets the part of it nearest its own text. Text on open background, like the inside of a burst bubble, is lettered close to where it was, and the spikes around it stay.
- **Signs** get the English in a small white caption box over the original.
- **Speech drawn over the art** (no plain bubble around it, like the pink lettering of a whisper), and lettering drawn big as part of the art (a shout), stay as drawn. So do **sound effects**.
- A scan site's watermark (a box the translator marked not text that reads like a web address) is painted out.
- It's done when the video is made, and the pictures are kept in the project's `typeset` folder and reused while the picture and its English stay the same. A scrolling webtoon shows whole pages: each piece's boxes are placed in the strip where the piece was cut from (found once, by matching the piece against the strip), and each page is lettered with a quarter of the pages either side, so a bubble across a page break comes out whole on both.
- Tested on a Korean webtoon chapter (23 pages, 133 text boxes): every bubble and caption the reader found whole was lettered in place; the shouts, sound effects and a whisper over the art stayed as drawn, and 22 watermarks were painted out. (One caption had been cut in two by the panel cutter, which no longer cuts through lettering: see [Limits](using.md#limits).)
- Limits: a long line of English in a small bubble gets small. A box the reader found only part of keeps its original text, unless the rest of its bubble is lettered, which then takes its English too.
