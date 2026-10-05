"""The package must import and run on every Python that pyproject claims.

`requires-python = ">=3.10"` is a promise in a file nobody reads. The cheapest
way to make it true is to check the two things that actually break it: syntax
newer than 3.10, and stdlib names that only exist from 3.11.

This is not a substitute for testing on 3.10 -- it is the cheap half. The
expensive half is a CI matrix, and there is no CI here yet.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCAN_DIRS = ("python", "license_server", "scripts", "tests", "web")

# name -> (introduced_in, why it matters)
NEWER_THAN_310 = {
    "StrEnum": (3, 11),
    "tomllib": (3, 11),
    "ExceptionGroup": (3, 11),
    "TaskGroup": (3, 11),
    "file_digest": (3, 11),
    "from datetime import UTC": (3, 11),
    "itertools.batched": (3, 12),
    "TypeAliasType": (3, 12),
    "typing.override": (3, 12),
}


def _sources() -> list[Path]:
    out: list[Path] = []
    for d in SCAN_DIRS:
        p = ROOT / d
        if p.exists():
            out += [f for f in p.rglob("*.py") if "__pycache__" not in f.parts]
    return out


def _min_version() -> tuple[int, ...]:
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    m = re.search(r'requires-python\s*=\s*">=\s*(\d+)\.(\d+)', text)
    assert m, "pyproject.toml has no requires-python"
    return (int(m.group(1)), int(m.group(2)))


def test_min_version_is_310() -> None:
    assert _min_version() == (3, 10)


@pytest.mark.parametrize("feat,ver", sorted(NEWER_THAN_310.items()))
def test_no_feature_newer_than_minimum(feat: str, ver: tuple[int, ...]) -> None:
    """Fail on the first offender, naming the file."""
    if ver <= _min_version():
        pytest.skip(f"{feat} is allowed on this minimum")

    offenders = []
    for f in _sources():
        try:
            src = f.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        # Skip the scanner itself, and comment lines, or this test warns about
        # the very strings it searches for.
        if f.name == Path(__file__).name:
            continue
        for line in src.splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if feat in line:
                offenders.append(f"{f.relative_to(ROOT)}: {stripped[:80]}")

    assert not offenders, (
        f"{feat} needs Python {ver[0]}.{ver[1]} but requires-python is "
        f"{_min_version()[0]}.{_min_version()[1]}:\n  "
        + "\n  ".join(offenders)
    )


def test_utc_alias_exists_where_needed() -> None:
    """The 3.10-compatible spelling must actually be defined."""
    for rel in ("python/wx/ui/history.py", "python/wx/modes/session_manager.py"):
        src = (ROOT / rel).read_text(encoding="utf-8")
        assert "timezone.utc" in src, f"{rel} should derive UTC from timezone"
