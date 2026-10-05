"""Static checks on web/ — no node, no network, no browser.

Catches the failures that actually happen when editing a static site by hand:
a typo'd asset path, an unclosed tag, a CSS brace that does not balance, a
var() with no declaration, an anchor pointing at nothing, a root-absolute path
that works on a host and breaks from disk.

Every *.html file is checked, not just index.html. A 404 page with broken
asset paths ships exactly as easily as a broken index does.
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
                f"line {self.getpos()[0]}: </{tag}> closes <{open_tag}> "
                f"opened at line {line}"
            )


def check_page(page: Path) -> list[str]:
    """One HTML file: tags, assets, anchors, required furniture."""
    out: list[str] = []
    here = page.name
    html = page.read_text(encoding="utf-8")

    c = Collector()
    c.feed(html)

    for e in c.errors:
        out.append(f"{here}: {e}")
    if c.stack:
        out.append(f"{here}: unclosed tags: {[t for t, _ in c.stack]}")

    for ref in c.refs:
        if ref.startswith(("http://", "https://", "mailto:")):
            continue
        target, sep, frag = ref.partition("#")
        if target.startswith("/"):
            # Root-absolute works on a real host and breaks under file://.
            # The site is meant to open from disk as well as be deployed.
            out.append(f"{here}: root-absolute path {ref} breaks file:// use")
            target = target.lstrip("/")
        if target:
            if not (page.parent / target).exists():
                out.append(f"{here}: missing asset: {ref}")
        elif sep and len(frag) > 1 and frag not in c.ids:
            out.append(f"{here}: anchor with no target: {ref}")

    if "<title>" not in html:
        out.append(f"{here}: no <title>")
    if not re.search(r'<meta\s+name="viewport"[^>]*width=device-width', html):
        out.append(f"{here}: no viewport meta")
    if 'lang="ko"' not in html:
        out.append(f"{here}: html element has no lang attribute")
    if "<h1" not in html:
        out.append(f"{here}: no <h1>")
    if c.imgs_no_alt:
        out.append(f"{here}: {c.imgs_no_alt} <img> without alt")

    return out


def check_assets() -> list[str]:
    """CSS and JS balance, and every var() has a declaration."""
    out: list[str] = []

    css_path = ROOT / "assets" / "style.css"
    if not css_path.exists():
        out.append("missing assets/style.css")
    else:
        css = css_path.read_text(encoding="utf-8")
        if css.count("{") != css.count("}"):
            out.append(
                f"CSS braces unbalanced: {css.count('{')} open, "
                f"{css.count('}')} close"
            )
        declared = set(re.findall(r"(--[a-z0-9-]+)\s*:", css))
        used = set(re.findall(r"var\((--[a-z0-9-]+)", css))
        undefined = used - declared
        if undefined:
            out.append(f"CSS uses undefined variables: {sorted(undefined)}")

    js_path = ROOT / "assets" / "main.js"
    if not js_path.exists():
        out.append("missing assets/main.js")
    else:
        js = js_path.read_text(encoding="utf-8")
        for open_c, close_c in (("{", "}"), ("(", ")"), ("[", "]")):
            if js.count(open_c) != js.count(close_c):
                out.append(
                    f"JS {open_c}{close_c} unbalanced: "
                    f"{js.count(open_c)} vs {js.count(close_c)}"
                )

    return out


def main() -> int:
    index = ROOT / "index.html"
    if not index.exists():
        print(f"  [XX] missing {index}")
        return 1

    pages = sorted(ROOT.glob("*.html"))
    problems: list[str] = []
    for page in pages:
        problems += check_page(page)
    problems += check_assets()

    for page in pages:
        print(f"  {page.name:<14} {page.stat().st_size:>6} bytes")
    for name in ("assets/style.css", "assets/main.js"):
        p = ROOT / name
        if p.exists():
            print(f"  {Path(name).name:<14} {p.stat().st_size:>6} bytes")
    print()

    if problems:
        print(f"  {len(problems)} problem(s):")
        for p in problems:
            print(f"   - {p}")
        return 1

    print(f"  structure OK ({len(pages)} page(s))")
    return 0


if __name__ == "__main__":
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    raise SystemExit(main())
