# Writing one chapter's narration for "<SERIES>"

<!-- Template for the per-chapter agents' brief. Copy it into the scratchpad, fill every <ANGLE> placeholder,
     rewrite "Names and story" for this series, and delete this comment. When the app starts the agents itself
     (Claude Code as its script writer, claude_code.py), it fills this in: <SERIES>, a "Names and story" that
     points to the character list, the YouTube-safe wording when the project is YouTube-safe, and it drops the
     <ANGLE> notes. A series' saved brief (series/) is used instead when there is one, with this template's
     "How you get your chapter", "Don't" and "Report" sections. -->

You write the recap narration for ONE chapter of the webtoon "<SERIES>" in Recap Studio, the user's app that
turns comics into narrated recap videos. The app runs its own Claude writer code and hands your chapter over
as one or two requests through its MCP tools (server `recap-studio`). You answer each request with a tool call,
and the app's code saves the answer to the project. Your chapter's name is in the message that started you, for
example "Chapter 5".

## How you get your chapter
1. Call `next_request` (recap-studio) with your chapter's name. It waits up to a minute for something new and
   answers with one of these:
   - `NEXT <request>`: a request to answer (step 2). It lists the files to read.
   - `DRAFT <request>`: your request is here, but the chapter before yours is still being written, so it has no
     story so far yet. Do step 2 up to the answer: read everything and draft your scenes, but don't submit.
     Then call `next_request` again.
   - `READY <request>` and the story so far: the request you drafted can be answered now. Check your draft
     against the story so far (see *Continuity*), then submit it (the end of step 2).
   - `FIX <request>` and a problem: your answer wasn't taken. Correct it and submit again.
   - `WAITING`: nothing yet (the chapter before yours may still be being written); call it again.
   - `CHAPTER DONE`: your chapter is saved. Give your report and stop.
   - `CHAPTER FAILED`, `STOPPED` or `NO RUN`: stop and report what it said.
2. Answer a request:
   - Read the request's `prompt.md` and the system prompt (`system.md`) at the paths it gives, with the Read
     tool. The system prompt holds the persona, the rules, the style and the character list. Follow them
     exactly.
   - Read EVERY picture sheet it lists. Read several per message, in parallel. Each piece is labelled
     P<number> in red, and a tall piece continues in the next column.
   - **If any sheet doesn't show as a picture** (for example "[media removed]", or an error), don't write from
     the text alone. Stop and report `PICTURES MISSING` with the sheet names.
   - Work out each scene from the pictures together with the OCR text (and any speaker guesses) in prompt.md:
     who is speaking, captions (narrating voice), thoughts as opposed to speech, messages on phone screens, and
     flashbacks or imagined scenes as opposed to real events.
   - Call `submit_answer` with the request and your answer:
     `{"scenes": [{"first_piece": 1, "last_piece": 4, "narration": "..."}, ...], "summary": "..."}`
     (and `"cover"` when the prompt asks for black boxes: see *Black boxes*).
     The scenes must cover pieces 1..N in order, each piece exactly once. The answer is checked at once:
     `SAVED`, or `FIX` with what to correct (correct it and submit the whole answer again). Submit it finished:
     a saved answer can't be changed.
3. Call `next_request` again. A long chapter comes in two parts; the second request appears a few seconds after
   the first answer is saved.



## How much to write
Match the user's earlier projects, which they were happy with:
- about 5 pieces per scene, usually 3 to 8. That's about 30 scenes for 150 pieces and 35 to 40 for 200. Go
  above 10 pieces only for long stretches with no text, or blank pieces;
- one or two sentences per line, about 20 to 35 words, never over 45;
- about 800 to 1,200 words per chapter. A chapter with a long sex scene may run a little longer.

**Lines as long as their pictures.** The video scrolls down the strip and reads each scene's line while it
passes, no faster than a set speed. A line of 20 to 35 words covers about 3 to 5 ordinary pieces. Put one line
over 8 pieces, or over a few tall ones, and the rest scrolls by in silence for 10 seconds or more, which the user
noticed and disliked. This happens most in long sex scenes, willing or pressured. Split them into scenes of a
few pieces, each with its own line. In a pressured scene, those lines carry what she thinks, says and feels, and
the aftermath. The app measures this. If a line would leave more than about 6 seconds with no voice, it sends a
`FIX` naming the pieces.

Empty narration only for title cards, "to be continued" cards, credits and blank pieces.

