"""The fonts captions, thumbnails and re-lettered bubbles are drawn in.

Windows has every one the app uses: Arial Black (captions), Impact (thumbnail titles), Segoe UI Black (thumbnail
labels and bubbles), Comic Sans (re-lettered speech bubbles) and Arial Bold (when one is missing). Linux has none of
them, so each look has stand-ins, tried in order:
- Microsoft's own fonts, when the member installed them (ttf-mscorefonts-installer on Ubuntu and Debian,
  msttcore-fonts on Fedora, ttf-ms-fonts on Arch). Videos then look as they do on Windows (no Segoe UI there).
- The open look-alikes, when the member downloaded them from Settings (DOWNLOADS: Anton, Archivo Black, Comic Neue
  and Noto Sans, all under the SIL Open Font License, 4.7 MB into the add-ons folder).
- What the distribution has: Noto Sans (Fedora, openSUSE, KDE), Ubuntu Sans and Ubuntu (Ubuntu), Cantarell
  (GNOME), Liberation Sans (most) and DejaVu Sans (almost every one).
Fonts are found with fontconfig (fc-list), or in the usual font folders without it. A variable font's named style
(Ubuntu Sans ExtraBold) is a font of its own here: its spec is "file|style". status() says which looks only have a
stand-in, so Settings can offer the download."""
import functools
import hashlib
import os
import shutil
import subprocess
import urllib.request
from pathlib import Path

from PIL import ImageFont

from . import paths

WINDOWS = os.name == "nt"
WINDOWS_FONTS = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
FOLDER = paths.ADDONS / "fonts"  # the downloaded look-alikes

# Each look: its name, Windows' file, then (family, style) to try elsewhere, best first; "then" is the look tried
# after those (Comic Sans falls back to Arial Bold, as on Windows)
LOOKS = {
    "arial-black": {"name": "Arial Black", "windows": "ariblk.ttf", "then": "arial-bold", "linux": [
        ("Arial Black", "Regular"), ("Archivo Black", "Regular"), ("Noto Sans", "Black"),
        ("Ubuntu Sans", "ExtraBold"), ("Ubuntu", "Bold"), ("Inter", "Black"), ("Cantarell", "Extra Bold"),
        ("Open Sans", "ExtraBold"), ("Liberation Sans", "Bold"), ("DejaVu Sans", "Bold")]},
    "impact": {"name": "Impact", "windows": "impact.ttf", "then": "arial-black", "linux": [
        ("Impact", "Regular"), ("Anton", "Regular"), ("Oswald", "Bold"), ("Ubuntu Sans", "Condensed ExtraBold"),
        ("Noto Sans", "Condensed Black"), ("Noto Sans", "ExtraCondensed Black"), ("Ubuntu", "Condensed Bold"),
        ("Liberation Sans Narrow", "Bold"), ("DejaVu Sans", "Condensed Bold")]},
    "segoe-black": {"name": "Segoe UI Black", "windows": "seguibl.ttf", "then": "arial-black", "linux": [
        ("Segoe UI Black", "Regular"), ("Noto Sans", "Black"), ("Ubuntu Sans", "ExtraBold"), ("Inter", "Black"),
        ("Cantarell", "Extra Bold"), ("Open Sans", "ExtraBold")]},
    "segoe-black-italic": {"name": "Segoe UI Black Italic", "windows": "seguibli.ttf", "then": "segoe-black",
                           "linux": [("Segoe UI Black", "Italic"), ("Noto Sans", "Black Italic"),
                                     ("Ubuntu Sans", "ExtraBold Italic"), ("Inter", "Black Italic"),
                                     ("Open Sans", "ExtraBold Italic"), ("DejaVu Sans", "Bold Oblique")]},
    "comic-bold": {"name": "Comic Sans Bold", "windows": "comicbd.ttf", "then": "arial-bold", "linux": [
        ("Comic Sans MS", "Bold"), ("Comic Neue", "Bold"), ("Comic Relief", "Bold")]},
    "arial-bold": {"name": "Arial Bold", "windows": "arialbd.ttf", "then": None, "linux": [
        ("Arial", "Bold"), ("Liberation Sans", "Bold"), ("Arimo", "Bold"), ("Noto Sans", "Bold"),
        ("Ubuntu Sans", "Bold"), ("Ubuntu", "Bold"), ("Cantarell", "Bold"), ("DejaVu Sans", "Bold"),
        ("FreeSans", "Bold")]},
}

