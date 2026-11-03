"""A made-up comic site on 127.0.0.1, shaped like the one docs/plugin-example.py is written for, for testing sources:
series pages, chapter pages, pictures (made on the fly, numbered; refused without the site's Referer, as real sites
do), search and popular lists as JSON, a plugin list (index.json) and a few broken things to test with.

    python fakesite.py <port>
Control: GET /admin/add?slug=&n=  adds a chapter; GET /admin/broken?on=1 makes chapter pictures error pages."""
import io
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from PIL import Image, ImageDraw

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8797
BASE = "http://127.0.0.1" if PORT == 80 else f"http://127.0.0.1:{PORT}"
HERE = Path(__file__).parent
REPO = Path(sys.argv[2]) if len(sys.argv) > 2 else HERE / "repo"  # a plugin list it serves
SERIES = {
    "moon-tower": {"title": "The Moon Tower", "summary": "A girl climbs a tower that reaches the moon.",
                   "chapters": [1, 2, 3, 3.5, 4]},
    "red-river": {"title": "Red River Days", "summary": "Two brothers run a ferry.", "chapters": [1, 2]},
    "moonlight-cafe": {"title": "Moonlight Cafe", "summary": "A cafe open only at night.", "chapters": [1]},
}
STATE = {"broken": False}
lock = threading.Lock()


def picture(text: str, w: int, h: int, fmt: str = "PNG") -> bytes:
    im = Image.new("RGB", (w, h), (250, 248, 240))
    d = ImageDraw.Draw(im)
    d.rectangle([20, 20, w - 20, h - 20], outline=(30, 30, 30), width=4)
    d.text((40, 40), text, fill=(0, 0, 0))
    out = io.BytesIO()
    im.save(out, fmt)
    return out.getvalue()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, code, body, kind="text/html; charset=utf-8"):
        data = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        parts = [p for p in u.path.split("/") if p]
        referred = (self.headers.get("Referer") or "").startswith(BASE)
        if parts[:1] == ["series"] and parts[1] in SERIES:
            s = SERIES[parts[1]]
            links = "".join(f'<li><a class="chapter" href="/chapter/{parts[1]}/{n:g}" data-number="{n:g}">Chapter {n:g}'
                            f'</a></li>' for n in reversed(s["chapters"]))  # newest first, as sites list them
            return self.send(200, f'<html><h1>{s["title"]}</h1><img class="cover" src="/cover/{parts[1]}.png">'
                                  f'<p class="summary">{s["summary"]}</p><ul>{links}</ul></html>')
        if parts[:1] == ["chapter"] and len(parts) == 3:
            imgs = "".join(f'<img class="page" src="/img/{parts[1]}/{parts[2]}/{i}.png">' for i in range(1, 4))
            return self.send(200, f"<html>{imgs}</html>")
        if parts[:1] == ["img"]:
            if not referred:
                return self.send(403, "No hotlinking")
            if STATE["broken"]:
                return self.send(200, "<html>Please log in</html>")
            slug, n, i = parts[1], parts[2], parts[3].split(".")[0]
            fmt = "GIF" if i == "3" and n == "2" else "PNG"  # a GIF page, saved by the app as PNG
            return self.send(200, picture(f"{slug} ch {n} page {i}", 720, 1200 + 100 * int(i), fmt), f"image/{fmt.lower()}")
        if parts[:1] == ["cover"]:
            if not referred:
                return self.send(403, "No hotlinking")
            return self.send(200, picture(parts[1], 200, 300), "image/png")
        if u.path == "/api/search":
            found = [k for k, s in SERIES.items() if q.get("q", "").lower() in s["title"].lower()]
            return self.json(found, int(q.get("page", 1)), 10)
        if u.path == "/api/popular":
            return self.json(list(SERIES), int(q.get("page", 1)), 2)
        if parts[:1] == ["repo"]:
            f = REPO / "/".join(parts[1:])
            return self.send(200, f.read_bytes(), "application/json") if f.is_file() else self.send(404, "no")
        if u.path == "/admin/add":
            SERIES[q["slug"]]["chapters"].append(float(q["n"]))
            return self.send(200, "ok")
        if u.path == "/admin/broken":
            STATE["broken"] = q.get("on") == "1"
            return self.send(200, "ok")
        self.send(404, "Not found")

    def json(self, slugs, page, size):
        chunk = slugs[(page - 1) * size:page * size]
        more = page * size < len(slugs)
        body = {"results": [{"slug": k, "title": SERIES[k]["title"], "cover": f"{BASE}/cover/{k}.png",
                             "summary": SERIES[k]["summary"]} for k in chunk], "next": page + 1 if more else None}
        self.send(200, json.dumps(body), "application/json")


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
