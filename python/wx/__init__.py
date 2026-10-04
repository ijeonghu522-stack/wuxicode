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

# Keep in sync with version.json and pyproject.toml. Test T3 in CI asserts all
# three agree; this is the third copy the owner maintains.
__version__ = "0.1.0"

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