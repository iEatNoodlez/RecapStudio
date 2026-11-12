"""The pages a scan group (or a publisher) puts at the start and end of chapters: credits, Patreon and
Discord notices, "read early" ads, other series they do, recruiting, end cards. Found so they're left
out of the script and the video. The series' own cards and notes stay: its title and "To be continued"
cards, the creators' credits, warnings and season notes.

A piece near either end of a chapter counts as the group's page when the same picture is there in at
least two other chapters (one other chapter, in a project of up to three), or once text has been read,
when its text is a credit or a notice (Patreon, Discord, "read early", translator...). A web address or
the group's name alone doesn't count: groups put their watermark on story panels too. Story panels
aren't repeated like that: the one exception, a chapter opening on the previous one's last panels,
repeats in a single other chapter, so it doesn't count. Pictures are compared at 64 x 64 in grey:
nearly the same (under 3% apart on average, and correlated over 0.95) with nearly the same shape.
Looser, and a series' look-alike speech bubbles counted as repeats.

Once text has been read, a piece whose words are the series' own (to be continued, adaptation, art by,
episode 12...) stays though it repeats, and so do its copies in the other chapters (the text read from
one copy can be garbled where another's isn't). So does the rest of a card cut into pieces: a repeated
piece or a scrap of a few words in the same scene as such a piece, and a piece with the series' title
next to one (or to the series' words drawn with the group's banner), or not next to a piece with the
group's words. The title alone isn't enough: the group's header names the series too (so a piece with
the group's name or address on it is never the title card).

A raw's own text counts before Translate when it's in the Latin alphabet (a Spanish scan group's pages say
"traducción", "Discord" and "estamos reclutando"); a Korean, Chinese or Japanese one's waits for its English.

Your choices count most: a piece with the same picture as one you put back stays, and one with the same
picture as one you left out goes (in chapters added later too). A chapter that's only a notice
(postponed, a hiatus, a season break: short, with a notice's words) is left out whole.

What's found is checked by kind (kinds): each picture once, with its copies in other chapters, so one
choice keeps or leaves out them all. Make the recap waits for that before the script (pipeline's
_credits_hold) unless the project trusts what's found. A piece is checked once you chose for it
(credits_kept), or it followed an earlier choice, or the wait was resumed without checking it
(credits_seen); blank pieces go with the pages around them and aren't asked about.

From each end, the left-out block runs to the last of the group's pages, carrying on past at most two
other pieces between them (a chapter number or credits list that changes each time), or four once their
text has been read and none could be dialogue (the covers of the group's other series, between its
ads); blank pieces and the series' cards don't count either way, and those cards stay in. Then a little
more of that last piece's scene, toward the story (a notice cut into several pieces): blank pieces, and
up to two others: before text is read, any; after, the group's pages and pieces with no text (not
even the watermark, which is on story panels).
"""
import math
import re

import cv2
import numpy as np

from .panels import imread_gray

WINDOW = 30            # pieces looked at from each end of a chapter
SIZE = 64              # pictures are compared this small, in grey
NEAR, CORR = 0.03, 0.95
SHAPE = 0.15           # log of height/width within this
PLAIN = 0.04           # a piece this even (standard deviation, 0-1) is blank
GAP, GAP_READ = 2, 4   # other pieces allowed between two of the group's pages (see above)
# a credit or a notice: what the group's pages say (OCR runs words together, so few word breaks). Not
# "join us" or "recruit" alone: story characters say those
TEXT = re.compile(
    r"patreon|patrons?\b|discord|ko-?fi|read ?(chapters? ?|it ?)?early|early ?access|scanlat|translat(or|ion|ed ?by)"
    r"|proof ?read|typeset|redraw|\bcleaner\b|clean(ing|ed) ?by|quality ?check|raw ?provider"
    r"|recruit(ing|ment)|we.?re ?recruit|support ?(us|our)|join ?(our|us ?on) ?(discord|server|team|patreon)"
    r"|other ?series|collab|series ?we ?do|(visit|read)\w* .{0,40}\.(com|org|net)\b"
    r"|traducci[oó]n|traductor|reclutamiento|estamos ?reclutando|lee ?antes ?en", re.I)  # (Spanish, as in English)
