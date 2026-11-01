"""Example Comics: a Recap Studio source plugin for a made-up site, to copy from when writing one for a real site.

Recap Studio reads the CAPITAL settings below without running the file, then runs the functions in a process of its
own when a member uses the plugin: series() and pages() are required; search() and browse() are optional. Each
returns plain data (dicts, lists, text, numbers). Raise an exception with a readable message when something's wrong;
the app shows it. See docs/sources.md for the whole interface.

The made-up site has:
  https://example.com/series/<slug>          a series' page: its title, cover, summary and chapter links
  https://example.com/chapter/<slug>/<n>     a chapter's page: its pictures as <img class="page" src=...>
  https://example.com/api/search?q=&page=    search results as JSON
  https://example.com/api/popular?page=      the popular list as JSON
"""
import html
import re

import requests

RECAP_PLUGIN = 1                 # the plugin interface this file is written for
NAME = "Example Comics"
VERSION = "1.0.0"
SITES = ["example.com"]          # links on these sites open with this plugin
LANGUAGE = "en"
NSFW = False
LISTS = [{"id": "popular", "name": "Popular"}]     # what browse() can list
HEADERS = {"Referer": "https://example.com/"}      # sent with every picture the app downloads
DELAY = 0                        # seconds between pictures, for sites that limit how fast you ask

BASE = "https://example.com"
_session = requests.Session()
_session.headers["User-Agent"] = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"


def _page(url: str) -> str:
    r = _session.get(url, timeout=30)
    r.raise_for_status()
    return r.text


def _cards(found: dict) -> dict:
    return {"items": [{"url": f"{BASE}/series/{x['slug']}", "title": x["title"], "cover": x.get("cover"),
                       "summary": x.get("summary", "")} for x in found["results"]],
            "more": found.get("next") is not None}


def search(query: str, page: int = 1) -> dict:
    """Series whose title matches: {"items": [{"url", "title", "cover", "summary"}], "more": another page?}."""
    r = _session.get(f"{BASE}/api/search", params={"q": query, "page": page}, timeout=30)
    r.raise_for_status()
    return _cards(r.json())


def browse(list_id: str, page: int = 1) -> dict:
    """One of LISTS, a page at a time, the same shape as search()."""
    if list_id != "popular":
        raise ValueError(f"No list {list_id}")
    r = _session.get(f"{BASE}/api/popular", params={"page": page}, timeout=30)
    r.raise_for_status()
    return _cards(r.json())


def series(url: str) -> dict:
    """A series from its link: {"title", "cover", "summary", "chapters": [{"url", "name", "number", "date"}]}."""
    text = _page(url)
    title = re.search(r"<h1[^>]*>(.*?)</h1>", text, re.S)
    cover = re.search(r'<img class="cover" src="([^"]+)"', text)
    summary = re.search(r'<p class="summary">(.*?)</p>', text, re.S)
    chapters = [{"url": requests.compat.urljoin(url, href), "name": html.unescape(name.strip()), "number": float(n)}
                for href, n, name in re.findall(r'<a class="chapter" href="([^"]+)" data-number="([\d.]+)">(.*?)</a>', text)]
    if not title:
        raise ValueError("That page isn't a series on Example Comics")
    return {"title": html.unescape(title.group(1).strip()), "cover": cover and requests.compat.urljoin(url, cover.group(1)),
            "summary": summary and html.unescape(summary.group(1).strip()), "chapters": chapters}


def pages(chapter_url: str) -> list:
    """A chapter's pictures, in reading order: links (or {"url", "headers"} for a picture that needs its own)."""
    text = _page(chapter_url)
    found = re.findall(r'<img class="page" src="([^"]+)"', text)
    if not found:
        raise ValueError("No pictures on that chapter's page (it may need a login)")
    return [requests.compat.urljoin(chapter_url, src) for src in found]
