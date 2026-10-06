"""The UI files, checked without a browser.

There is no DOM here and no console in the window, so a panel that renders
nothing and a panel that was never wired look identical from the outside. These
are the checks that catch the difference: an id the script reaches for that
nothing renders, and a class the script emits that no stylesheet defines.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

UI = Path(__file__).resolve().parent.parent / "python" / "wx" / "gui" / "ui"


@pytest.fixture(scope="module")
def ui() -> dict[str, str]:
    return {n: (UI / n).read_text(encoding="utf-8") for n in ("index.html", "app.css", "app.js")}


#: Built by innerHTML at render time, so absent from the source file.
RUNTIME_IDS = {"empty-action"}

#: A literal class list only: lowercase start, word characters, spaces, dashes.
#: Anything looser matches a JS expression spliced into an attribute and
#: reports "(t.id" as an unstyled class.
_LITERAL = r'class="([a-z][\w -]*)"'


def test_every_file_exists(ui) -> None:
    for name in ("index.html", "app.css", "app.js", "icon.ico"):
        assert (UI / name).is_file(), name


def test_referenced_ids_exist(ui) -> None:
    ids = set(re.findall(r'id="([^"]+)"', ui["index.html"]))
    used = set(re.findall(r'\$\("([^"]+)"\)', ui["app.js"]))
    assert used, "the script reached for no ids at all"
    assert not (used - ids - RUNTIME_IDS), sorted(used - ids - RUNTIME_IDS)


def test_every_class_is_styled(ui) -> None:
    styled = set(re.findall(r"\.([a-zA-Z][\w-]*)", ui["app.css"]))
    classes: set[str] = set()
    for text in (ui["index.html"], ui["app.js"]):
        for group in re.findall(_LITERAL, text) + re.findall(r"class=\\'([a-z][\\w -]*)\\'", text):
            classes.update(group.split())
    assert not (classes - styled), sorted(classes - styled)


def test_panels_referenced_by_the_nav_exist(ui) -> None:
    """A nav button pointing at a missing section switches to a blank panel."""
    panels = set(re.findall(r'data-panel="([^"]+)"', ui["index.html"]))
    ids = set(re.findall(r'id="([^"]+)"', ui["index.html"]))
    for name in panels:
        assert f"panel-{name}" in ids, name


def test_bracket_balance(ui) -> None:
    """Not a parser. A missing paren in 700 lines of IIFE is a blank window,
    and the window has no console to report it."""
    js = ui["app.js"]
    for open_c, close_c in (("(", ")"), ("{", "}"), ("[", "]")):
        assert js.count(open_c) == js.count(close_c), open_c


def test_every_request_carries_a_tab(ui) -> None:
    """Two tabs share nothing but the URL; a fetch that forgets ?tab= is one
    conversation answering another's question."""
    js = ui["app.js"]
    for call in re.findall(r'fetch\("([^"]+)"', js):
        assert "?tab=" in call, call


def test_model_output_is_escaped_before_formatting(ui) -> None:
    """The inline formatter inserts markup. If it runs before escaping, a tool
    result containing <img onerror=...> becomes script execution."""
    js = ui["app.js"]
    md_at = js.index("function md(text)")
    body = js[md_at:md_at + 400]
    assert body.index("esc(text)") < body.index("inline(s)"), "escape must come first"
    assert "function inline(escaped)" in js
    assert "unescape" not in js and "innerHTML = ev" not in js


def test_tabs_do_not_share_module_state(ui) -> None:
    """Turns live per tab. A module-level turns array is the exact bug the
    pool was written to fix, in the renderer."""
    js = ui["app.js"]
    assert re.search(r"turns: \[\]", js), "turns should live inside the tab object"
    assert not re.search(r"var turns = \[\]", js)