# the group's watermark, on story panels too: not a sign of its pages, and not the story's words
WATERMARK = re.compile(r"\S*(scans?\b|\w\.(org|com|net|gg|io)\b|https?:|www\.)\S*", re.I)
# the series' own cards and notes (OCR garbles "continued": "be e continuec")
SERIES = re.compile(
    r"contin(ue|uec|ueo)|to ?be ?con|the ?end\b|origina\w* ?work|adaptation|colou?r(ing|ist)|(story|\btext) ?[/:-]"
    r"|\bart ?[/:-]|\bart ?by|script ?[/:-]|illustrat\w* ?by|written ?by|creator|managed ?by|copyright|©"
    r"|all ?rights|discretion|episode ?\d|#\s?\d|season ?\d|next ?(week|episode|season)|will ?r\w?turn"
    r"|thanks? ?(you ?)?for ?reading|hiatus|postpone|^\W*fin\W*$|continuar[aá]", re.I)
STOP = {"the", "and", "with", "from", "that", "this", "your"}
# a notice-only chapter: at most this many pieces (webtoon story chapters had 55 or more in three series; a manga's
# can have fewer), with a notice's own words in its text. NOTICE's words are a notice's (a hiatus, a break, readers);
# SECOND's come up in story dialogue too ("did you notice", "I'm sorry", "I'll be back"), so they only count beside
# one of NOTICE's. (A bare "notice" and a "sorry" left My Dress-Up Darling's story chapters 4 and 5, 22 and 27
# pieces, out whole until 2026-11-11.)
SHORT = 40
NOTICE = re.compile(
    r"postpone|hiatus|on ?(a )?break|season ?\d|announcement|for ?waiting|maternity|give ?birth|\breaders\b"
    r"|(important|special|short) ?notice|notice ?:|will ?(resume|return)|delayed ?(until|to|by)|for ?the ?(delay|wait)"
    r"|no ?(new ?)?(chapter|update|episode)|(release|update|upload) ?schedule|will ?be ?(released|uploaded|updated)", re.I)
SECOND = re.compile(r"notice|announce|circumstances|will ?(be ?)?back|apologi[sz]e|sorry", re.I)


def _groups(panels):
    """Each chapter's pieces in order."""
    out = {}
    for x in panels:
        out.setdefault(x["chapter"], []).append(x)
    return out


def _fingerprint(path):
    img = imread_gray(path)
    if img is None:
        return None
    h, w = img.shape
    return cv2.resize(img, (SIZE, SIZE), interpolation=cv2.INTER_AREA).ravel(), math.log(h / max(1, w))


_cache = {"dir": None, "prints": {}}  # the last project's fingerprints, by file (with its time): 4 KB a piece


def _print_of(project_dir, piece):
    """A piece's fingerprint (its picture at 64 x 64 in grey, 0-1, and the log of its shape), or None."""
    if _cache["dir"] != str(project_dir):
        _cache.update(dir=str(project_dir), prints={})
    path = project_dir / "panels" / piece["file"]
    try:
        stamp = path.stat().st_mtime_ns
    except OSError:
        return None
    hit = _cache["prints"].get(piece["file"])
    if not hit or hit[0] != stamp:
        hit = (stamp, _fingerprint(path))
        _cache["prints"][piece["file"]] = hit
    if hit[1] is None:
        return None
    return hit[1][0].astype(np.float32) / 255, hit[1][1]


def _said(piece) -> str:
    """A piece's words: its text, or before Translate, a raw's own text when it's in the Latin alphabet."""
    text = piece.get("text") or ""
    source = piece.get("source_text") or ""
    if text or not source:
        return text
    letters = [c for c in source if c.isalpha()]
    return source if letters and sum(c < "\u0250" for c in letters) >= 0.8 * len(letters) else ""


def _words(text: str) -> list[str]:
    """The words read (three letters or more), without the group's watermark."""
    return re.findall(r"[A-Za-z][A-Za-z']{2,}", WATERMARK.sub(" ", text))


