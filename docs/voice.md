# Voice

## Kokoro, Microsoft Edge or ElevenLabs

Settings → **Voice** picks who reads the narration (**Play a sample** tries the choice):

- **Kokoro**, on this computer: free and private (the default).
- **Microsoft Edge voices** (online, free, no account): the voices Edge reads web pages aloud with, through the edge-tts package (LGPL-3.0). 322 voices in many languages, English first in the list. It isn't an official service, so Microsoft can change or limit it, and the narration is sent to Microsoft. Four lines are read at once: 8 lines took about 2 seconds. A line it doesn't answer is tried again after 2, 5 and 15 seconds.
- **ElevenLabs** (online, paid): with your own API key (Settings, stored only on this PC in `data\elevenlabs_key.txt`, or the `ELEVENLABS_API_KEY` environment variable). Settings lists your voices and its models (with how many credits a character costs), and **Test key** shows how many characters your plan has left this month. It's billed per character: a chapter's narration is typically about 5,000. Two lines are read at once; it takes speeds from 0.7 to 1.2. Tested against a stand-in server that answers like its API, not with a real key.

With an online voice, problems pause the Voice job instead of failing it: a missing or rejected key, no characters left, a voice it doesn't have, or the service not answering after a few tries. Fix it in Settings (or edit the line), then Resume. Speed applies to all three. Changing the voice doesn't redo lines already voiced: run Voice with Redo for that.

## Your own voice-over

Each chapter's **🎙** button (in the chapter list) takes your own recording of the chapter instead of a generated voice. It shows the chapter's script to read (with **Copy**); record yourself reading it in order, as one file (WAV, MP3, M4A, FLAC, OGG and others), and upload it. Speech recognition on this computer, Vosk with its small English model (Apache 2.0; a 41 MB download into `data\addons\vosk`, offered the first time, checksum-checked and unpacked), writes down what was said with each word's time. Those words are lined up with the script's, so each line gets its part of the recording (with a little room at each end), and the clips are used in the video and the export.

- The dialog shows how many lines were found and what share of the script's words were heard, lists the lines to check (heard poorly, or not found), and anything said that isn't in the script. Names and unusual words aren't in the recognizer's vocabulary, so a line with them shows fewer words heard even when it was read right. Under each scene, a recorded line shows 🎙 and its share.
- A line that wasn't read keeps the voice it had (the gap where it would be is used only if there's speech in it), so run Voice for it, or record again. **Voice** never replaces lines from your recording, even with Redo. Editing a recorded line's narration drops its clip, since it no longer matches. **Remove the recording** deletes it and its clips.
- Tested with a made-up recording (Kokoro reading a 12-line chapter as an MP3, one line left out and a sentence added, with room noise): it was split in about 5 seconds; the 11 lines read were found, 89–100% of their words heard, each clip within 0.25 s of the line's length and holding its own line when listened to again; the line left out was listed as not found, and the added sentence as said but not in the script.
