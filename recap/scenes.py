"""Scenes: runs of consecutive panel pieces shown and narrated together.

Panel detection cuts pages finely; a piece marked `joined` belongs to the scene
of the piece above it (within the same chapter). Each scene has one narration
line, stored on its first piece (the head); the video shows the scene's pieces
stacked back together.
"""
import cv2
import numpy as np


def groups(panels: list[dict]) -> list[list[dict]]:
    """Enabled panels (in project order) grouped into scenes; the head comes first."""
    out: list[list[dict]] = []
    for x in panels:
        if not x.get("enabled", True):
            continue
        if out and x.get("joined") and out[-1][0]["chapter"] == x["chapter"]:
            out[-1].append(x)
        else:
            out.append([x])
    return out


def all_groups(panels: list[dict]) -> list[list[dict]]:
    """Like groups(), but keeping pieces left out of the video in their scenes."""
    out: list[list[dict]] = []
    for x in panels:
        if out and x.get("joined") and out[-1][0]["chapter"] == x["chapter"]:
            out[-1].append(x)
        else:
            out.append([x])
    return out


def _edge_color(img: np.ndarray):
    edge = np.concatenate([img[:, :2].reshape(-1, 3), img[:, -2:].reshape(-1, 3)])
    return tuple(int(v) for v in np.median(edge, axis=0))


def stack(images: list[np.ndarray]) -> np.ndarray:
    """Put a scene's pieces back together top to bottom, centered, padding each
    piece with its own edge color so the joins don't show."""
    images = [im for im in images if im is not None]
    if len(images) == 1:
        return images[0]
    width = max(im.shape[1] for im in images)
    rows = []
    for im in images:
        extra = width - im.shape[1]
        if extra:
            left = extra // 2
            im = cv2.copyMakeBorder(im, 0, 0, left, extra - left, cv2.BORDER_CONSTANT, value=_edge_color(im))
        rows.append(im)
    return np.vstack(rows)
