"""Panel detection with recursive XY-cuts along uniform gutters.

Works for manga/comic grids (rows and columns separated by gutters) and for
long webtoon strips (panels stacked vertically with background between them).
No trained model needed.
"""
import cv2
import numpy as np

STD_THRESH = 7.0      # a gutter line is (nearly) one flat color
BG_DIFF = 28.0        # ...and close to the region's border color


def imread(path) -> np.ndarray | None:
    """cv2.imread that also works with non-ASCII Windows paths."""
    try:
        data = np.fromfile(str(path), dtype=np.uint8)
    except OSError:
        return None
    return cv2.imdecode(data, cv2.IMREAD_COLOR) if data.size else None


def _gutter_runs(gray: np.ndarray, axis: int, bg: float, min_gap: int):
    """Return (start, end) runs of gutter lines across `axis` (0 = rows, 1 = cols)."""
    std = gray.std(axis=1 - axis)
    mean = gray.mean(axis=1 - axis)
    is_gap = (std < STD_THRESH) & (np.abs(mean - bg) < BG_DIFF)
    runs, start = [], None
    for i, g in enumerate(is_gap):
        if g and start is None:
            start = i
        elif not g and start is not None:
            if i - start >= min_gap:
                runs.append((start, i))
            start = None
    if start is not None and len(is_gap) - start >= min_gap:
        runs.append((start, len(is_gap)))
    return runs, is_gap