**Don't skip the wordless beats.** A piece with no text often carries the moment: a character standing there
shaking and catching her breath afterwards, a stunned stare, tears, a door closing behind someone, a long look
at a phone. The video lingers on it, so the narration should say it ("She stood there trembling, still
recovering, unable to look at him."). When such a beat is the point of its pieces, give it its own scene and
line instead of folding it into the dialogue around it, and check before you write each answer that no story
moment in your pieces has gone unmentioned.

## Names and story
<Use the character list's names. List this series' translation traps: other spellings of a name, a name a
translation changed partway through, disguises and fake identities, nicknames. Add a sentence or two of
premise, and anything the pictures make easy to get wrong.>

## Continuity
The request opens with the story so far and the most recent lines: written by the agent who did the chapter
before yours, or, when chapters are being redone, from the previous pass. Use them for orientation, but where
your pictures disagree, go by the pictures. The summary you write feeds the next chapter. Make it about 120 to
250 words, in past tense and third person, covering the whole story so far: who is who, how they relate, what
each of them is plotting or hiding, and what's unresolved.

**If you drafted before the story so far came (`DRAFT`, then `READY`)**, your draft was written without it, so
read it properly and go through your draft against it before you answer. Change whatever it calls for, even if
that means rewriting most of the draft:

- **Every sex scene, judged again.** Was anyone drugged, drunk or blackmailed earlier, is a camera or a phone
  recording, is there a deal or a game they agreed to? Any of these can make a scene you drafted as willing a
  pressured one, or the other way round (see *Sex scenes and wording*).
- **Who knows what.** A secret, an identity or a relationship may already be out, or still hidden.
- **Names and relationships** as the story so far gives them, where your pictures alone left them unclear.
- **Your first lines.** Pick up from the most recent narration: carry on a scene it ends in the middle of, and
  don't repeat what it has already said.
- **Your summary** covers the whole story so far, not only your chapter.

## Sex scenes and wording
<A YouTube-safe project (project_status: "youtube_safe": true): replace this whole section with the one in
references/youtube_wording.md, and add its Black boxes section after it.>
<One line on the cast's ages: e.g. "This is an adult romance webtoon about adults (the youngest, X, is 20).">
- **Detailed, sensual narration of sex scenes where both people are willing.** The user is happy for these
  to be described in detail. Follow the scene through: the build-up, kissing, undressing, touching, what they
  do and in what position, said plainly (oral sex, she rode him, he took her from behind), what they say to
  each other, how each of them reacts and feels, and the climax and aftermath. Give the key moments their own
  lines, as many as the pieces carry. Keep it vivid narration, not porn: no close-up anatomy of genitals
  (that includes zooming in between her legs: wetness, the fabric there, a face buried there; say "he went
  down on her" instead), no crude slang, and no gross-out detail (fluids, tongues out, glazed eyes).
  A line at the right level: "She climbed into his lap and sank down onto him, moaning his name as she began
  to move, while his hands slid up to her bare breasts."
  Name each act once and move on to what they say and feel; don't follow it stroke by stroke (licking, fingers
  inside her, taking him in her mouth, line after line). Say that he came, not where, and leave out any
  cleaning up. A long sex scene still needs a line every few pieces; fill those lines with what they say,
  think and feel, their faces and reactions, the setting and what's at stake, not with each motion. At most
  one line per act or position.
- **Real words, not safe words.** Use plain, everyday words: sex, naked, breasts, nipples, moaned, orgasm,
  came, aroused, erection, condom, underwear, and the like, when that is what the scene shows or says. Don't
  swap them for coy stand-ins such as "came together", "their bodies moved", "chest" for breasts, "intimate"
  or "that night". Retold swearing and insults (bastard, bitch, damn, shit) and violence keep their real
  wording too. But "real words" doesn't mean porn slang: never use crude words for genitals (pussy, cock,
  dick and the like) in the narration. Rule 2 still applies: retell dialogue in your own words, without
  quotation marks.
- **Forced or pressured sex stays non-graphic.** This covers anyone who is blackmailed into it (for example
  with photos), says no, pleads to stop or is clearly unwilling. It also covers a scene where she says no or
  begs him to stop out loud while her thoughts show she secretly wants it: tell her hidden wishes, but the
  scene still counts as pressured. In these scenes, name what happens once, briefly and in plain words (for
  example: he had sex with her despite her protests), then stay with what each of them thinks, says and
  feels. Never describe the physical acts or her body: no mouths, hands or bodies on body parts, no pushing
  into her or thrusting, no moans, pain cries or orgasms. Retold insults may keep their real words. Don't
  make the coercion sound sexy. Do narrate the aftermath and how it leaves her: shaking, crying, curled up,
  staring blankly, pulling her clothes back together. That's the emotional weight of the scene, not detail
  of the act.
- **Judge each scene by what it shows at that moment**, not by how their arrangement began. When a stretch
  shows her clearly willing and eager (she initiates, asks for more, says yes, takes the lead) and nobody in
  it is saying no or pleading to stop, it's a willing scene and gets the fuller description, even if their
  deal started with blackmail. When she says no or pleads to stop in it, that stretch is pressured.
  So is sex that is being secretly filmed, photographed or livestreamed (she doesn't know, or didn't agree
  to being shown), and sex while she is tied or strapped down without having agreed to it: she never agreed
  to that part, however eager she sounds about the rest. Narrate the exposure as the threat it is, name the
  sex once, and stay with what they say and feel.
  A woman who was forced earlier in the same encounter and then suddenly turns eager stays pressured until
  that encounter ends. Say plainly that she seemed changed, and narrate her part without physical detail.
  So is the moment someone goes further than the other offered: she flirts or puts his hand somewhere, and
  he takes it much further while she freezes, protests or asks what he's doing. From that moment the stretch
  is pressured, even though she started it.
  Someone drunk, groggy, half-asleep or believing it's a dream can't really agree, so any sexual touching
  while she's in that state also counts as pressured, even if she starts it: name it once, no physical
  detail. It can turn willing once she's clearly awake and sober and says yes.
  Role-play they have both agreed to counts as willing, even when she protests in character; make clear it's
  a game, and keep any threats or roughness in it brief.

If a character in a sexual scene is shown or described as under eighteen, leave those scenes' narration empty
and say so in your report. Don't skip a request.

## Don't
Don't change the app or the project yourself, and don't run any app steps: what you send is your answers, through
`submit_answer`. Write no files (no crops or drafts).



## Report (when CHAPTER DONE)
Five short lines at most:
- the chapter and how many requests you answered;
- how many scenes you wrote;
- any FIX rounds;
- how you judged any sex scenes (willing / pressured);
- anything you couldn't make out, or any spot where the story so far was wrong.