def dialogue(text: str) -> bool:
    """Could be the story's words: a line with ? or !, a sentence, or six words or more (not a sound
    effect, or the title on another series' cover)."""
    words = _words(text)
    rest = WATERMARK.sub(" ", text)
    return bool(words) and (bool(re.search(r"[A-Za-z]['’]?\s*[?!]", rest)) or len(words) >= 6
                            or (len(words) >= 2 and bool(re.search(r"[A-Za-z][.,…]", rest))))


def series_card(text: str) -> bool:
    """Words of the series' own cards and notes: to be continued, the creators' credits, episode
    numbers, warnings, season notes. The group's words win (its banner drawn with the end card)."""
    return bool(text.strip()) and not TEXT.search(text) and bool(SERIES.search(text))


def _title_words(name: str) -> list[str]:
    return [w for w in re.findall(r"[a-z]{4,}", name.lower()) if w not in STOP]


def _titled(text: str, title: list[str]) -> bool:
    """A few words with the series' title in them (on its title card, or the group's header: not
    one with the group's name on it)."""
    if WATERMARK.search(text):
        return False
    words = _words(text)
    letters = re.sub(r"[^a-z]", "", text.lower())
    return 0 < len(words) <= 5 and any(t in letters or any(len(w) >= 4 and w.lower() in t for w in words)
                                        for t in title)


