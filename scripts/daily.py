"""One daily pass: learn, test, build, report.

Runs the sequence that has to run every day anyway, in the order that fails
fastest, and writes a dated report to docs/reports/ so the history is on disk
rather than in a chat scrollback.

    python scripts/daily.py                 # full pass
    python scripts/daily.py --skip-build    # learn + test only (fast)
    python scripts/daily.py --skip-native   # also skip the C++ rebuild

What each step is for:

    verify    version sources agree. Catches the drift that makes an EXE
              report one number and its file properties another.
    learn     distil the last day's tool calls into observations, then publish
              a digest. This is the layer that makes the agent better at YOUR
              repository over time rather than better at benchmarks.
    test      pytest + the lint rules that catch real bugs (F, E9, B). The
              whole suite must be green before a build is worth starting.
    native    rebuild _wx_core. Only needed when cpp/ or version.json changed;
              the step says so and skips itself otherwise.
    build     PyInstaller onefile EXE, with the script's own --doctor run.
    site      refresh web/ icons and version.json so the landing page cannot
              advertise a build that does not exist.

Exit code is 0 only if every enabled step passed, so this is usable from
Task Scheduler or a cron entry without parsing output.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "docs" / "reports"

def _supports_color() -> bool:
    """ANSI on this terminal? Windows needs more than isatty to say yes."""
    import os

    if os.environ.get("TERM") == "dumb" or os.environ.get("NO_COLOR"):
        return False
    if not hasattr(sys.stdout, "isatty") or not sys.stdout.isatty():
        return False
    if sys.platform == "win32":
        return bool(os.environ.get("WT_SESSION") or os.environ.get("TERM_PROGRAM")
                    or "ANSICON" in os.environ)
    return True


if _supports_color():
    GREEN, RED, YELLOW, DIM, RESET = (
        "\033[32m", "\033[31m", "\033[33m", "\033[90m", "\033[0m"
    )
else:
    GREEN = RED = YELLOW = DIM = RESET = ""


class Step:
    """One named step, timed, with its output captured."""

    def __init__(self, name: str, cmd: list[str], *, cwd: Path = ROOT,
                 timeout: int = 3600, allow_fail: bool = False) -> None:
        self.name = name
        self.cmd = cmd
        self.cwd = cwd
        self.timeout = timeout
        self.allow_fail = allow_fail
        self.ok = False
        self.skipped = False
        self.seconds = 0.0
        self.output = ""

    def run(self) -> "Step":
        print(f"\n{YELLOW}==> {self.name}{RESET} {DIM}$ {' '.join(self.cmd)}{RESET}")
        started = time.perf_counter()
        try:
            proc = subprocess.run(
                self.cmd,
                cwd=self.cwd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.timeout,
            )
            self.output = (proc.stdout or "") + (proc.stderr or "")
            self.ok = proc.returncode == 0 or self.allow_fail
        except FileNotFoundError:
            self.output = f"command not found: {self.cmd[0]}"
            self.ok = False
        except subprocess.TimeoutExpired:
            self.output = f"timed out after {self.timeout}s"
            self.ok = False
        self.seconds = time.perf_counter() - started

        tail = self.output.strip().splitlines()[-6:]
        for line in tail:
            print(f"  {DIM}{line}{RESET}")
        if self.ok:
            print(f"  {GREEN}[OK]{RESET} {self.name} {DIM}({self.seconds:.1f}s){RESET}")
        else:
            print(f"  {RED}[XX]{RESET} {self.name} {DIM}({self.seconds:.1f}s){RESET}")
        return self

    def skip(self, why: str) -> "Step":
        self.skipped = True
        print(f"\n{DIM}==> {self.name} skipped: {why}{RESET}")
        return self


def native_dirty() -> bool:
    """True when cpp/ or version.json is newer than the built extension."""
    pyd = ROOT / "build" / "cp312-cp312-win_amd64" / "cpp" / "Release"
    built = [f for f in pyd.glob("_wx_core*.pyd")] if pyd.exists() else []
    if not built:
        return True
    newest_build = max(f.stat().st_mtime for f in built)
    sources = list((ROOT / "cpp").rglob("*.[ch]pp")) + [ROOT / "CMakeLists.txt", ROOT / "version.json"]
    return any(f.exists() and f.stat().st_mtime > newest_build for f in sources)


def main() -> int:
    ap = argparse.ArgumentParser(description="daily learn/test/build pass")
    ap.add_argument("--skip-build", action="store_true", help="stop after the tests")
    ap.add_argument("--skip-native", action="store_true", help="skip the C++ rebuild")
    ap.add_argument("--skip-site", action="store_true", help="skip the site refresh")
    ap.add_argument("--force-native", action="store_true", help="rebuild even if unchanged")
    args = ap.parse_args()

    now = datetime.now()
    stamp = now.strftime("%Y%m%d-%H%M")
    print(f"{DIM}WUXICODE daily pass · {now:%Y-%m-%d %H:%M}{RESET}")

    steps: list[Step] = []

    # 1. version agreement -- cheapest, and gates everything else
    steps.append(Step("verify versions", [sys.executable, "scripts/check_version.py"]).run())
    if not steps[-1].ok:
        print(f"\n{RED}versions disagree -- not building anything on top of that{RESET}")

    # 2. learn from yesterday's tool calls.
    #    --learn is a flag, not the positional prompt: passing a bare word
    #    here sends it to the model, which costs a real API call and 40s.
    steps.append(
        Step("learn (digest)", [sys.executable, "-m", "wx.cli", "--learn"]).run()
    )

    # 3. tests and the lint rules that catch real bugs
    steps.append(Step("pytest", [sys.executable, "-m", "pytest", "-q"]).run())
    steps.append(
        Step("ruff (F, E9, B)", [sys.executable, "-m", "ruff", "check", "python",
                                 "license_server", "tests", "--select", "F,E9,B"]).run()
    )
    steps.append(Step("site check", [sys.executable, "web/check_site.py"]).run())

    if not args.skip_build:
        native = Step("native rebuild", [
            "cmake", "--build", "build/cp312-cp312-win_amd64", "--config", "Release",
            "--target", "_wx_core",
        ], allow_fail=True)
        if args.skip_native:
            native.skip("--skip-native")
        elif not native_dirty() and not args.force_native:
            native.skip("cpp/ and version.json unchanged since the last build")
        else:
            # Needs MSVC on PATH; only the build step, so let the user decide.
            native.run()
        steps.append(native)

        steps.append(
            Step("build EXE", [
                "powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                "-File", "scripts/build_exe.ps1",
            ], timeout=3600).run()
        )

    if not args.skip_site:
        steps.append(
            Step("refresh site", [sys.executable, "scripts/sync_site.py"]).run()
        )

    # -- report ------------------------------------------------------------
    REPORTS.mkdir(parents=True, exist_ok=True)
    report = REPORTS / f"{stamp}.json"
    payload = {
        "started": now.isoformat(timespec="seconds"),
        "duration_s": round(sum(s.seconds for s in steps), 1),
        "steps": [
            {
                "name": s.name,
                "ok": s.ok,
                "skipped": s.skipped,
                "seconds": round(s.seconds, 1),
                "tail": s.output.strip().splitlines()[-12:],
            }
            for s in steps
        ],
    }
    report.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    failed = [s for s in steps if not s.ok and not s.skipped]
    print(f"\n{'=' * 60}")
    print(f"  {'PASS' if not failed else 'FAIL'}  "
          f"{len(steps) - len(failed)}/{len(steps)} steps  "
          f"{DIM}{payload['duration_s']}s{RESET}")
    print(f"  report: {report.relative_to(ROOT)}")
    if failed:
        print(f"  failed: {', '.join(s.name for s in failed)}")
    print("=" * 60)
    return 1 if failed else 0


if __name__ == "__main__":
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    raise SystemExit(main())