# The open look-alikes, from google/fonts at a fixed commit, each checked against its SHA-256
SOURCE = "https://raw.githubusercontent.com/google/fonts/bbda7253fc363238b05d97515ca71f8ae49c4fa9/ofl/"
DOWNLOADS = {
    "Anton-Regular.ttf": ("anton/Anton-Regular.ttf", 170812,
                          "a4ba3a92350ebb031da0cb47630ac49eb265082ca1bc0450442f4a83ab947cab"),
    "ArchivoBlack-Regular.ttf": ("archivoblack/ArchivoBlack-Regular.ttf", 90988,
                                 "dd9a89a019b4849f66ab75455fe7bdf931311042cbb0f0f97acc061539703180"),
    "ComicNeue-Bold.ttf": ("comicneue/ComicNeue-Bold.ttf", 55716,
                           "3e7e5fccfd7e0788f317b43312151c1bd5cf058c9697a8d83eac3939050bd61e"),
    "NotoSans[wdth,wght].ttf": ("notosans/NotoSans%5Bwdth%2Cwght%5D.ttf", 2049096,
                                "bfb7bb691513f12e734dc346c03a03f784912432d7e3fa8e56efcf906fe86b3d"),
    "NotoSans-Italic[wdth,wght].ttf": ("notosans/NotoSans-Italic%5Bwdth%2Cwght%5D.ttf", 2322640,
                                       "58e6e0ebd1931b29a365aa2d3e2ee9a9e831a3af7cf3ad1462d4e72154f0b291"),
}
NOTICE = """These fonts were downloaded by Recap Studio from Google Fonts (github.com/google/fonts):
Anton, Archivo Black, Comic Neue and Noto Sans. Each is under the SIL Open Font License 1.1
(openfontlicense.org), and belongs to its authors. Recap Studio draws captions, thumbnails and speech
bubbles with them on Linux, which doesn't have Windows' Impact, Arial Black, Comic Sans or Segoe UI.
Deleting this folder is fine: Recap Studio then uses the fonts your system has.
"""
FOLDERS = ["/usr/share/fonts", "/usr/local/share/fonts", "~/.local/share/fonts", "~/.fonts"]


def _key(text: str) -> str:
    return "".join(text.lower().split())  # ("Extra Bold" and "ExtraBold" are the same style)


def _scan(files) -> list:
    """(family, style, file, named style or "") for font files, read with Pillow (a variable font: each named
    style)."""
    out = []
    for f in files:
        try:
            font = ImageFont.truetype(str(f), 12)
            family, style = font.getname()
        except (OSError, ValueError):
            continue
        out.append((family or "", style or "", str(f), ""))
        try:
            names = font.get_variation_names()
        except (OSError, ValueError, AttributeError):
            names = []
        for n in names:
            n = n.decode("utf-8", "replace") if isinstance(n, bytes) else str(n)
            out.append((family or "", n, str(f), n))
    return out


def _fontconfig() -> list | None:
    fc = shutil.which("fc-list")
    if not fc:
        return None
    try:
        r = subprocess.run([fc, "-f", "%{file}\t%{family}\t%{style}\t%{variable}\t%{namedinstance}\n"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20)
    except (OSError, subprocess.SubprocessError):
        return None
    out = []
    for line in r.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) < 5 or parts[3] == "True":  # (a variable font itself: its named styles come as lines too)
            continue
        file, families, styles, named = parts[0], parts[1].split(","), parts[2].split(","), parts[4] == "True"
        for family in families:
            for style in styles:
                out.append((family, style, file, style if named else ""))
    return out