def find(project: dict, project_dir, chapter_ids=None, progress=None, cancelled=None, followed=None) -> dict:
    """{chapter id: [ids of the pieces to leave out]} for chapter_ids (default: all), comparing them with
    every chapter of the project. progress(fraction); cancelled() stops it (InterruptedError). followed (a
    set) gets the ids found because they're copies of a piece you left out."""
    by_ch = _groups(project["panels"])
    order = [c["id"] for c in project["chapters"] if c["id"] in by_ch]
    wanted = set(chapter_ids or order)
    title = _title_words(project.get("name", ""))
    items = []  # (chapter index, "head"/"tail", position from that end, piece)
    for ci, cid in enumerate(order):
        pans = by_ch[cid]
        for k, x in enumerate(pans[:WINDOW]):
            items.append((ci, "head", k, x))
        for k, x in enumerate(reversed(pans[-WINDOW:])):
            items.append((ci, "tail", k, x))
    raw = np.zeros((len(items), SIZE * SIZE), np.float32)
    shape = np.zeros(len(items), np.float32)
    usable = np.zeros(len(items), bool)
    for i, (_, _, _, x) in enumerate(items):
        if cancelled and cancelled():
            raise InterruptedError("cancelled")
        f = _print_of(project_dir, x)
        if f is not None:
            raw[i], shape[i] = f
            usable[i] = float(f[0].std()) >= PLAIN
        if progress and i % 50 == 0:
            progress(0.8 * i / max(1, len(items)))
    said = [_said(it[3]) for it in items]
    ad = np.array([bool(TEXT.search(t)) for t in said])
    series = np.array([series_card(t) for t in said])
    # your choices: a piece you put back (+1) or left out (-1), whose copies elsewhere follow
    chose = np.array([0 if "credits_kept" not in it[3] else 1 if it[3]["credits_kept"] else -1 for it in items])
    norm = raw - raw.mean(axis=1, keepdims=True)
    norm /= np.maximum(norm.std(axis=1, keepdims=True), 1e-6)
    chap = np.array([it[0] for it in items])
    n = len(order)
    need = 2 if n >= 4 else 1
    repeated = np.zeros(len(items), bool)
    copy_of_card = np.zeros(len(items), bool)  # the same picture as a series card elsewhere
    copy_of_choice = np.zeros(len(items), int)  # ... as a piece you put back (+1) or left out (-1)
    mine = np.isin(chap, [ci for ci, cid in enumerate(order) if cid in wanted]) & usable
    rows = np.nonzero(mine)[0]
    if n > 1:
        for start in range(0, len(rows), 256):  # correlations a block at a time; the close ones checked fully
            block = rows[start:start + 256]
            corr = norm[block] @ norm.T / norm.shape[1]
            for r, i in enumerate(block):
                cand = np.nonzero((corr[r] > CORR) & usable & (chap != chap[i]) & (np.abs(shape - shape[i]) < SHAPE))[0]
                if len(cand):
                    close = cand[np.abs(raw[cand] - raw[i]).mean(axis=1) < NEAR]
                    repeated[i] = len(set(chap[close].tolist())) >= need
                    copy_of_card[i] = bool(series[close].any())
                    copy_of_choice[i] = int(np.sign(chose[close].sum()))  # (a tie: neither)
            if progress:
                progress(0.8 + 0.2 * min(1.0, (start + 256) / len(rows)))
    # what each piece is, by id (a short chapter's pieces are in both its head and its tail)
    blank, read, talk, named, card, group = set(), set(), set(), set(), set(), set()
    for i, it in enumerate(items):
        pid = it[3]["id"]
        if not usable[i]:
            blank.add(pid)
        if it[3].get("ocr_done"):
            read.add(pid)
            if dialogue(said[i]):
                talk.add(pid)
        if copy_of_choice[i] > 0 or copy_of_choice[i] == 0 and (series[i] or copy_of_card[i]) and not ad[i]:
            card.add(pid)
        elif copy_of_choice[i] < 0 or ad[i] or repeated[i]:
            group.add(pid)
            if copy_of_choice[i] < 0 and followed is not None:
                followed.add(pid)
        if not ad[i] and _titled(said[i], title):
            named.add(pid)
    out = {}
    for ci, cid in enumerate(order):
        if cid not in wanted:
            continue
        pans = by_ch[cid]
        scene_of, s = [], -1
        for x in pans:
            s += 0 if x.get("joined") and s >= 0 else 1
            scene_of.append(s)
        ends = set(range(min(WINDOW, len(pans)))) | set(range(max(0, len(pans) - WINDOW), len(pans)))
        # the rest of a series card cut into pieces (see above)
        for _ in range(2):
            for pos in sorted(ends):
                pid = pans[pos]["id"]
                text = _said(pans[pos])
                few = pid in read and pid not in talk and len(_words(text)) <= 3  # a scrap: "!!", "T IV NT TIN"
                if pid in card or pid in blank or not (pid in named or pid in group or few) or ad_of(pans[pos]):
                    continue
                near = []  # the pieces on either side, past blank ones
                for step in (-1, 1):
                    j = pos + step
                    while j in ends and pans[j]["id"] in blank:
                        j += step
                    if j in ends:
                        near.append(j)
                by_card = [j for j in near if pans[j]["id"] in card]
                # (the series' words drawn with the group's banner still mark its title as the card's)
                by_words = by_card + [j for j in near if SERIES.search(_said(pans[j]))]
                if (pid in named and (by_words or not any(ad_of(pans[j]) for j in near))
                        or any(scene_of[j] == scene_of[pos] for j in by_card)):
                    card.add(pid)
                    group.discard(pid)
        off = set()
        for end in ("head", "tail"):
            last, between = -1, []
            for k in range(min(WINDOW, len(pans))):
                pid = pans[k if end == "head" else len(pans) - 1 - k]["id"]
                if pid in group:
                    last, between = k, []
                elif pid in blank or pid in card:
                    continue
                else:  # between two of the group's pages: a few pieces, fewer when one could be the story
                    between.append(pid)
                    allowed = GAP_READ if all(q in read and q not in talk for q in between) else GAP
                    if len(between) > allowed:
                        break
            if last < 0:
                continue
            idx = [k if end == "head" else len(pans) - 1 - k for k in range(last + 1)]
            idx = [j for j in idx if pans[j]["id"] not in card]
            edge = last if end == "head" else len(pans) - 1 - last  # the block's piece nearest the story
            # more of that piece's scene, toward the story, while it's still the group's (a notice cut into
            # pieces): blank pieces, and up to GAP others. Detection can join a chapter's last lines to the
            # notice after them, so never the whole scene, and once text is read, only the group's pages
            step, j, extra = (1 if end == "head" else -1), edge, 0
            while 0 <= j + step < len(pans) and scene_of[j + step] == scene_of[edge]:
                j += step
                pid = pans[j]["id"]
                if pid not in blank:
                    if (extra >= GAP or j not in ends or pid in card  # (the watermark alone: a story panel)
                            or (pid in read and pid not in group and _said(pans[j]).strip())):
                        break
                    extra += 1
                idx.append(j)
            off |= {pans[j]["id"] for j in idx}
        if notice_only(pans):
            off = {x["id"] for x in pans}
        out[cid] = [x["id"] for x in pans if x["id"] in off]
    return out


