"""Character scan: find the characters in chosen chapters, group each one's appearances, and
suggest appearance traits, for the user to name and add to the character list.

An optional download from inside the app (about 586 MB into data\\addons\\charscan), never shipped:
- DeepGHS anime person and head detectors (MIT) find characters; only people with a head in view
  are kept, which drops body-only crops and stray hits on speech bubbles.
- CCIP (DeepGHS, OpenRAIL) turns each character into a vector; two pictures differ by
  (1 - cosine) / 2. Groups are built by average linkage: a picture joins a group only if it is
  close to the group as a whole. (Single links, as DBSCAN uses, chained a whole series' cast into
  one group, since one artist draws everyone alike.)
- WD tagger v3 (SmilingWolf, Apache 2.0) suggests traits. Only hair, eyes, glasses, accessories,
  clothing and build tags are used, never explicit ones, and snapshots shown for review are the
  ones its content rating marks as not explicit, when there are any.
Everything runs on ONNX Runtime, on the GPU when available, like text reading.
"""
import csv
import hashlib
import json
import re
import shutil
import threading
import time
import urllib.request
import uuid

import cv2
import numpy as np
import onnxruntime as ort

from . import gpu, panels, store

ADDON_DIR = store.ADDONS_DIR / "charscan"
STATE_FILE = ADDON_DIR / "installed.json"
URL = "https://huggingface.co/{repo}/resolve/{rev}/{path}"
# local name: (repository, pinned revision, file, bytes, sha256)
FILES = {
    "person.onnx": ("deepghs/anime_person_detection", "e39c744c22432ad01f91dd254fe2b02c8d878b8c",
                    "person_detect_v1.1_s/model.onnx", 44_583_231,
                    "02972c0401cd77e1a5f05eee480d9e6455e14fafd8df50f4b80ecaf47263f1de"),
    "head.onnx": ("deepghs/anime_head_detection", "06604feee81983792a57c21081e539c0ae229833",
                  "head_detect_v2.0_n/model.onnx", 12_104_715,
                  "69b069d15c9b97e0d7e6db33ac1c3fd480b1be67b078922c29beacfac81ad9ac"),
    "ccip.onnx": ("deepghs/ccip_onnx", "eb2acdd29af1703388d3d0c04221add322bc9110",
                  "ccip-caformer-24-randaug-pruned/model_feat.onnx", 150_248_245,
                  "4ea118d16496274f4f6e08d3afc768cc592389e8f7f32f8732ce2215c228ac5f"),
    "tagger.onnx": ("SmilingWolf/wd-vit-tagger-v3", "7f6b584d0bd3f55c4531f14ba3d4761b2bccdc0f",
                    "model.onnx", 378_536_310,
                    "35f23693620b668f4d53fd3c62bf65e40af739bc52c7eb0fbc49258b58d065b6"),
    "tags.csv": ("SmilingWolf/wd-vit-tagger-v3", "7f6b584d0bd3f55c4531f14ba3d4761b2bccdc0f",
                 "selected_tags.csv", 308_468,
                 "298633d94d0031d2081c0893f29c82eab7f0df00b08483ba8f29d1e979441217"),
}
TOTAL_BYTES = sum(f[3] for f in FILES.values())
CREDITS = ("Anime person and head detectors and CCIP by DeepGHS (MIT and OpenRAIL licenses), "
           "WD tagger v3 by SmilingWolf (Apache 2.0). All allow commercial use.")

PERSON_CONF, HEAD_CONF = 0.384, 0.368  # each detector's own best-F1 cut-off (its threshold.json)
SAME = 0.21    # average difference under which pictures count as one character. CCIP's own cut-off is
               # 0.178; on a colour webtoon 0.21 also joined each character's close-ups and back
               # views to their main group without mixing two characters.
MAYBE = 0.26   # close enough to suggest "same as …" and let the user decide
MIN_SIDE = 64  # smaller crops are too small to recognise anyone
MAX_POINTS = 2500  # above this, near-identical pictures are pre-grouped first to keep grouping fast
SNAPSHOTS = 6

_status = {"state": "idle", "progress": 0.0, "message": "", "error": None}
_lock = threading.Lock()
_cancel = threading.Event()
_in_use = 0


