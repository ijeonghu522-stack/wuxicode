"""The window.

pywebview gives a real OS window -- minimise, maximise, snap, taskbar, its own
icon -- by hosting the system WebView2 runtime, which is already installed on
every Windows 10 and 11 machine. That is why this is a window and not a Tauri
or Electron build: neither needs to ship a browser, and neither needs a second
toolchain.

Lifecycle, and why it is the way it is:

    the server is started BEFORE the window
        the URL handed to the window has to resolve on first paint, and a
        connection refused on load is a white box with no error in it

    the window is closed BEFORE the server is stopped
        shutting the listener first races the in-flight SSE request and the
        page sees a network error instead of a clean exit

    the agent is aborted on the way out
        a turn left running would keep tool subprocesses alive after the
        window is gone, which on Windows means orphaned processes holding file
        handles the user cannot see
"""

from __future__ import annotations

import argparse
import os
import sys
import threading
from pathlib import Path

from wx import __version__
from wx import require_core as wx_require_core
from wx.gui.server import UI_DIR, serve
from wx.gui.session import Session

WINDOW_TITLE = "WUXICODE"
WIDTH, HEIGHT = 1280, 800
MIN_WIDTH, MIN_HEIGHT = 900, 600


def _crash_log() -> "Path | None":
    """Where to write the crash log, or None if it cannot be opened.

    A --windowed PyInstaller binary has sys.stdout and sys.stderr set to None,
    so a window that fails to open leaves no trace. The log goes next to the
    user's state directory, which always exists and is always writable.
    """
    try:
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        d = Path(base) / "wuxicode"
        d.mkdir(parents=True, exist_ok=True)
        return d / "gui.log"
    except OSError:
        return None


def _log(message: str) -> None:
    """Append to the crash log and try stderr, which is None when windowed."""
    path = _crash_log()
    if path is not None:
        try:
            with path.open("a", encoding="utf-8") as fh:
                fh.write(message.rstrip() + "\n")
        except OSError:
            pass
    try:
        if sys.stderr is not None:
            print(message, file=sys.stderr, flush=True)
    except (AttributeError, ValueError, OSError):
        pass