def _border_bg(gray: np.ndarray) -> float:
    b = max(2, min(gray.shape) // 100)
    edge = np.concatenate([gray[:b].ravel(), gray[-b:].ravel(), gray[:, :b].ravel(), gray[:, -b:].ravel()])
    return float(np.median(edge))


def _trim(gray, x, y, w, h, bg):
    """Shrink a box to drop flat background margins."""
    sub = gray[y:y + h, x:x + w]
    _, rows = _gutter_runs(sub, 0, bg, 1)
    _, cols = _gutter_runs(sub, 1, bg, 1)
    r = np.where(~rows)[0]
    c = np.where(~cols)[0]
    if len(r) == 0 or len(c) == 0:
        return None
    return x + int(c[0]), y + int(r[0]), int(c[-1] - c[0] + 1), int(r[-1] - r[0] + 1)


def _split(gray, box, bg, min_gap, rtl, depth=0):
    x, y, w, h = box
    if depth > 12:
        return [box]
    sub = gray[y:y + h, x:x + w]
    for axis in (0, 1):  # try horizontal gutters first, then vertical
        runs, _ = _gutter_runs(sub, axis, bg, min_gap)
        size = h if axis == 0 else w
        inner = [(a, b) for a, b in runs if a > 0 and b < size]
        if not inner:
            continue
        cuts, prev = [], 0
        for a, b in inner:
            cuts.append((prev, a))
            prev = b
        cuts.append((prev, size))
        if axis == 1 and rtl:
            cuts.reverse()
        out = []
        for a, b in cuts:
            if b - a < 4:
                continue
            child = (x, y + a, w, b - a) if axis == 0 else (x + a, y, b - a, h)
            t = _trim(gray, *child, bg)
            if t:
                out.extend(_split(gray, t, bg, min_gap, rtl, depth + 1))
        return out
    return [box]


def is_webtoon(sizes: list[tuple[int, int]]) -> bool:
    """Tall, same-width pages are slices of one long vertical strip."""
    if not sizes:
        return False
    ratios = sorted(h / w for w, h in sizes)
    widths = {w for w, _ in sizes}
    return ratios[len(ratios) // 2] > 1.8 and len(widths) <= max(1, len(sizes) // 10 + 1)


def detect_strip(images: list[np.ndarray], min_height_frac: float = 0.35, with_groups: bool = False):
    """Treat pages as one continuous vertical strip and cut it at flat rows.

    Gutters in webtoons are often gradients, so a gutter row only needs to be
    flat across its width. Fragments shorter than min_height_frac * width
    (floating bubbles, petals, sound effects) are merged into a neighbor.

    Returns (page_index, crop, group, y0, y1) in reading order, y0/y1 being the
    piece's rows in the stitched strip (pages scaled to the first page's width).
    The strip is cut finely,
    at every gutter and every band that is mostly one color (a gutter crossed by
    a speech bubble), so no panel boundary is missed; `group` numbers the panels
    the heuristics above would have made, and consecutive pieces with the same
    group are shown together until someone (Claude or the user) regroups them.
    """
    W = images[0].shape[1]
    pages = [im if im.shape[1] == W else cv2.resize(im, (W, int(im.shape[0] * W / im.shape[1])))
             for im in images]
    flat, mostly = [], []
    for im in pages:
        g = cv2.cvtColor(im, cv2.COLOR_BGR2GRAY).astype(np.float32)[:, ::2]
        flat.append(g.std(axis=1) < STD_THRESH)
        # share of each row that is one color (gutters crossed by a bubble or effect)
        med = np.median(g, axis=1, keepdims=True)
        mostly.append((np.abs(g - med) < 10).mean(axis=1) > 0.8)
    flat = np.concatenate(flat)
    mostly = np.concatenate(mostly)
    offsets = np.cumsum([0] + [im.shape[0] for im in pages])
    total = int(offsets[-1])

    min_gap = max(3, int(W * 0.01))
    segs, start, gap_start = [], None, None
    for y, f in enumerate(flat):
        if not f:
            if start is None:
                start = y
            gap_start = None
        else:
            if start is not None:
                if gap_start is None:
                    gap_start = y
                if y - gap_start >= min_gap:
                    segs.append([start, gap_start])
                    start, gap_start = None, None
    if start is not None:
        segs.append([start, gap_start if gap_start is not None else total])
    segs_raw = [tuple(s) for s in segs]

    min_h = int(W * min_height_frac)
    while len(segs) > 1:
        small = [i for i, (a, b) in enumerate(segs) if b - a < min_h]
        if not small:
            break
        i = min(small, key=lambda k: segs[k][1] - segs[k][0])
        gap_prev = segs[i][0] - segs[i - 1][1] if i > 0 else None
        gap_next = segs[i + 1][0] - segs[i][1] if i + 1 < len(segs) else None
        j = i - 1 if gap_next is None or (gap_prev is not None and gap_prev <= gap_next) else i + 1
        a, b = min(i, j), max(i, j)
        segs[a:b + 1] = [[segs[a][0], segs[b][1]]]

    # split very tall pieces at the longest mostly-empty band
    max_h = int(W * 2.2)

    def split_tall(a, b):
        if b - a <= max_h:
            return [[a, b]]
        best, run_start = None, None
        for y in range(a + min_h, b - min_h + 1):
            m = y < b - min_h and mostly[y]
            if m and run_start is None:
                run_start = y
            elif not m and run_start is not None:
                if y - run_start >= min_gap and (best is None or y - run_start > best[1] - best[0]):
                    best = (run_start, y)
                run_start = None
        if best is None:
            return [[a, b]]
        cut = (best[0] + best[1]) // 2
        return split_tall(a, cut) + split_tall(cut, b)

    raw = [list(s) for s in segs_raw]
    segs = [s for a, b in segs for s in split_tall(a, b)]

    # fine pieces inside each panel: cut at gutters that were merged over and at
    # mostly-one-color bands, keeping every piece at least min_piece tall
    min_piece = int(W * 0.15)
    pieces = []
    for gi, (a, b) in enumerate(segs):
        cands = []
        for (_, rb), (na, _) in zip(raw, raw[1:]):
            if a < rb and na < b:
                cands.append((rb + na) // 2)
        run_start = None
        for y in range(a, b + 1):
            m = y < b and mostly[y]
            if m and run_start is None:
                run_start = y
            elif not m and run_start is not None:
                if y - run_start >= min_gap:
                    cands.append((run_start + y) // 2)
                run_start = None
        cuts, last = [], a
        for c in sorted(set(cands)):
            if c - last >= min_piece and b - c >= min_piece:
                cuts.append(c)
                last = c
        bounds = [a] + cuts + [b]
        pieces.extend((bounds[k], bounds[k + 1], gi) for k in range(len(bounds) - 1))

    pad = 4

    def cut(a, b):
        """The strip between rows a and b as a crop, with flat margins trimmed off."""
        a, b = max(0, a - pad), min(total, b + pad)
        parts = []
        for k, im in enumerate(pages):
            lo, hi = max(a, offsets[k]), min(b, offsets[k + 1])
            if lo < hi:
                parts.append(im[lo - offsets[k]:hi - offsets[k]])
        crop = np.vstack(parts)
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY).astype(np.float32)
        t = _trim(gray, 0, 0, crop.shape[1], crop.shape[0], _border_bg(gray))
        if t:
            x, y, w, h = t
            crop = crop[max(0, y - pad):y + h + pad, max(0, x - pad):x + w + pad]
        return int(np.searchsorted(offsets, a, side="right") - 1), crop

    # each piece also keeps its rows in the strip (a, b), which the scrolling video uses
    crops = [(*cut(a, b), gi, a, b) for a, b, gi in pieces]
    if not with_groups:
        return crops
    # the whole detected panels, as earlier versions cut them (for matching old projects)
    groups = [(gi, a, b, cut(a, b)[1].shape[:2]) for gi, (a, b) in enumerate(segs)]
    return crops, groups


def detect(img_bgr: np.ndarray, min_panel_frac: float = 0.08, rtl: bool = False):
    """Return panel boxes (x, y, w, h) in reading order."""
    H, W = img_bgr.shape[:2]
    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY).astype(np.float32)
    # downscale big pages for speed, then map boxes back
    scale = min(1.0, 1200.0 / W)
    if scale < 1.0:
        gray = cv2.resize(gray, (int(W * scale), int(H * scale)), interpolation=cv2.INTER_AREA)
    h, w = gray.shape
    bg = _border_bg(gray)
    min_gap = max(3, int(w * 0.008))
    start = _trim(gray, 0, 0, w, h, bg) or (0, 0, w, h)
    boxes = _split(gray, start, bg, min_gap, rtl)

    min_side = max(40, int(w * min_panel_frac))
    keep = [b for b in boxes if b[2] >= min_side and b[3] >= min_side * 0.5]
    if not keep:
        keep = [(0, 0, w, h)]
    pad = 2
    result = []
    for bx, by, bw, bh in keep:
        X = max(0, int((bx - pad) / scale))
        Y = max(0, int((by - pad) / scale))
        X2 = min(W, int((bx + bw + pad) / scale))
        Y2 = min(H, int((by + bh + pad) / scale))
        result.append((X, Y, X2 - X, Y2 - Y))
    return result
