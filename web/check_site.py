"""Static checks on web/ — no node, no network, no browser.

Catches the failures that actually happen when editing a static site by hand:
a typo'd asset path, an unclosed tag, a CSS brace that does not balance.
"""

from __future__ import annotations

import re
import sys
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VOID = {
    "area", "base", "br", "col", "embed", "hr", "img", "input",
    "link", "meta", "param", "source", "track", "wbr",
}


class Collector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[tuple[str, int]] = []
        self.errors: list[str] = []
        self.refs: list[str] = []
        self.ids: set[str] = set()
        self.headings: list[tuple[str, str]] = []
        self.imgs_no_alt = 0

    def handle_starttag(self, tag, attrs):
        d = dict(attrs)
        if "id" in d:
            if d["id"] in self.ids:
                self.errors.append(f"duplicate id: {d['id']}")
            self.ids.add(d["id"])
        for key in ("src", "href"):
            if key in d:
                self.refs.append(d[key])
        # alt="" is the correct value for a decorative image, so the test is
        # presence, not truthiness.
        if tag == "img" and "alt" not in d:
            self.imgs_no_alt += 1
        if re.fullmatch(r"h[1-6]", tag):
            self._h = []
        if tag not in VOID:
            self.stack.append((tag, self.getpos()[0]))

    def handle_endtag(self, tag):
        if tag in VOID:
            return
        if not self.stack:
            self.errors.append(f"line {self.getpos()[0]}: </{tag}> with nothing open")
            return
        open_tag, line = self.stack.pop()
        if open_tag != tag:
            self.errors.append(
                f"line {self.getpos()[0]}: </{tag}> closes <{open_tag}> opened at line {line}"
            )


def main() -> int:
    problems: list[str] = []

    index = ROOT / "index.html"
    if not index.exists():
        print(f"  [XX] missing {index}")
        return 1

    html = index.read_text(encoding="utf-8")
    c = Collector()
    c.feed(html)

    for e in c.errors:
        problems.append(e)
    if c.stack:
        problems.append(f"unclosed tags: {[t for t, _ in c.stack]}")

    # -- assets referenced actually exist ---------------------------------
    for ref in c.refs:
        if ref.startswith(("http://", "https://", "mailto:", "#")):
            continue
        target = ref.split("#", 1)[0].split("?", 1)[0]
        if not target:
            continue
        p = (index.parent / target).resolve()
        if not p.exists():
            problems.append(f"missing asset: {ref}")

    # -- in-page anchors resolve ------------------------------------------
    for ref in c.refs:
        if ref.startswith("#") and len(ref) > 1:
            if ref[1:] not in c.ids:
                problems.append(f"anchor with no target: {ref}")

    # -- required page furniture ------------------------------------------
    if "<title>" not in html:
        problems.append("no <title>")
    m = re.search(r'<meta\s+name="viewport"[^>]*width=device-width', html)
    if not m:
        problems.append("no viewport meta (mobile will render at desktop width)")
    if 'lang="ko"' not in html:
        problems.append('html element has no lang attribute')
    if "<h1" not in html:
        problems.append("no <h1>")
    if c.imgs_no_alt:
        problems.append(f"{c.imgs_no_alt} <img> without alt")
    if "description" not in html:
        problems.append('no meta name="description"')

    # -- CSS balances ------------------------------------------------------
    css_path = index.parent / "assets" / "style.css"
    if not css_path.exists():
        problems.append("missing assets/style.css")
    else:
        css = css_path.read_text(encoding="utf-8")
        if css.count("{") != css.count("}"):
            problems.append(
                f"CSS braces unbalanced: {css.count('{')} open, {css.count('}')} close"
            )
        # var(--x) with no matching declaration
        declared = set(re.findall(r"(--[a-z0-9-]+)\s*:", css))
        used = set(re.findall(r"var\((--[a-z0-9-]+)", css))
        undefined = used - declared
        if undefined:
            problems.append(f"CSS uses undefined variables: {sorted(undefined)}")

    # -- JS balances -------------------------------------------------------
    js_path = index.parent / "assets" / "main.js"
    if not js_path.exists():
        problems.append("missing assets/main.js")
    else:
        js = js_path.read_text(encoding="utf-8")
        for open_c, close_c in (("{", "}"), ("(", ")"), ("[", "]")):
            if js.count(open_c) != js.count(close_c):
                problems.append(
                    f"JS {open_c}{close_c} unbalanced: "
                    f"{js.count(open_c)} vs {js.count(close_c)}"
                )

    print(f"  html   {len(html):>6} bytes   {len(c.ids)} ids   {len(c.refs)} refs")
    if css_path.exists():
        print(f"  css    {len(css_path.read_text(encoding='utf-8')):>6} bytes")
    if js_path.exists():
        print(f"  js     {len(js_path.read_text(encoding='utf-8')):>6} bytes")
    print()

    if problems:
        print(f"  {len(problems)} problem(s):")
        for p in problems:
            print(f"   - {p}")
        return 1

    print("  structure OK")
    return 0


if __name__ == "__main__":
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    raise SystemExit(main())
