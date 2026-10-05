"""Version agreement, token counting, and the native core's liveness.

These are the invariants that break silently. A version mismatch still runs;
it just reports the wrong number on two different surfaces. A broken GIL
contract does not fail an assertion -- it kills the interpreter, and the test
run dies with 0xC0000409 and no traceback. Both are cheap to check and
expensive to discover later.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

import wx

ROOT = Path(__file__).resolve().parent.parent


class TestVersionAgreement:
    def test_pyproject_matches_version_json(self) -> None:
        import re

        text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        declared = re.search(r"^version\s*=\s*[\"']([^\"']+)[\"']", text, re.MULTILINE)
        assert declared, "pyproject.toml has no version line"
        assert declared.group(1) == wx.__version__

    def test_version_json_is_the_source(self) -> None:
        import json

        vj = json.loads((ROOT / "version.json").read_text(encoding="utf-8"))
        assert vj["version"] == wx.__version__

    def test_check_version_script_passes(self) -> None:
        # The script is what CI and the release build run. Exercising it here
        # means a version bump that forgets to rebuild the extension fails the
        # test suite instead of failing on someone's machine.
        r = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "check_version.py")],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=ROOT,
        )
        assert r.returncode == 0, f"{r.stdout}\n{r.stderr}"

    def test_not_a_placeholder(self) -> None:
        # 0.0.0+unknown is what _detect_version() falls back to when neither
        # version.json nor the native core can be read. A release must never
        # ship that string.
        assert wx.__version__ != "0.0.0+unknown"


@pytest.mark.skipif(not wx.has_core(), reason="native core not built")
class TestNativeCore:
    def test_core_version_matches_package(self) -> None:
        core = wx.require_core()
        assert core.version() == wx.__version__

    def test_core_identity(self) -> None:
        core = wx.require_core()
        assert core.channel() == "dev"
        assert core.product() == "WUXICODE"

    def test_gil_is_not_released_twice(self) -> None:
        """count_tokens and friends must not both release the GIL themselves
        and via py::call_guard.

        The double release made the second PyEval_SaveThread run with a NULL
        thread state, which is a fatal interpreter error rather than a Python
        exception -- so the first symptom was pytest exiting with 0xC0000409
        and no traceback at all. A subprocess is the only honest way to test
        it: if it dies, this test dies, and that is the point.
        """
        code = (
            "import _wx_core\n"
            "assert _wx_core.count_tokens('hello world') > 0\n"
            "assert _wx_core.estimate_tokens('hello') > 0\n"
            "assert _wx_core.hash64(b'abc') > 0\n"
            "print('ok')\n"
        )
        r = subprocess.run(
            [sys.executable, "-c", code], capture_output=True, text=True
        )
        assert r.returncode == 0, (
            f"interpreter died with 0x{r.returncode & 0xFFFFFFFF:08X} -- "
            f"a GIL contract violation, not a Python error\n{r.stderr}"
        )
        assert "ok" in r.stdout

    def test_tokenizer_is_honest_about_being_heuristic(self) -> None:
        core = wx.require_core()
        assert core.tokenizer_mode() in {"exact", "heuristic"}

    def test_sse_parser_roundtrip(self) -> None:
        core = wx.require_core()
        p = core.SseParser()
        events = p.feed(b'data: {"a":1}\n\n')
        assert len(events) == 1