def notice_only(pieces) -> bool:
    """A chapter that's only a notice from the publisher or the creators (postponed, a hiatus, a season
    break): short, with a notice's words once its text is read. Story chapters are longer."""
    if not pieces or len(pieces) > SHORT:
        return False
    said = [_said(x) for x in pieces]
    strong = {re.sub(r"\W", "", m.group(0).lower()) for t in said for m in NOTICE.finditer(t)}
    return len(strong) >= 2 or (len(strong) == 1 and any(SECOND.search(NOTICE.sub("", t)) for t in said))


def same(project: dict, project_dir, piece_id: str) -> list[str]:
    """The pieces near either end of the other chapters with the same picture as this one, compared as
    find compares them (none for a blank piece, which looks like any other)."""
    me = next((x for x in project["panels"] if x["id"] == piece_id), None)
    f = me and _print_of(project_dir, me)
    if not f or float(f[0].std()) < PLAIN:
        return []
    a, shape = f
    na = (a - a.mean()) / max(float(a.std()), 1e-6)
    out = []
    for cid, pans in _groups(project["panels"]).items():
        if cid == me["chapter"]:
            continue
        for x in {x["id"]: x for x in pans[:WINDOW] + pans[-WINDOW:]}.values():
            g = _print_of(project_dir, x)
            if not g or abs(g[1] - shape) >= SHAPE or float(g[0].std()) < PLAIN:
                continue
            b = g[0]
            nb = (b - b.mean()) / float(b.std())
            if float(na @ nb) / na.size > CORR and float(np.abs(a - b).mean()) < NEAR:
                out.append(x["id"])
    return out


def blank(project_dir, piece) -> bool:
    """A piece too even to tell from any other (or one that can't be read): it goes with the pages around it."""
    f = _print_of(project_dir, piece)
    return f is None or float(f[0].std()) < PLAIN


def unchecked(piece) -> bool:
    """Left out as the group's page, and not checked yet (see above)."""
    return (bool(piece.get("credits")) and not piece.get("enabled", True) and "credits_kept" not in piece
            and not piece.get("credits_seen"))


