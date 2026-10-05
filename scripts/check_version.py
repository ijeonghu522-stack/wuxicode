"""Fail loudly when the version number has drifted between its sources.

`wx.__version__` is now read from version.json at import time rather than
being a literal somebody has to remember to bump, which removes the drift
that made `--version` print 0.1.0 while the EXE's file properties said 0.2.0.
What this script guards is the pair that is still hand-maintained:

    version.json      the source of truth
    pyproject.toml    read by pip, scikit-build-core and CMake
    _wx_core.version()  baked from version.json at compile time

Run it after changing a version, and before every release build. The native
core is optional -- a pure-Python install without it is a legitimate state --
so a missing extension is reported, not failed.

    python scripts/check_version.py
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def fail(message: str) -> None:
    print(f"  [XX] {message}")
    raise SystemExit(1)


def main() -> int:
    print("== version sources ==")

    try:
        vj = json.loads((ROOT / "version.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        fail(f"version.json unreadable: {exc}")
    source = vj.get("version")
    if not isinstance(source, str) or not source:
        fail("version.json has no usable 'version'")
    print(f"  version.json        {source}")

    # pyproject is TOML, but the only field we need is a single quoted string
    # on a `version =` line. A full TOML parse would need a dependency, and the
    # project deliberately keeps its runtime deps to httpx alone.
    try:
        text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    except OSError as exc:
        fail(f"pyproject.toml unreadable: {exc}")
    m = re.search(r"^version\s*=\s*[\"']([^\"']+)[\"']", text, re.MULTILINE)
    if not m:
        fail("pyproject.toml has no `version = \"...\"` line")
    declared = m.group(1)
    print(f"  pyproject.toml      {declared}")
    if declared != source:
        fail(f"pyproject.toml says {declared}, version.json says {source}")

    try:
        import wx  # noqa: PLC0415
    except ImportError as exc:
        fail(f"cannot import wx: {exc}")
    print(f"  wx.__version__      {wx.__version__}")
    if wx.__version__ != source:
        fail(f"wx.__version__ is {wx.__version__}, version.json says {source}")

    print()
    print("== native core ==")
    try:
        import _wx_core  # noqa: PLC0415
    except ImportError:
        print("  [!!] _wx_core not importable -- skipped (pure-Python install)")
        print("      rebuild with `pip install -e .` to check it")
        print()
        print("version sources agree")
        return 0

    baked = _wx_core.version()
    print(f"  _wx_core.version()  {baked}")
    if baked != source:
        fail(
            f"the compiled core reports {baked}, version.json says {source}.\n"
            "      CMake bakes WX_VERSION_STRING from version.json, so the "
            "extension is stale.\n"
            "      rebuild: pip install -e .   (or delete build/ and reinstall)"
        )

    print()
    print(f"version sources agree: {source}")
    return 0


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    raise SystemExit(main())
