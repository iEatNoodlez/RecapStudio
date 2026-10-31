"""Wording a YouTube-safe recap leaves out (the narration skill's references/youtube_wording.md), found in
narration lines: sexual body parts, acts and orgasms in any scene, and in a forced or pressured scene the act told
step by step: undressing in more than one line, his excitement in more than two, her body reacting or her seeming
to enjoy it.

It's a word search, so it can't tell a willing scene from a forced one, or "came home" from the other kind: what it
finds is for the writer, or you, to read again. The Claude Code writer asks its agent about it once, and the chapter
keeps what's still there as "wording" (shown as Check wording in the chapter list)."""
import re

# (why, pattern): wrong in any scene of a YouTube-safe recap
ANY = [
    ("names a sexual body part",
     r"nipples?|areolas?|genital\w*|penis\w*|vagina\w*|crotch|groin|clit\w*|pussy|cocks?|cunt|tits|boobs"),
    ("names a sex act or position",
     r"blowjobs?|oral sex|(went|go|goes|going) down on (her|him)|(took|take|takes|taking|had|having) (her|him) from "
     r"behind|rode him|riding him|on all fours|penetrat\w*|thrust(s|ing)?|(was|were|got|deep|deeper|pushed|slid|"
     r"slipped) inside (her|him)|between (her|his) (legs|thighs)"),
    ("an orgasm",
     r"orgasm\w*|climax\w*|semen|cum|ejaculat\w*|erections?|hard-on|wetness|(made|make|makes|making) (her|him) come|"
     r"came (hard|together|again|inside)"),
    ("hands or mouth on her body",
     r"(touch|touched|touching|grabbed|squeezed|fondled|kissed|licked|sucked|caressed|cupped|stroked|kneaded)\s+"
     r"(\w+\s+){0,2}(breasts?|chest|bottom|butt|ass)"),
]
# a line that says the scene is forced or pressured
FORCED = (r"forc(e|ed|es|ing)\b|forceful\w*|against (her|his) will|without (her|his) consent|rap(e|ed|es|ing|ist)\b|"
          r"assault\w*|begg(ed|ing)|plead\w*|stop it|let (me|her|him) go|pinned|held (her|him) down|holding (her|him) "
          r"down|struggl\w*|blackmail\w*|threat\w*|too drunk|drugged|took advantage|couldn't stop (him|her)|trapped|"
          r"surrender\w*|screamed no|cried no|no, no|tied|strapped|half-asleep|passed out|unconscious|helpless")
# (why, pattern, lines allowed): in a forced or pressured scene, what tells it step by step
STEPS = [
    ("her body reacting, or her seeming to enjoy it",
     r"felt (so |really )?good|pervert\w*|moan\w*|pleasure\w*|enjoy(ed|ing|s)? (it|this|that|herself)|turned on|"
     r"(was|felt|got) wet|her body "
     r"(betrayed|responded|reacted|gave in)|couldn't (hold|keep) back (a |her )?(moan|cry|cries|sound|voice)", 0),
    ("taking clothes off",
     r"bras?|panties|underwear|knickers|clasp\w*|unclasp\w*|unhook\w*|unbutton\w*|unzip\w*|undress\w*|"
     r"stripp?(ed|ing|s)?|naked|nude|topless|breasts?|rest of (her|his) clothes|(pulled|pulling|took|taking|tore|"
     r"tearing|ripped|ripping|yanked|yanking|tugged|tugging|peeled|peeling) ((\w+ ){1,3}off|off (her|his|the))", 1),
    ("his excitement",
     r"excite\w*|shudder\w*|giddy|thrill\w*|aroused|arousal|hold back|wanted it|liked it|pleased with himself|"
     r"at his mercy|his luck", 2),
]
WINDOW = 8  # a line belongs to a forced scene when a line this close (before or after) says it's forced


def _rx(pattern):
    return re.compile(r"(?i)\b(" + pattern + r")\b")


_ANY = [(why, _rx(p)) for why, p in ANY]
_FORCED = _rx(FORCED)
_STEPS = [(why, _rx(p), allowed) for why, p, allowed in STEPS]


def find(lines: list[str]) -> list[dict]:
    """What a YouTube-safe recap shouldn't say in these lines (one scene's narration each, in story order):
    [{"index": i, "why": ..., "word": the words found, "line": ...}], in line order."""
    out = {}
    for i, line in enumerate(lines):
        for why, rx in _ANY:
            m = rx.search(line or "")
            if m:
                out[i] = {"index": i, "why": why, "word": m.group(0), "line": line}
                break
    forced = [i for i, line in enumerate(lines) if _FORCED.search(line or "")]
    if len(forced) >= 2:  # one mention ("forced a smile") isn't a forced scene
        near = {i for i in range(len(lines)) if any(abs(i - j) <= WINDOW for j in forced)}
        for why, rx, allowed in _STEPS:
            hits = [(i, m) for i in sorted(near) if i not in out for m in [rx.search(lines[i] or "")] if m]
            if len(hits) > allowed:
                for i, m in hits:
                    out[i] = {"index": i, "why": why + (f" (in {len(hits)} lines)" if allowed else ""),
                              "word": m.group(0), "line": lines[i]}
    return [out[i] for i in sorted(out)]