@functools.lru_cache(maxsize=1)
def _faces() -> list:
    """Every font this computer has, the downloaded look-alikes first."""
    found = _scan(sorted(FOLDER.glob("*.ttf"))) if FOLDER.is_dir() else []
    system = _fontconfig()
    if system is None:
        files = []
        for folder in FOLDERS:
            root = Path(folder).expanduser()
            if root.is_dir():
                files += [p for p in root.rglob("*") if p.suffix.lower() in (".ttf", ".otf", ".ttc")]
        system = _scan(files)
    return found + system


@functools.lru_cache(maxsize=None)
def find(look: str) -> dict:
    """The font for a look: {"spec": "file" or "file|style", "name": what it is, "exact": whether it's the font
    the look is named after}. spec is "" when there's none at all (Pillow's own font is drawn then)."""
    want = LOOKS[look]
    if WINDOWS:
        f = WINDOWS_FONTS / want["windows"]
        if f.exists():
            return {"spec": str(f), "name": want["name"], "exact": True}
    else:
        faces = {}
        for family, style, file, named in _faces():
            faces.setdefault((_key(family), _key(style)), (family, style, file, named))
        for i, (family, style) in enumerate(want["linux"]):
            hit = faces.get((_key(family), _key(style)))
            if hit:
                spec = hit[2] + (f"|{hit[3]}" if hit[3] else "")
                # (Microsoft's own font is the first in each list)
                return {"spec": spec, "name": f"{hit[0]} {hit[1]}".replace(" Regular", ""), "exact": i == 0}
    if want["then"]:
        return {**find(want["then"]), "exact": False}
    return {"spec": "", "name": "Pillow's default font", "exact": False}


def spec(look: str) -> str:
    return find(look)["spec"]


def truetype(spec_: str, size: int) -> ImageFont.FreeTypeFont:
    """A font from a spec (find()), at this size; Pillow's default font when there's none."""
    file, _, named = (spec_ or "").partition("|")
    if not file:
        return ImageFont.load_default(size)
    font = ImageFont.truetype(file, size)
    if named:
        try:
            font.set_variation_by_name(named)
        except (OSError, ValueError):
            pass
    return font


def load(look: str, size: int) -> ImageFont.FreeTypeFont:
    try:
        return truetype(spec(look), size)
    except OSError:
        return ImageFont.load_default(size)


def signature(look: str) -> list:
    """What a look's font adds to a video's fingerprint: nothing on Windows (as before Linux), else the font used,
    so a video is drawn again when its font changes (and a project moved from Windows draws its own pieces)."""
    if WINDOWS:
        return []
    file, _, named = find(look)["spec"].partition("|")
    return [Path(file).name + (f"|{named}" if named else "")]


def downloaded() -> bool:
    return all((FOLDER / name).exists() for name in DOWNLOADS)


def status() -> dict:
    looks = [{"id": k, "name": v["name"], "font": find(k)["name"], "exact": find(k)["exact"]} for k, v in LOOKS.items()]
    return {"looks": looks, "downloaded": downloaded(), "folder": str(FOLDER),
            "size_mb": round(sum(d[1] for d in DOWNLOADS.values()) / 1e6, 1),
            # (Microsoft's fonts beat the look-alikes; Windows has them all)
            "offered": not WINDOWS and not downloaded() and not all(x["exact"] for x in looks)}


def download() -> dict:
    """Download the open look-alikes into the add-ons folder, each checked against its SHA-256."""
    FOLDER.mkdir(parents=True, exist_ok=True)
    for name, (path, size, sha) in DOWNLOADS.items():
        target = FOLDER / name
        if target.exists() and hashlib.sha256(target.read_bytes()).hexdigest() == sha:
            continue
        req = urllib.request.Request(SOURCE + path, headers={"User-Agent": "RecapStudio"})
        with urllib.request.urlopen(req, timeout=60) as r:
            data = r.read(size + 1)
        if hashlib.sha256(data).hexdigest() != sha:
            raise RuntimeError(f"{name} doesn't match its checksum, so it wasn't kept.")
        part = target.with_name(name + ".part")
        part.write_bytes(data)
        part.replace(target)
    (FOLDER / "README.txt").write_text(NOTICE, encoding="utf-8")
    forget()
    return status()


def forget():
    """Look again (after a download, or fonts installed while the app runs)."""
    _faces.cache_clear()
    find.cache_clear()
