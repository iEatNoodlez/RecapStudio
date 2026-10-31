"""Runs inside the Magi add-on's own Python environment (with PyTorch), never in the app's.

    python magi_worker.py download            # fetch Magi v2 and its text-reader files, print progress
    python magi_worker.py check  --device cuda  # load the model once and run it on a blank page
    python magi_worker.py serve  --device cuda  # load once, then answer one JSON request per line

Requests (one JSON object per line on stdin):
    {"pages": [image paths], "bank": {"images": [image paths], "names": [names]}}
    {"quit": true}
Answers (one JSON object per line on stdout):
    {"ready": true, "device": ...} once loaded, then for each request
    {"pages": [[{"box": [x0, y0, x1, y1], "text": ..., "essential": bool, "speaker": name}, ...], ...]}
    or {"error": "..."}.
Only this file's standard-library, numpy, PIL, torch and transformers imports are available here.
"""
import argparse
import json
import os
import sys
import threading
import time
import traceback

REPO, REVISION = "ragavsachdeva/magiv2", "fbc890fec52977142e8ee00bfe26e9458b65517c"  # the reviewed commit
TROCR, TROCR_REVISION = "microsoft/trocr-base-printed", "93450be3f1ed40a930690d951ef3932687cc1892"


def say(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def folder_bytes(path):
    total = 0
    for root, _, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


def download():
    from huggingface_hub import snapshot_download

    hub = os.environ["HF_HOME"]
    done = threading.Event()

    def report():  # the hub's own progress bars aren't readable from another process
        while not done.wait(1.0):
            say({"progress": folder_bytes(hub)})

    threading.Thread(target=report, daemon=True).start()
    snapshot_download(REPO, revision=REVISION)
    snapshot_download(TROCR, revision=TROCR_REVISION, allow_patterns=["*.json", "*.txt"])  # tokenizer only
    done.set()
    say({"progress": folder_bytes(hub), "done": True})


def load(device):
    os.environ["HF_HUB_OFFLINE"] = "1"  # everything was fetched at install time
    import torch
    from transformers import AutoConfig, AutoModel
    from transformers import TrOCRProcessor

    config = AutoConfig.from_pretrained(REPO, revision=REVISION, trust_remote_code=True)
    config.detection_model_config.use_pretrained_backbone = False  # Magi's checkpoint holds the backbone
    # Magi's code loads this by name; point it at the pinned files fetched at install time
    from huggingface_hub import snapshot_download
    config.ocr_pretrained_processor_path = snapshot_download(TROCR, revision=TROCR_REVISION,
                                                             allow_patterns=["*.json", "*.txt"])
    TrOCRProcessor.from_pretrained(config.ocr_pretrained_processor_path)  # fail here, clearly, if missing
    model = AutoModel.from_pretrained(REPO, revision=REVISION, config=config, trust_remote_code=True)
    return model.to(device).eval(), torch


def read_image(path):  # as in Magi's own example: grayscale, back to three channels
    import numpy as np
    from PIL import Image
    with open(path, "rb") as f:
        return np.array(Image.open(f).convert("L").convert("RGB"))


def chapter(model, torch, pages, bank):
    images = [read_image(p) for p in pages]
    names = list(bank.get("names") or [])
    bank = {"images": [read_image(p) for p in bank.get("images") or []], "names": names}
    with torch.no_grad():
        results = model.do_chapter_wide_prediction(images, bank, use_tqdm=False, do_ocr=True)
    out = []
    for r in results:
        speaker = {ti: r["character_names"][ci] for ti, ci in r["text_character_associations"]}
        out.append([{"box": [round(float(v)) for v in r["texts"][j]], "text": text,
                     "essential": bool(r["is_essential_text"][j]), "speaker": speaker.get(j, "unsure")}
                    for j, text in enumerate(r["ocr"])])
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["download", "check", "serve"])
    ap.add_argument("--device", default="cpu")
    args = ap.parse_args()
    if args.mode == "download":
        return download()
    t = time.time()
    model, torch = load(args.device)
    if args.mode == "check":
        import numpy as np
        blank = np.full((1200, 900, 3), 255, np.uint8)
        with torch.no_grad():
            model.predict_detections_and_associations([blank])
        memory = torch.cuda.max_memory_allocated() / 2**30 if args.device == "cuda" else 0
        return say({"ok": True, "device": args.device, "seconds": round(time.time() - t), "gpu_gb": round(memory, 1)})
    say({"ready": True, "device": args.device, "seconds": round(time.time() - t)})
    for line in sys.stdin:
        if not line.strip():
            continue
        req = json.loads(line)
        if req.get("quit"):
            break
        try:
            say({"pages": chapter(model, torch, req["pages"], req.get("bank") or {})})
        except Exception as e:  # report and keep serving
            traceback.print_exc(file=sys.stderr)
            say({"error": f"{type(e).__name__}: {e}"})


if __name__ == "__main__":
    main()
