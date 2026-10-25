#!/usr/bin/env python3
"""
build-index.py  —  VibhillionAI · Yojana viewer

Scans the repo for monthly issue pages laid out as

    en/<YEAR>/<Month>.html        e.g.  en/2026/October.html     (English edition)
    te/<YEAR>/<Month>.html        e.g.  te/2026/October.html     (Telugu edition)

reads the theme / contents out of each page (see issue-template-en.html) and writes
content-index.json, which index.html fetches on load.

Run by the GitHub Pages workflow on every push, so nothing needs to be run by hand.
Standard library only (no pip install).
"""
import json
import re
import sys
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "content-index.json"
LANGS = ("en", "te")
MONTHS = ["January", "February", "March", "April", "May", "June", "July",
          "August", "September", "October", "November", "December"]
MONTH_BY_LOWER = {m.lower(): m for m in MONTHS}


def clean(text):
    return re.sub(r"\s+", " ", text or "").strip()


class IssueParser(HTMLParser):
    """Collects the issue theme, the 'at a glance' list and every <article class="yj-article">."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.theme_attr = ""
        self.head_h1 = ""
        self.glance = []
        self.articles = []
        self._cur = None
        self._depth = 0
        self._cap = None
        self._buf = []
        self._in_title = False
        self._in_head = 0        # >0 while inside <header class="issue-head">
        self._in_h1 = False
        self._in_glance = False
        self._facts_depth = 0

    def _start_cap(self, what):
        self._cap, self._buf = what, []

    def _end_cap(self):
        text = clean("".join(self._buf))
        what, self._cap, self._buf = self._cap, None, []
        if what == "glance":
            if text:
                self.glance.append(text)
            return
        a = self._cur
        if a is None or not text:
            return
        if what == "title" and not a["title"]:
            a["title"] = text
        elif what == "author" and not a["author"]:
            a["author"] = re.sub(r"^(by|రచయిత)\s*[:\-]?\s*", "", text, flags=re.I)
        elif what == "summary" and not a["summary"]:
            a["summary"] = text
        elif what == "fact":
            a["facts"].append(text)

    def handle_starttag(self, tag, attrs):
        at = dict(attrs)
        cls = (at.get("class") or "").split()
        if tag == "title":
            self._in_title = True
        elif tag == "header" and "issue-head" in cls:
            self._in_head = 1
            self.theme_attr = clean(at.get("data-theme"))
        elif tag == "h1" and self._in_head and self._cur is None:
            self._in_h1 = True
        elif tag == "ol" and "glance" in cls and self._cur is None:
            self._in_glance = True
        elif tag == "li" and self._in_glance:
            self._start_cap("glance")
        elif tag == "article":
            if self._cur is not None:
                self._depth += 1
            elif "yj-article" in cls:
                read = at.get("data-read") or ""
                self._cur = {
                    "id": at.get("id") or "",
                    "category": clean(at.get("data-category")) or "General",
                    "author": clean(at.get("data-author")),
                    "relevance": clean(at.get("data-relevance")),
                    "readMin": int(read) if read.strip().isdigit() else 0,
                    "title": "", "summary": "", "facts": [],
                }
                self._depth = 0
        elif self._cur is not None:
            if tag == "h2" and not self._cur["title"]:
                self._start_cap("title")
            elif tag == "p" and "author" in cls and not self._cur["author"]:
                self._start_cap("author")
            elif tag == "p" and "summary" in cls and not self._cur["summary"]:
                self._start_cap("summary")
            elif tag == "ul" and "key-facts" in cls:
                self._facts_depth = 1
            elif tag == "ul" and self._facts_depth:
                self._facts_depth += 1
            elif tag == "li" and self._facts_depth == 1:
                self._start_cap("fact")

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        elif tag == "header":
            self._in_head = 0
            self._in_h1 = False
        elif tag == "h1":
            self._in_h1 = False
        elif tag == "ol" and self._in_glance:
            self._in_glance = False
        elif tag == "article" and self._cur is not None:
            if self._depth:
                self._depth -= 1
            else:
                self.articles.append(self._cur)
                self._cur, self._cap, self._facts_depth = None, None, 0
        elif tag in ("h2", "p", "li") and self._cap:
            self._end_cap()
        elif tag == "ul" and self._cur is not None and self._facts_depth:
            self._facts_depth -= 1

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        if self._in_h1:
            self.head_h1 += data
        if self._cap:
            self._buf.append(data)


def parse_issue(path, label):
    p = IssueParser()
    p.feed(path.read_text(encoding="utf-8", errors="replace"))
    notes = []
    theme = p.theme_attr or clean(p.head_h1) or clean(p.title) or label
    if not (p.theme_attr or clean(p.head_h1)):
        notes.append("no issue theme found (header.issue-head), using the page title")
    arts = p.articles
    for i, a in enumerate(arts, 1):
        if not a["id"]:
            a["id"] = f"a{i}"
            notes.append(f"article {i} has no id attribute, using a{i}")
        if not a["title"]:
            a["title"] = f"Article {i}"
            notes.append(f"article {i} has no <h2> title")
        if not a["readMin"]:
            words = len(" ".join([a["title"], a["summary"]] + a["facts"]).split())
            a["readMin"] = max(1, round(words / 40))
    if not arts:
        arts = [{"id": "a1", "category": "General", "author": "", "relevance": "",
                 "readMin": 0, "title": theme, "summary": "", "facts": []}]
        notes.append('no <article class="yj-article"> found, listed as a single entry')
    return theme, p.glance, arts, notes


def parse_chapter_folder(d, label):
    """Folder layout:  en/2026/September/index.html + ch01_*.html ...  The folder's index.html
    lists chapters as <a class="cd" href="ch01_x.html"> cards (nb=number, tp=topic, <b>=title,
    sb=summary, au=author). Falls back to the <title> of every ch*.html file."""
    idx = d / "index.html"
    html = idx.read_text(encoding="utf-8", errors="replace")
    notes = []
    h1 = re.search(r"<h1[^>]*>(.*?)</h1>\s*<p[^>]*>(.*?)</p>", html, re.S)
    strip = lambda t: clean(re.sub(r"<[^>]+>", " ", t))
    theme = strip(h1.group(2)) if h1 else label
    theme = re.split(r"\s+[\u2014-]\s+chapter", theme, flags=re.I)[0]
    chapters = []
    for m in re.finditer(r'<a[^>]*class="cd"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', html, re.S):
        href, body = m.group(1), m.group(2)
        if not (d / href).is_file():
            notes.append(f"{href} listed in index.html but file not found"); continue
        g = lambda c: strip((re.search(r'<span class="%s">(.*?)</span>' % c, body, re.S) or [0, ""])[1] if True else "")
        title = strip((re.search(r"<b>(.*?)</b>", body, re.S) or [0, ""])[1])
        col = re.search(r"--c:(#[0-9A-Fa-f]{3,8})", m.group(0))
        chapters.append({"href": href, "category": g("tp") or "Chapter", "author": g("au"),
                         "title": title, "summary": g("sb"), "color": col.group(1) if col else ""})
    if not chapters:
        for f in sorted(d.glob("ch*.html")):
            t = re.search(r"<title>(.*?)</title>", f.read_text(encoding="utf-8", errors="replace"), re.S)
            chapters.append({"href": f.name, "category": "Chapter", "author": "", "color": "",
                             "title": strip(t.group(1)) if t else f.stem, "summary": ""})
        notes.append("no chapter cards in index.html, listed from ch*.html files")
    arts = []
    for i, c in enumerate(chapters, 1):
        c["id"] = f"c{i}"; c["readMin"] = 0; c["relevance"] = ""; c["facts"] = []
        c["title"] = re.sub(r"^Chapter\s*\d+\s*:\s*", "", c["title"])
        arts.append(c)
    return theme, [], arts, notes


def main():
    editions, skipped, warnings = {}, [], []
    n_issues = n_articles = 0

    for lang in LANGS:
        ldir = ROOT / lang
        if not ldir.is_dir():
            continue
        for ydir in sorted(p for p in ldir.iterdir() if p.is_dir() and re.fullmatch(r"\d{4}", p.name)):
            for f in sorted(ydir.iterdir()):
                if f.is_dir() and (f / "index.html").is_file() and f.name.lower() in MONTH_BY_LOWER:
                    month = MONTH_BY_LOWER[f.name.lower()]
                    rel = (f / "index.html").relative_to(ROOT).as_posix()
                    theme, glance, arts, notes = parse_chapter_folder(f, f"{month} {ydir.name}")
                    base = f.relative_to(ROOT).as_posix() + "/"
                    for a in arts:
                        a["href"] = base + a["href"]
                    editions.setdefault(lang, {}).setdefault(ydir.name, {})[month] = {
                        "path": rel, "theme": theme, "glance": glance, "articles": arts, "chapters": True}
                    n_issues += 1; n_articles += len(arts)
                    warnings += [f"{rel}: {n}" for n in notes]
                    continue
                if not f.is_file() or f.name.startswith("."):
                    continue
                m = re.fullmatch(r"([A-Za-z]+)\.html", f.name, re.I)
                month = MONTH_BY_LOWER.get(m.group(1).lower()) if m else None
                if not month:
                    skipped.append(f"{lang}/{ydir.name}/{f.name}  (expected an English month name like October.html)")
                    continue
                rel = f.relative_to(ROOT).as_posix()
                theme, glance, arts, notes = parse_issue(f, f"{month} {ydir.name}")
                editions.setdefault(lang, {}).setdefault(ydir.name, {})[month] = {
                    "path": rel, "theme": theme, "glance": glance, "articles": arts}
                n_issues += 1
                n_articles += len(arts)
                warnings += [f"{rel}: {n}" for n in notes]

    index = {"generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "editions": editions}
    OUT.write_text(json.dumps(index, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"content-index.json written: {n_issues} issue page(s), {n_articles} article(s)")
    for lang in LANGS:
        for y in sorted(editions.get(lang, {})):
            ms = sorted(editions[lang][y], key=MONTHS.index)
            print(f"  {lang}/{y}: {', '.join(ms)}")
    if warnings:
        print("\nWarnings:")
        for w in warnings:
            print("  -", w)
    if skipped:
        print("\nSkipped (not picked up by the viewer):")
        for s in skipped:
            print("  -", s)
    if n_issues == 0:
        print("\nNo issue pages found yet. Add e.g. en/2026/October.html and push again.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