def kinds(project: dict, project_dir, chapter_ids=None) -> list[dict]:
    """The pieces found as the group's pages, by kind, to check them: each picture once (its copy in the
    earliest chapter), with its copies in the other chapters, compared as find compares them, and a page cut
    into pieces as one (pictures in the same chapters, with only blank or left-out pieces between them);
    blank pieces aren't listed. Kinds with something not checked yet first, then the biggest. Each is {"ids",
    "show" (the pieces shown: the earliest chapter's), "chapters" (how many it's in), "why", "open" (how many
    aren't checked), "kept" (how many are used)}. chapter_ids: only the kinds with a piece in those chapters.
    On five series, 1087 pieces left out came to 141 kinds, 904 to 105, 118 to 25."""
    order = {c["id"]: k for k, c in enumerate(project["chapters"])}
    by_ch = _groups(project["panels"])
    found = [x for x in project["panels"] if x.get("credits") and not blank(project_dir, x)]
    n = len(found)
    parent = list(range(n))

    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    if n > 1:
        prints = [_print_of(project_dir, x) for x in found]
        raw = np.stack([f[0] for f in prints])
        shape = np.array([f[1] for f in prints], np.float32)
        norm = raw - raw.mean(axis=1, keepdims=True)
        norm /= np.maximum(norm.std(axis=1, keepdims=True), 1e-6)
        for start in range(0, n, 256):  # correlations a block at a time; the close ones checked fully
            corr = norm[start:start + 256] @ norm.T / norm.shape[1]
            for r in range(corr.shape[0]):
                i = start + r
                cand = np.nonzero((corr[r] > CORR) & (np.abs(shape - shape[i]) < SHAPE))[0]
                cand = cand[cand > i]
                for j in cand[np.abs(raw[cand] - raw[i]).mean(axis=1) < NEAR]:
                    parent[root(int(j))] = root(i)
    # a page cut into pieces: pictures in the same chapters, next to each other in each (past blank pieces
    # and other left-out ones)
    pos = {x["id"]: (cid, k) for cid, pans in by_ch.items() for k, x in enumerate(pans)}
    where = {}  # picture -> {chapter: [positions]}
    for i in range(n):
        cid, k = pos[found[i]["id"]]
        where.setdefault(root(i), {}).setdefault(cid, []).append(k)

    def beside(a, b):
        for cid, mine in where[a].items():
            pans = by_ch[cid]
            if not any(all(pans[t].get("credits") for t in range(min(i, j) + 1, max(i, j)))
                       for i in mine for j in where[b][cid]):
                return False
        return True

    same_chapters = {}
    for r in where:
        same_chapters.setdefault(frozenset(where[r]), []).append(r)
    for rs in same_chapters.values():
        for x in range(len(rs)):
            for y in range(x + 1, len(rs)):
                if root(rs[x]) != root(rs[y]) and beside(rs[x], rs[y]):
                    parent[root(rs[x])] = root(rs[y])
    groups = {}
    for i in range(n):
        groups.setdefault(root(i), []).append(found[i])
    want = set(chapter_ids) if chapter_ids else None
    out = []
    for members in groups.values():
        if want is not None and not any(x["chapter"] in want for x in members):
            continue
        members.sort(key=lambda x: (order.get(x["chapter"], 0), pos[x["id"]][1]))
        chapters = len({x["chapter"] for x in members})
        said = next((m for m in (TEXT.search(_said(x)) for x in members) if m), None)
        if said:
            why = f'Says "{said.group(0).strip()}"'
        elif chapters > 1:
            why = "Repeats at the start or end of chapters"
        elif notice_only(by_ch.get(members[0]["chapter"], [])):
            why = "In a chapter that's only a notice"
        else:
            why = "Next to the scan group's pages"
        show = [x["id"] for x in members if x["chapter"] == members[0]["chapter"]]
        out.append({"ids": [x["id"] for x in members], "show": show, "chapters": chapters, "why": why,
                    "open": sum(unchecked(x) for x in members), "kept": sum(bool(x.get("enabled", True)) for x in members),
                    "first": order.get(members[0]["chapter"], 0)})
    out.sort(key=lambda k: (not k["open"], -len(k["ids"]), k["first"]))
    for k in out:
        del k["first"]
    return out


def choose(project: dict, ids, enabled: bool) -> list[dict]:
    """Use or leave out these pieces as you chose for one of them (its copies in other chapters): left
    out, they're marked as the scan group's pages; either way the choice stands when they're found again,
    and their copies in chapters added later follow it. Returns the pieces changed."""
    want, changed = set(ids), []
    for x in project["panels"]:
        if x["id"] in want:
            x["enabled"] = enabled
            if not enabled:
                x["credits"] = True
            if x.get("credits"):
                x["credits_kept"] = enabled
            changed.append(x)
    return changed


def ad_of(piece) -> bool:
    """The group's words in a piece's text."""
    return bool(TEXT.search(_said(piece)))


def apply(project: dict, found: dict, followed=()) -> int:
    """Leave the found pieces out (unticked, marked "credits"), first putting back the ones left out
    before in those chapters. A credit piece you ticked or unticked yourself (credits_kept) stays as
    you set it, and one found as a copy of a piece you left out (followed) counts as your choice too.
    Scan groups made mostly of left-out pieces are set aside. Returns how many pieces are left out now."""
    off = {i for ids in found.values() for i in ids}
    n = 0
    for x in project["panels"]:
        if x["chapter"] not in found or "credits_kept" in x:
            continue
        if x.get("credits"):
            x["enabled"] = True
            x.pop("credits", None)
        if x["id"] in off:
            x["enabled"], x["credits"] = False, True
            if x["id"] in followed:
                x["credits_kept"] = False
            n += 1
    left_out = {x["id"] for x in project["panels"] if x.get("credits")}
    for g in (project.get("scan") or {}).get("groups", []):
        members = [m[0] for m in g.get("members", [])]
        if (g.get("status") == "open" and g.get("why") != "reopened" and members
                and sum(m in left_out for m in members) > len(members) / 2):
            g["status"], g["why"] = "ignored", "credits"
    return n
