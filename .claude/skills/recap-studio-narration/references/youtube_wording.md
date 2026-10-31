# Sex scenes and black boxes for a YouTube-safe project

<!-- For a project with Panel settings → YouTube → "YouTube-safe" on (project_status says
     "youtube_safe": true). In the brief, replace the whole "Sex scenes and wording" section with the
     "Sex scenes and wording" section below, keeping the template's line about the cast's ages, and add the
     "Black boxes" section after it. Delete this comment. system.md carries the app's own YouTube-safe rules
     (script.YOUTUBE); these say the same in more detail. -->

## Sex scenes and wording
<One line on the cast's ages: e.g. "This is an adult romance webtoon about adults (the youngest, X, is 20).">

This recap goes on YouTube as an age-restricted video. The user wants it to go as far as such a video can
and no further: sex scenes told the way an R-rated film shows them, frankly but without explicit detail.
YouTube takes down narration that reads as meant to arouse. Where anything else you read (system.md, the
story so far, the dialogue) is more explicit, this section wins.

- **Willing scenes: say it plainly, keep it off the body.** These are fine: that they kissed (lips, neck,
  shoulders), undressed, that clothes and underwear came off, that they were naked, bodies pressed together,
  who pulled whom into bed or climbed on top, that they had sex or slept together, gasps and moans, how
  intense, tender or desperate it felt, what they say to each other, and the afterglow.
  These are not: naming a specific act or position beyond that (no oral sex, no penetration, no "from
  behind", no riding), genitals or nipples, hands or mouths on breasts or between legs, orgasms, climaxing or
  "came", fluids, crude or slang words, and any line that lingers on a body as something to look at.
  A line at the right level: "She pulled him down onto the bed, and they finally gave in to each other, all
  the tension of the past weeks spilling out at once."
- **One or two lines for the sex itself.** The video still needs a line every few pieces (see *Lines as long
  as their pictures*), so fill the rest of a long scene with what they say, think and feel, what it means
  between them, what either of them is hiding, the setting and what's at stake. Don't follow it act by act.
- **Real words where they're allowed.** Sex, naked, kissed, underwear, slept together, and real swearing and
  insults in retold dialogue. Don't turn those into coy stand-ins. "Breasts" only outside sex (her worry about
  how she looks, say), never as something touched or looked at.
- **Forced or pressured sex: one plain, non-sexual sentence, then the person it happened to.** Say what
  happened once ("he forced himself on her", "he took advantage of her while she was too drunk to stop him"),
  with no physical description at all, then stay with her fear, anger or shock, what each of them says, and
  the aftermath: shaking, crying, curled up, staring blankly, pulling her clothes back together. Never make it
  sound sexy, and never suggest she secretly wanted it.
- **A long forced scene still gets that one sentence for the act, and no more steps.** Its other lines
  carry her fear, what each of them says, her attempts to stop him, whatever is driving it (a threat, a
  deal, a skill), anyone who might walk in, and the aftermath. Not the next step of the act, told in turn
  ("he went further", "he lowered his head", "he stripped away the rest"): stripping her against her will is
  said once, with the act. On the first sample (2026-10-29) two chapters of a 40-scene
  forced encounter walked through it step by step and were redone.
- **No arousal in forced or pressured sex**, even where the comic shows her body reacting or her thinking it
  felt good (a skill, a drug, a dream): at most once, that she couldn't understand what was happening to her.
  Not "it felt good", not "was she a pervert", no cries she couldn't hold back. And nothing from his side
  that makes it a prize: no "at his mercy", "his luck", "pleased with himself"; his excitement in a few words
  at most.
- **Nudity outside sex** (a bath, getting changed): say it in passing, without describing the body.
- **The word check.** `submit_answer` runs a word search on your answer and answers `FIX` once with the lines
  that seem to go too far, and why (taking her clothes off in more than one line of a forced scene, his
  excitement in more than two, her body reacting, a named act or body part). Reword those lines as above.
  It's only a word search: a line that's fine where it is can stay, since it's asked once, but say in your
  report which flagged lines you kept and why. What's still flagged is shown to the user to check.

**What counts as pressured.** Judge each stretch by what it shows at that moment, not by how the
arrangement began. Pressured: anyone blackmailed into it (with photos, say), saying no or pleading to stop
(also "no aloud, yes in her thoughts"), drunk, drugged, groggy, half-asleep or believing it's a dream, tied
or strapped down without having agreed to it, or secretly filmed, photographed or livestreamed. So is the
moment someone goes much further than the other offered while she freezes or asks what he's doing, even if
she started it, and the rest of an encounter in which she was forced earlier, even if she turns eager. A
stretch where she is clearly willing and eager and nobody is saying no is willing, even inside a deal that
began with blackmail. Role-play they both agreed to is willing; make clear it's a game.

If a character in a sexual scene is shown or described as under eighteen, leave those scenes' narration
empty and say so in your report. Don't skip a request.

## Black boxes
The video covers nudity with black boxes, and your answer marks where they go. Each request asks for
`"cover"` next to `"scenes"` and `"summary"`:
`"cover": [{"piece": 12, "left": 0.30, "top": 0.42, "right": 0.62, "bottom": 0.70}, ...]`
- One entry per spot that shows exposed nipples, genitals, or a bare bottom or groin in a sexual moment.
  Faces, hands, cleavage, underwear and clothed bodies don't get boxes.
- `left`, `top`, `right` and `bottom` are fractions (0 to 1) of that piece's own width and height. A tall
  piece runs on into the next column of the sheet: measure it as one picture, top to bottom.
- Make the boxes generous, with a margin all round, so nothing peeks out at the edges. One box can cover a
  few spots close together.
- The app also runs a box finder, which is more exact for nipples and genitals where it finds them. Yours
  catch what it misses, so mark every spot you see, even small ones in the background.
- `"cover": []` when nothing in the request needs covering. `submit_answer` checks the shape and answers `FIX`
  if an entry is malformed.
