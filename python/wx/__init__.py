"""WUXICODE — a hybrid coding agent that streams while your tools read.

Importing this package never fails and never imports the native core. The
extension module is loaded on first use so that a broken or unbuilt native
layer produces an actionable message instead of an ImportError traceback at
package import time.

    >>> import wx
    >>> wx.require_core().version()
    '0.1.0'
"""

from __future__ import annotations

__all__ = ["__version__", "CoreUnavailable", "require_core", "has_core"]


def _detect_version() -> str:
    """The one version string, read from wherever it actually lives.

    There used to be a literal here that a human kept in step with
    version.json and pyproject.toml by hand, and it drifted: the repo said
    0.2.0 while this said 0.1.0, so `--version` and the EXE's file properties
    disagreed and `--doctor` printed a MISMATCH line every run.

    Now there is no literal to forget. Two readers, in order of authority:

        1. version.json, when it sits beside the package (sdist, editable
           install, a frozen bundle) or at the repository root.
        2. the native core, which CMake bakes WX_VERSION_STRING from
           version.json at compile time.

    Both derive from the same file. A build with neither present reports
    0.0.0+unknown, which is honest -- `wx --doctor` says so, and every place
    that compares versions treats it as never matching.
    """
    import json
    from pathlib import Path

    here = Path(__file__).resolve().parent
    for candidate in (here / "version.json", here.parent.parent / "version.json"):
        try:
            with candidate.open("r", encoding="utf-8") as fh:
                value = json.load(fh).get("version")
            if isinstance(value, str) and value:
                return value
        except (OSError, ValueError, AttributeError):
            continue

    try:
        import _wx_core  # noqa: PLC0415  (deliberately deferred)

        baked = _wx_core.version()
        if baked:
            return str(baked)
    except ImportError:
        pass

    return "0.0.0+unknown"


# Single source: version.json, read at import. pyproject.toml declares the same
# number for packaging, and scripts/check_version.py fails the build if the two
# ever diverge.
__version__ = _detect_version()

_MISSING_CORE_HINT = """\
wx._wx_core is not importable.

The native core (_wx_core) is built from C++ and is not part of a pure-Python
install. Build it with:

    pip install -e .

from the project root. If that succeeded and this still fires, the extension
was built for a different Python version or architecture than the one running
now. Check with:

    python -c "import sys, sysconfig; print(sys.version); print(sysconfig.get_platform())"

Original error: {error}\
"""


class CoreUnavailable(RuntimeError):
    """Raised when the compiled core cannot be loaded."""


def require_core():
    """Import and return the native core module, or explain how to build it."""
    try:
        import _wx_core  # noqa: PLC0415  (deliberately deferred)
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise CoreUnavailable(_MISSING_CORE_HINT.format(error=exc)) from exc
    return _wx_core


def has_core() -> bool:
    """True when the native core is importable. Never raises."""
    try:
        import _wx_core  # noqa: F401, PLC0415
    except ImportError:
        return False
    return True