class Cancelled(Exception):
    pass


# ---- download (on request; nothing of this is shipped) --------------------------------------

def installed() -> bool:
    return STATE_FILE.exists() and all((ADDON_DIR / n).exists() for n in FILES)


def status() -> dict:
    return {**_status, "installed": installed(), "download_mb": round(TOTAL_BYTES / 1e6),
            "credits": CREDITS, "in_use": _in_use > 0}


def install() -> dict:
    with _lock:
        if _status["state"] == "installing":
            raise ValueError("The models are already downloading")
        if installed():
            raise ValueError("The models are already installed")
        _cancel.clear()
        _status.update(state="installing", progress=0.0, message="Starting", error=None)
    threading.Thread(target=_download, daemon=True, name="charscan-download").start()
    return status()


def cancel_install():
    if _status["state"] == "installing":
        _cancel.set()


def _download():
    tmp = ADDON_DIR / "partial"
    done = 0
    try:
        shutil.rmtree(tmp, ignore_errors=True)
        tmp.mkdir(parents=True)
        for name, (repo, rev, path, size, sha) in FILES.items():
            digest, got = hashlib.sha256(), 0
            req = urllib.request.Request(URL.format(repo=repo, rev=rev, path=path), headers={"User-Agent": "RecapStudio"})
            with urllib.request.urlopen(req, timeout=60) as r, open(tmp / name, "wb") as f:
                while chunk := r.read(1 << 20):
                    if _cancel.is_set():
                        raise Cancelled()
                    f.write(chunk)
                    digest.update(chunk)
                    got += len(chunk)
                    _status.update(progress=(done + got) / TOTAL_BYTES,
                                   message=f"{(done + got) / 1e6:.0f} of {TOTAL_BYTES / 1e6:.0f} MB")
            if got != size or digest.hexdigest() != sha:
                raise RuntimeError(f"{path} from {repo} didn't download correctly (its size or checksum is wrong)")
            done += size
        for name in FILES:
            (tmp / name).replace(ADDON_DIR / name)
        tmp.rmdir()
        STATE_FILE.write_text(json.dumps({"installed_at": time.time(), "files": list(FILES)}), encoding="utf-8")
        _status.update(state="idle", progress=1.0, message="Installed")
    except Cancelled:
        shutil.rmtree(tmp, ignore_errors=True)
        _status.update(state="idle", progress=0.0, message="Download cancelled; nothing was kept")
    except Exception as e:  # report it in the app; keep nothing half-downloaded
        shutil.rmtree(tmp, ignore_errors=True)
        _status.update(state="error", progress=0.0, message="The download failed", error=str(e))


def remove() -> dict:
    if _status["state"] == "installing":
        raise ValueError("Cancel the download first")
    if _in_use:
        raise ValueError("A scan is running")
    shutil.rmtree(ADDON_DIR, ignore_errors=True)
    _status.update(state="idle", progress=0.0, message="Removed", error=None)
    return status()


# ---- the models ---------------------------------------------------------------------------

def _nms(boxes, scores, thr):
    order, keep = scores.argsort()[::-1], []
    area = (boxes[:, 2] - boxes[:, 0]) * (boxes[:, 3] - boxes[:, 1])
    while len(order):
        i, rest = order[0], order[1:]
        keep.append(i)
        w = np.clip(np.minimum(boxes[i, 2], boxes[rest, 2]) - np.maximum(boxes[i, 0], boxes[rest, 0]), 0, None)
        h = np.clip(np.minimum(boxes[i, 3], boxes[rest, 3]) - np.maximum(boxes[i, 1], boxes[rest, 1]), 0, None)
        order = rest[w * h / (area[i] + area[rest] - w * h + 1e-9) <= thr]
    return np.array(keep, int)


