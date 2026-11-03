"""Reading text (ocr.py) on made-up pictures, with the built-in reader:
- the readers don't turn over lines they take for upside down (that read "NO" as "ON"), nor a plugin language's
  unless it asks to;
- a tall webtoon piece is read in overlapping chunks: a line a chunk's edge cuts through is read once, whole (it was
  read twice, its top half as other words: "DE CIEDTA FODMA FORMA"), and a line too tall for the overlap still read;
- a plugin's language puts right what its reader always gets wrong (ocr.put_right, plugin.language's fix), and a
  fix that fails leaves the text as it was;
- the scan group's pages (credits.py) found in a raw's own text before Translate when it's in the Latin alphabet,
  Spanish groups' words included, and not in a Korean one's; and the job's note on them says only the latest count
  (Read text looks again)."""
import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from helpers import App, check, data_folder, done

app = App(data_folder())
from recap import ocr  # noqa: E402

W = 720
EDGE_AT = max(W * 2, 1600)  # where read_lines' first chunk ends


def strip(lines, height=2600):
    """A white webtoon piece W wide with lines of black text: (top, words, size)."""
    img = Image.new("RGB", (W, height), "white")
    draw = ImageDraw.Draw(img)
    for top, words, size in lines:
        draw.text((40, top), words, fill="black", font=ImageFont.truetype("arialbd.ttf", size))
    return cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)


found = ocr.read_lines(strip([(300, "HOLA, BUENOS DIAS", 40), (EDGE_AT - 22, "DE CIERTA FORMA", 40),
                              (2300, "HASTA MAÑANA", 40)]), 0.2, use_gpu=False)
texts = [t for *_, t in found]
across = [t for x0, y0, x1, y1, t in found if y0 < EDGE_AT < y1 + 30]
check("a line across a chunk's edge: read once, whole", len(across) == 1 and "CIERTA" in across[0]
      and "FORMA" in across[0], texts)
check("the lines away from the edge read as before", any("BUENOS" in t for t in texts) and any("HASTA" in t for t in texts),
      texts)
check("nothing read twice", len(found) == len({(round(y0, -1), t) for x0, y0, x1, y1, t in found}), found)
big = ocr.read_lines(strip([(EDGE_AT - 150, "BOOM", 260)]), 0.2, use_gpu=False)
check("a line too tall for the overlap is still read", any("BOOM" in t or "BOO" in t for *_, t in big), big)
check("the reader doesn't turn lines it thinks are upside down (\"NO\" read as \"ON\")",
      ocr._get(False, "built-in").use_cls is False and ocr._model_args("built-in")["use_cls"] is False)
ocr.READERS["zz"] = {"args": lambda: {"rec_model_path": "x"}, "ready": None, "fix": None}
ocr.READERS["zy"] = {"args": lambda: {"use_cls": True}, "ready": None, "fix": None}
check("nor a plugin language's, unless it asks to", ocr._model_args("lang:zz")["use_cls"] is False
      and ocr._model_args("lang:zy")["use_cls"] is True)

ocr.READERS["xx"] = {"args": None, "ready": None, "fix": lambda t: t.replace("0N", "ON")}
ocr.READERS["yy"] = {"args": None, "ready": None, "fix": lambda t: 1 / 0}
check("a language's fix puts the text right", [b["text"] for b in ocr.put_right([{"text": "0N TIME"}], "xx")] == ["ON TIME"])
check("one that fails leaves it as it was", [b["text"] for b in ocr.put_right([{"text": "0N TIME"}], "yy")] == ["0N TIME"])
check("a language without one: as read", [b["text"] for b in ocr.put_right([{"text": "0N"}], "en")] == ["0N"])

from recap import credits  # noqa: E402
spanish = {"text": "", "source_text": "TRADUCCIÓN STAFF\nREDRAW STAFF\nHTTPS://DISCORD.GG/ABC"}
korean = {"text": "", "source_text": "번역 디스코드 discord"}
check("the scan group's page check reads a raw's own text in the Latin alphabet, before Translate",
      credits.ad_of(spanish) and credits._said(spanish) == spanish["source_text"], credits._said(spanish))
check("not a Korean, Chinese or Japanese one's (it waits for the English)", credits._said(korean) == "")
check("the English, once there, first", credits._said({"text": "Translation: staff", "source_text": "x"}) == "Translation: staff")
check("Spanish scan groups' words", all(credits.ad_of({"text": t}) for t in (
    "ESTAMOS RECLUTANDO TRADUCTORES", "ABANDONADO. Lee antes en: templescan", "TRADUCCIÓN: STAFF")))
check("not a story's words", not any(credits.ad_of({"text": t}) for t in (
    "¡VAMOS A RECLUTAR GENTE!", "LEE ANTES DE FIRMAR", "¿QUÉ ESTÁS LEYENDO?")))
check("CONTINUARÁ is the series' own card", credits.series_card("CONTINUARÁ.") and credits.series_card("Continuara"))

# the job's note on them: Read text looks again and counts what's left out now, so its note replaces the first's
from recap import pipeline, store  # noqa: E402
real = store.load, store.update, store.project_dir, credits.find, credits.apply
counts = iter([44, 40, 0])
store.load, store.update, store.project_dir = (lambda pid: {"settings": {}}), (lambda pid, fn: fn({})), str
credits.find, credits.apply = (lambda *a, **k: {"c1": ["x"]}), (lambda *a: next(counts))
job = {"cancel": False, "note": "Added 1 chapter"}
notes = []
for _ in range(3):
    pipeline._leave_out_credits(job, "p", ["c1"])
    notes.append(job["note"])
store.load, store.update, store.project_dir, credits.find, credits.apply = real
check("a second look at the scan group's pages replaces the first's note", notes[:2] == [
    "Added 1 chapter. Left out 44 pieces at the start or end of 1 chapter (the scan group's credits, notices and ads)",
    "Added 1 chapter. Left out 40 pieces at the start or end of 1 chapter (the scan group's credits, notices and ads)"],
      notes)
check("and one that finds none takes it away", notes[2] == "Added 1 chapter", notes[2])
done()
