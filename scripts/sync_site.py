"""Copy the build's facts into web/ so the site cannot lie about a release.

The landing page is static, which is the point -- it opens from disk with no
server and no build step. The cost of static is that nothing stops it drifting
from the binary, so this script is the thing that stops it:

    version.json  ->  web/version.json   (the page reads it at runtime)
    icon-*.png    ->  web/assets/        (favicon, apple-touch, og image)

Run it after a version bump or an icon change. `daily.py` runs it last, so a
published page always describes the EXE that was just built.

    python scripts/sync_site.py
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
WEB = ROOT / "web"
WEB_ASSETS = WEB / "assets"

# (source, destination) for the icon sizes the page actually references.
ICONS = (
    ("icon-512.png", "icon-512.png"),   # favicon, og image, hero, footer
    ("icon-512.png", "icon-180.png"),   # apple-touch-icon
    ("icon-512.png", "icon-96.png"),    # android / PWA
    ("icon.ico", "favicon.ico"),        # classic fallback
)


def main() -> int:
    if not WEB.exists():
        print(f"  [!!] no web/ directory at {WEB}")
        return 0

    WEB_ASSETS.mkdir(parents=True, exist_ok=True)
    copied: list[str] = []

    # -- version ---------------------------------------------------------
    vj = ROOT / "version.json"
    if vj.exists():
        data = json.loads(vj.read_text(encoding="utf-8"))
        shutil.copy2(vj, WEB / "version.json")
        copied.append(f"version.json -> {data.get('version', '?')}")
    else:
        print("  [!!] version.json missing; the page will keep its static version")

    # -- icons -----------------------------------------------------------
    for src_name, dst_name in ICONS:
        src = ASSETS / src_name
        if not src.exists():
            print(f"  [!!] missing {src}; skipping {dst_name}")
            continue
        if src.suffix == ".ico":
            shutil.copy2(src, WEB_ASSETS / dst_name)
        else:
            # Copy rather than link: web/ has to be self-contained enough to
            # upload to a static host.
            shutil.copy2(src, WEB_ASSETS / dst_name)
        copied.append(dst_name)

    for line in copied:
        print(f"  [OK] {line}")

    # -- a quick sanity pass ---------------------------------------------
    index = WEB / "index.html"
    if index.exists():
        missing = []
        html = index.read_text(encoding="utf-8")
        import re

        for ref in re.findall(r'(?:src|href)="([^"#][^"]*)"', html):
            if ref.startswith(("http", "mailto:")):
                continue
            if not (WEB / ref).exists():
                missing.append(ref)
        if missing:
            print(f"  [XX] page references missing files: {missing}")
            return 1

    return 0


if __name__ == "__main__":
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    raise SystemExit(main())