def _icon_path() -> str | None:
    """The .ico next to the bundled UI, if it survived packaging."""
    here = Path(__file__).resolve().parent
    for candidate in (
        Path(sys._MEIPASS) / "ui" / "icon.ico" if getattr(sys, "frozen", False) else here / "ui" / "icon.ico",
    ):
        if candidate.exists():
            return str(candidate)
    return None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="wuxicode-gui", description="WUXICODE desktop window")
    ap.add_argument("workspace", nargs="?", default=None, help="project directory")
    ap.add_argument("--port", type=int, default=None, help="fixed port (default: ephemeral)")
    ap.add_argument("--browser", action="store_true",
                    help="print the URL and serve without opening a window")
    # A windowed build has no console, so these are the only two ways anything
    # can come back out of it. Without --version the build script's smoke test
    # saw argparse exit 2 and had no output to explain it.
    ap.add_argument("--version", action="version", version=f"WUXICODE {__version__}")
    ap.add_argument("--doctor", action="store_true",
                    help="print build health and exit")
    ap.add_argument("--debug", action="store_true",
                    help="log the window lifecycle and every HTTP request to stderr")
    args = ap.parse_args(argv)

    if args.doctor:
        print(f"WUXICODE {__version__}")
        print(f"  python      : {sys.version.split()[0]}")
        try:
            core = wx_require_core()
            print(f"  native core : OK  version={core.version()} channel={core.channel()}")
            print(f"  tokenizer   : {core.tokenizer_mode()}")
        except Exception as exc:
            print(f"  native core : FAILED\n{exc}")
            return 1
        try:
            import webview  # noqa: F401, PLC0415

            print("  web view    : pywebview OK")
        except ImportError:
            print("  web view    : MISSING (pip install pywebview)")
            return 1
        print(f"  ui          : {UI_DIR}")
        return 0

    ws = Path(args.workspace or os.getcwd()).resolve()
    if not ws.is_dir():
        print(f"workspace is not a directory: {ws}", file=sys.stderr)
        return 2

    try:
        import webview
    except ImportError:
        print("GUI needs pywebview:  pip install pywebview", file=sys.stderr)
        print("the CLI (wuxicode) works without it", file=sys.stderr)
        return 3

    session = Session(workspace=ws)
    if args.debug:
        os.environ["WX_DEBUG"] = "1"
    server = serve(session, args.port)
    host, port = server.server_address[:2]
    url = f"http://{host}:{port}/"

    if args.debug:
        print(f"port   : {port}", file=sys.stderr)
        print(f"url    : {url}", file=sys.stderr)
        print(f"ui dir : {UI_DIR}", file=sys.stderr)
        print(f"ui ok  : {UI_DIR.is_dir()}", file=sys.stderr)
        if UI_DIR.is_dir():
            for item in sorted(UI_DIR.iterdir()):
                print(f"   {item.name}  {item.stat().st_size}", file=sys.stderr)
        sys.stderr.flush()

    # A --windowed PyInstaller binary has sys.stdout and sys.stderr set to
    # None, so every print() below is a silent no-op and a window that fails to
    # open leaves no trace at all. That is exactly the failure this build is
    # most likely to have, so it goes to a file the user can be told to look at.
    log = _crash_log()
    if log:
        log.write_text(
            f"WUXICODE GUI\nurl      : {url}\n"
            f"ui dir   : {UI_DIR}\nui ok    : {UI_DIR.is_dir()}\n"
            f"executable: {sys.executable}\nfrozen   : "
            f"{getattr(sys, 'frozen', False)}\n",
            encoding="utf-8",
        )
    _log(f"[gui] url={url} ui={UI_DIR} frozen={getattr(sys, 'frozen', False)}")

    if args.browser:
        print(f"WUXICODE GUI  {url}")
        print(f"  workspace: {ws}")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            session.shutdown()
            server.shutdown()
        return 0

    # Started before the window so the first paint has something to load.
    thread = threading.Thread(target=server.serve_forever, name="wx-gui-http", daemon=True)
    thread.start()

    def on_closing() -> None:
        session.shutdown()
        server.shutdown()

    if os.environ.get("WX_DEBUG") == "1":
        _log(f"WUXICODE GUI  {url}")

    _log("[gui] creating window")
    try:
        window = webview.create_window(
            WINDOW_TITLE,
            url,
            width=WIDTH,
            height=HEIGHT,
            min_size=(MIN_WIDTH, MIN_HEIGHT),
            background_color="#08080E",
            text_select=True,
            on_top=False,
        )
    except Exception as exc:
        import traceback

        _log(f"[gui] create_window failed: {type(exc).__name__}: {exc}")
        _log(traceback.format_exc())
        session.shutdown()
        server.shutdown()
        return 1

    icon = _icon_path()
    if icon:
        try:
            window.icon = icon
        except Exception:
            # A window with a default icon is fine; a crash over one is not.
            pass

    _log("[gui] starting webview")

    # webview.start's first positional argument is `func`, which runs when the
    # window is CLOSING, not when it is shown. Passing a second one as
    # func= is a TypeError, and on a windowed build that TypeError is invisible
    # without the crash log. "shown" is an event on the window instead.
    def on_shown():
        _log("[gui] window shown")

    try:
        window.events.shown += on_shown
        window.events.closed += lambda: _log("[gui] window closed")
    except Exception as exc:  # older/newer pywebview may not expose events
        _log(f"[gui] could not attach window events: {type(exc).__name__}: {exc}")

    try:
        webview.start(on_closing, private_mode=False)
    except Exception as exc:
        # See _log: with no console there is nowhere to print this.
        import traceback

        detail = f"{type(exc).__name__}: {exc}"
        _log(f"[gui] webview.start failed: {detail}")
        _log(traceback.format_exc())
        _log(f"[gui] the UI is still reachable at {url}")
        session.shutdown()
        server.shutdown()
        return 1
    finally:
        session.shutdown()
        server.shutdown()
    _log("[gui] closed cleanly")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