class _Detector:
    """A DeepGHS YOLOv8 export: the image is resized to the model's size, RGB / 255, NCHW."""

    def __init__(self, session, conf, iou):
        self.s, self.conf, self.iou = session, conf, iou
        meta = session.get_modelmeta().custom_metadata_map
        self.size = tuple(json.loads(meta["imgsz"])) if "imgsz" in meta else (640, 640)
        self.input = session.get_inputs()[0].name

    def _run(self, rgb):
        h, w = rgb.shape[:2]
        th, tw = self.size
        x = cv2.resize(rgb, (tw, th), interpolation=cv2.INTER_LINEAR).astype(np.float32) / 255.0
        out = self.s.run(None, {self.input: x.transpose(2, 0, 1)[None]})[0][0]
        scores = out[4:].max(axis=0)
        keep = scores > self.conf
        if not keep.any():
            return np.zeros((0, 5), np.float32)
        cx, cy, bw, bh = out[:4, keep]
        boxes = np.stack([cx - bw / 2, cy - bh / 2, cx + bw / 2, cy + bh / 2], 1) * [w / tw, h / th, w / tw, h / th]
        found = np.column_stack([boxes, scores[keep]]).astype(np.float32)
        return found[_nms(found[:, :4], found[:, 4], self.iou)]

    def __call__(self, rgb):
        """Tall webtoon pieces are run in overlapping windows (squashing them would hide faces)."""
        h, w = rgb.shape[:2]
        win = int(w * 1.25)
        if h <= win:
            return self._run(rgb)
        parts = []
        for y in range(0, h - win // 4, int(win * 0.7)):
            y0 = min(y, h - win)
            found = self._run(rgb[y0:y0 + win])
            found[:, [1, 3]] += y0
            parts.append(found)
        found = np.concatenate(parts)
        return found[_nms(found[:, :4], found[:, 4], self.iou)] if len(found) else found


CLIP_MEAN = np.array([0.48145466, 0.4578275, 0.40821073], np.float32)
CLIP_STD = np.array([0.26862954, 0.26130258, 0.27577711], np.float32)


class Models:
    def __init__(self, use_gpu: bool):
        providers = gpu.providers(use_gpu)

        def load(name):
            return ort.InferenceSession(str(ADDON_DIR / name), providers=providers)
        self.person = _Detector(load("person.onnx"), PERSON_CONF, 0.5)
        self.head = _Detector(load("head.onnx"), HEAD_CONF, 0.7)
        self.ccip = load("ccip.onnx")
        self.tagger = load("tagger.onnx")
        self.tagger_input = self.tagger.get_inputs()[0].name
        with open(ADDON_DIR / "tags.csv", encoding="utf-8") as f:
            self.tags = list(csv.DictReader(f))
        self.rating = [k for k, r in enumerate(self.tags) if r["category"] == "9"]  # general, sensitive, questionable, explicit

    def characters(self, rgb) -> list[list[int]]:
        """Boxes [x0, y0, x1, y1] of the characters in a picture whose head is in view."""
        h, w = rgb.shape[:2]
        heads = self.head(rgb)
        out = []
        for x0, y0, x1, y1, _ in self.person(rgb):
            if not any(x0 <= (a + c) / 2 <= x1 and y0 <= (b + d) / 2 <= y1 for a, b, c, d, _ in heads):
                continue
            box = [int(max(0, x0)), int(max(0, y0)), int(min(w, x1)), int(min(h, y1))]
            if min(box[2] - box[0], box[3] - box[1]) >= MIN_SIDE:
                out.append(box)
        return out

    def features(self, crops) -> np.ndarray:
        """Unit-length CCIP vectors: RGB 384 x 384, CLIP normalisation."""
        out = []
        for i in range(0, len(crops), 32):
            batch = np.stack([((cv2.resize(c, (384, 384), interpolation=cv2.INTER_LINEAR).astype(np.float32) / 255.0
                                - CLIP_MEAN) / CLIP_STD).transpose(2, 0, 1) for c in crops[i:i + 32]])
            out.append(self.ccip.run(["output"], {"input": batch})[0])
        f = np.concatenate(out).astype(np.float32)
        return f / np.linalg.norm(f, axis=1, keepdims=True)

    def tag(self, rgb) -> np.ndarray:
        """WD tagger probabilities: padded to a white square, 448 x 448, BGR, 0-255, NHWC."""
        h, w = rgb.shape[:2]
        m = max(h, w)
        sq = np.full((m, m, 3), 255, np.uint8)
        sq[(m - h) // 2:(m - h) // 2 + h, (m - w) // 2:(m - w) // 2 + w] = rgb
        x = cv2.resize(sq, (448, 448), interpolation=cv2.INTER_CUBIC)[:, :, ::-1].astype(np.float32)[None]
        return self.tagger.run(None, {self.tagger_input: x})[0][0]


# ---- grouping -------------------------------------------------------------------------------

def difference(a_sum, a_n, b_sum, b_n) -> np.ndarray:
    """Average CCIP difference between all pictures of A and all of B, from their vector sums
    (unit vectors, so the mean cosine is sum_a . sum_b / (n_a n_b))."""
    return (1 - (a_sum @ b_sum.T) / np.outer(a_n, b_n)) / 2


def group(features: np.ndarray, order: np.ndarray) -> list[np.ndarray]:
    """Average-linkage groups (indices, biggest first), joining while the average difference < SAME."""
    n = len(features)
    if n > MAX_POINTS:  # pre-group near-identical pictures (a tenth of the way to SAME), best first
        leaders, members = [], []
        for i in order:
            if leaders:
                d = (1 - np.stack(leaders) @ features[i]) / 2
                j = int(np.argmin(d))
                if d[j] < SAME / 2:
                    members[j].append(i)
                    continue
            leaders.append(features[i])
            members.append([i])
        clusters = [np.array(m) for m in members]
    else:
        clusters = [np.array([i]) for i in range(n)]
    sums = np.stack([features[c].sum(0) for c in clusters]).astype(np.float64)
    sizes = np.array([len(c) for c in clusters], np.float64)
    D = difference(sums, sizes, sums, sizes)
    np.fill_diagonal(D, np.inf)
    m = len(clusters)
    while m > 1:
        i, j = divmod(int(np.argmin(D)), len(D))
        if D[i, j] >= SAME:
            break
        merged = (sizes[i] * D[i] + sizes[j] * D[j]) / (sizes[i] + sizes[j])  # exact for average linkage
        D[i], D[:, i] = merged, merged
        D[i, i] = np.inf
        D[j], D[:, j] = np.inf, np.inf
        sizes[i] += sizes[j]
        clusters[i] = np.concatenate([clusters[i], clusters[j]])
        clusters[j] = np.array([], int)
        m -= 1
    return sorted([c for c in clusters if len(c)], key=len, reverse=True)


# ---- traits ---------------------------------------------------------------------------------

_COLORS = ("blonde|brown|black|red|white|grey|pink|blue|purple|green|orange|silver|light_brown|dark_blue|"
           "aqua|multicolored|two-tone|yellow")
TRAITS = [  # (kind, pattern); only these tags are ever suggested
    ("hair_color", rf"^({_COLORS})_hair$"),
    ("hair_length", r"^(short|medium|long|very_long)_hair$"),
    ("hair_style", r"^(hair_bun|single_hair_bun|double_bun|ponytail|twintails|braid|bob_cut|messy_hair)$"),
    ("eyes", rf"^({_COLORS})_eyes$"),
    ("face", r"^(glasses|beard|mustache|stubble|freckles|mole_under_eye)$"),
    ("accessory", r"^(headphones|earrings|hairclip|hair_ribbon|necklace|hat|cap|scarf)$"),
    ("clothing", rf"^((({_COLORS})_)?(shirt|t-shirt|hoodie|jacket|dress|skirt|sweater|cardigan|pants|shorts|"
                 rf"apron|coat|vest|necktie|suit|jeans|school_uniform|collared_shirt))$"),
    ("build", r"^(petite|tall|muscular|plump|child|old_man|old_woman)$"),
]
EYEWEAR = re.compile(r"-framed_eyewear$|^round_eyewear$|^semi-rimless_eyewear$")


def traits_from(models: Models, probs: np.ndarray, cutoff: float = 0.35) -> list[dict]:
    out, seen = [], set()
    for k, p in sorted(enumerate(probs), key=lambda kp: -kp[1]):
        if p < cutoff:
            break
        row = models.tags[k]
        if row["category"] != "0":
            continue
        name = "glasses" if EYEWEAR.search(row["name"]) else row["name"]
        kind = next((kind for kind, pat in TRAITS if re.match(pat, name)), None)
        if kind and name not in seen and name != "shirt":  # a bare "shirt" says nothing
            seen.add(name)
            out.append({"tag": name, "label": name.replace("_", " "), "kind": kind})
    return out


# ---- the scan -------------------------------------------------------------------------------

def scan(project: dict, chapter_ids: list[str], use_gpu: bool, check, report) -> dict:
    """Scan the chapters' pieces. check() is called between pieces (pause / cancel);
    report(fraction, message) gives progress. Returns the scan record for project["scan"]."""
    global _in_use
    if not installed():
        raise RuntimeError("Download the character scan models first (the Characters tab → Find characters)")
    _in_use += 1
    try:
        return _scan(project, chapter_ids, use_gpu, check, report)
    finally:
        _in_use -= 1


def _scan(project, chapter_ids, use_gpu, check, report):
    d = store.project_dir(project["id"])
    names = {c["id"]: c["name"] for c in project["chapters"]}
    pieces = [x for x in project["panels"] if x["chapter"] in chapter_ids and x.get("enabled", True)]  # (not credits)
    report(0.0, "Scanning for characters: loading the models")
    models = Models(use_gpu)
    dets, feats = [], []
    for k, x in enumerate(pieces):
        check()
        img = panels.imread(d / "panels" / x["file"])
        if img is not None:
            rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
            boxes = models.characters(rgb)
            if boxes:
                feats.append(models.features([rgb[b[1]:b[3], b[0]:b[2]] for b in boxes]))
                dets += [{"piece": x["id"], "file": x["file"], "chapter": x["chapter"], "box": b} for b in boxes]
        report(0.85 * (k + 1) / max(1, len(pieces)),
               f"Scanning for characters: panel {k + 1}/{len(pieces)}, {len(dets)} characters seen")
    sid = uuid.uuid4().hex[:8]
    record = {"id": sid, "created": time.time(), "chapters": chapter_ids, "groups": [], "seen": len(dets)}
    if not dets:
        return record
    F = np.concatenate(feats)
    size = np.array([min(b["box"][2] - b["box"][0], b["box"][3] - b["box"][1]) for b in dets])
    report(0.87, f"Scanning for characters: grouping {len(dets)} appearances")
    groups = group(F, np.argsort(-size))
    # existing characters with pictures, to recognise them again
    known = _known_characters(project, models)
    out_dir = d / "scan"
    out_dir.mkdir(exist_ok=True)
    sums = []
    for gi, g in enumerate(groups):
        check()
        report(0.9 + 0.1 * gi / len(groups), f"Scanning for characters: describing group {gi + 1}/{len(groups)}")
        best = g[np.argsort(-size[g])][:12]  # the biggest, clearest views
        probs, safe = [], []
        for i in best:
            x0, y0, x1, y1 = dets[i]["box"]
            img = panels.imread(d / "panels" / dets[i]["file"])
            crop = cv2.cvtColor(img[y0:y1, x0:x1], cv2.COLOR_BGR2RGB)
            pr = models.tag(crop)
            probs.append(pr)
            safe.append(pr[models.rating[0]] + pr[models.rating[1]] > 0.5)
        mean = np.mean(probs, axis=0)
        # snapshots: not explicit first (by the tagger's content rating), then the largest
        picks = [best[k] for k in sorted(range(len(best)), key=lambda k: (not safe[k], k))][:SNAPSHOTS]
        snaps = []
        for k, i in enumerate(picks):
            x0, y0, x1, y1 = dets[i]["box"]
            img = panels.imread(d / "panels" / dets[i]["file"])[y0:y1, x0:x1]
            s = min(1.0, 320 / img.shape[0])
            name = f"{sid}_{gi}_{k}.jpg"
            cv2.imwrite(str(out_dir / name), cv2.resize(img, (max(1, int(img.shape[1] * s)), max(1, int(img.shape[0] * s))),
                                                        interpolation=cv2.INTER_AREA), [cv2.IMWRITE_JPEG_QUALITY, 88])
            snaps.append({"file": f"scan/{name}", "piece": dets[i]["piece"], "box": dets[i]["box"]})
        girl, boy = (mean[[r["name"] for r in models.tags].index(t)] for t in ("1girl", "1boy"))
        sums.append(F[g].sum(0))
        record["groups"].append({
            "id": f"g{gi + 1}", "size": int(len(g)), "pieces": len({dets[i]["piece"] for i in g}),
            "chapters": [names[c] for c in chapter_ids if any(dets[i]["chapter"] == c for i in g)],
            "gender": "female" if girl > max(boy, 0.5) else "male" if boy > max(girl, 0.5) else "",
            "traits": traits_from(models, mean), "snapshots": snaps, "status": "open",
            "members": [[dets[i]["piece"], *dets[i]["box"]] for i in g],
        })
    sums = np.stack(sums)
    np.save(out_dir / f"{sid}_sums.npy", sums)
    _suggest(record, sums, known)
    return record


def _known_characters(project, models):
    """(character, look or None, picture vectors) for each named character, and each of its other
    looks, that has pictures. A look is matched on its own: an alter ego looks like someone else."""
    folder = store.project_dir(project["id"]) / "characters"
    out = []
    for c in project["characters"]:
        if not c.get("name", "").strip():
            continue
        for look in [None, *(c.get("looks") or [])]:
            crops = []
            for n in (look or c).get("pictures") or []:
                img = panels.imread(folder / n)
                if img is not None:
                    crops.append(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
            if crops:
                out.append((c, look, models.features(crops)))
    return out


def _suggest(record, sums, known=None):
    """For each group: other groups that may be the same character, and (when known is given)
    the existing character it looks like."""
    groups = record["groups"]
    n = np.array([g["size"] for g in groups], np.float64)
    D = difference(sums, n, sums, n)
    np.fill_diagonal(D, np.inf)
    for k, g in enumerate(groups):
        near = [j for j in np.argsort(D[k]) if D[k, j] < MAYBE][:3]
        g["similar"] = [{"group": groups[j]["id"], "diff": round(float(D[k, j]), 3)} for j in near]
        if known is None:
            continue
        g["match"] = None
        if known:
            # the group's average difference to each picture, then the median over a character's
            # pictures, so a few wrongly added pictures don't hide them
            dk = [float(np.median((1 - feats @ sums[k] / g["size"]) / 2)) for _, _, feats in known]
            j = int(np.argmin(dk))
            if dk[j] < MAYBE:
                c, look, _ = known[j]
                g["match"] = {"character": c["id"], "name": c["name"], "diff": round(dk[j], 3), "sure": dk[j] < SAME,
                              "look": look["id"] if look else None, "look_label": (look or {}).get("label", "")}


def merge_groups(project: dict, source: str, target: str) -> None:
    """Fold group `source` into `target` (the user says they're the same character)."""
    rec = project.get("scan") or {}
    gs = {g["id"]: g for g in rec.get("groups", [])}
    if source not in gs or target not in gs or source == target:
        raise ValueError("Unknown group")
    a, b = gs[source], gs[target]
    b["members"] += a["members"]
    b["size"] += a["size"]
    b["pieces"] = len({m[0] for m in b["members"]})
    b["chapters"] = [c for c in dict.fromkeys(b["chapters"] + a["chapters"])]
    b["snapshots"] = (b["snapshots"] + a["snapshots"])[:SNAPSHOTS + 2]
    # traits stay the target's: the app folds the smaller group into the bigger, whose traits come
    # from more views (a small group's are often off, e.g. a character in a dark scene)
    a["status"], a["merged_into"] = "merged", target
    path = store.project_dir(project["id"]) / "scan" / f"{rec['id']}_sums.npy"
    if path.exists():  # keep "same as" suggestions right for what's left
        sums = np.load(path)
        idx = {g["id"]: k for k, g in enumerate(rec["groups"])}
        sums[idx[target]] += sums[idx[source]]
        np.save(path, sums)
        live = [k for k, g in enumerate(rec["groups"]) if g["status"] != "merged"]
        _suggest({"groups": [rec["groups"][k] for k in live]}, sums[live])


def clear(project: dict) -> None:
    """Delete the last scan's results and snapshot pictures."""
    folder = store.project_dir(project["id"]) / "scan"
    shutil.rmtree(folder, ignore_errors=True)
    project.pop("scan", None)
