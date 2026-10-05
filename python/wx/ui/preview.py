"""WUXICODE — 파일 미리보기.

에이전트가 파일을 쓰면 곧바로 그 내용을 띄운다. Claude Code가 파일을
만들고 곧바로 패널을 띄우는 그 동작. FTXUI TUI가 아니라 라인 단위
출력이므로, 여기서는 문자열을 만들어 프린트한다.

타입별 처리:

    html    -> 브라우저 열기 버튼 + 소스 코드
    markdown-> 인라인 렌더(제목/굵게/코드펜스) + 원본
    python  -> 구문 검사 결과 + 코드. 실행은 /preview <n> --run
    image   -> 경로·크기만 (터미널에서 그림은 안 그린다)
    json    -> 들여쓰기 붙여 pretty print
    text    -> 그대로

실행은 기본으로 꺼 둔다. 생성된 코드를 확인도 안 하고 돌리면 미리보기
가 코드 실행기가 된다. `--run`을 명시적으로 준 경우에만 subprocess로
5초 제한을 걸고 돌린다.
"""

from __future__ import annotations

import ast
import json as _json
import subprocess
import sys
import webbrowser
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from wx.ui import brand
from wx.ui.width import pad_to, truncate_to

__all__ = [
    "Preview",
    "detect_kind",
    "get_preview",
    "run_preview_command",
    "set_preview",
]

MAX_LINES = 40
MAX_BYTES = 200_000
PREVIEW_BOX_WIDTH = 52


def detect_kind(path: str | Path) -> str:
    """확장자로 종류를 정한다."""
    suffix = Path(path).suffix.lower()
    if suffix in (".html", ".htm", ".xhtml"):
        return "html"
    if suffix in (".md", ".markdown"):
        return "markdown"
    if suffix in (".py", ".pyw"):
        return "python"
    if suffix in (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".svg", ".ico"):
        return "image"
    if suffix in (".json", ".jsonc"):
        return "json"
    if suffix in (".js", ".ts", ".tsx", ".jsx", ".css", ".rs", ".go", ".c", ".cpp", ".h"):
        return "code"
    if suffix in (".txt", ".log", ".csv", ".toml", ".yaml", ".yml", ".ini", ".cfg"):
        return "text"
    return "text"


_LANGUAGE_BY_KIND = {
    "html": "html",
    "markdown": "markdown",
    "python": "python",
    "image": "",
    "json": "json",
    "code": "code",
    "text": "text",
}


def _render_markdown(text: str, width: int) -> list[str]:
    """의존성 없는 경량 마크다운 렌더.

    제목은 대문자로, 굵게는 그대로 굵게 표시 기호로, 코드펜스는 들여싼
    블록으로 바꾼다. 완전한 렌더러가 아니다 — 원문이 항상 아래에 붙으므로
    해석이 틀려도 원본으로 확인할 수 있다.
    """
    out: list[str] = []
    in_fence = False
    for raw in text.splitlines():
        line = raw.rstrip()
        if line.strip().startswith("```"):
            if in_fence:
                out.append("   └" + "─" * 8)
            else:
                out.append("   ┌" + "─" * 8)
            in_fence = not in_fence
            continue
        if in_fence:
            out.append(f"   │ {line}")
            continue
        stripped = line.strip()
        if not stripped:
            out.append("")
            continue
        if stripped.startswith("#"):
            level = len(stripped) - len(stripped.lstrip("#"))
            out.append(truncate_to("  " + stripped.lstrip("#").upper(), width))
            if level <= 2:
                out.append("  " + "=" * min(len(stripped.lstrip("#")), max(4, width - 4)))
            continue
        if stripped.startswith(("- ", "* ")):
            out.append(f"   • {stripped[2:]}")
            continue
        out.append("  " + line.strip())
    if in_fence:
        out.append("   └" + "─" * 8)
    return out


@dataclass
class Preview:
    """미리보기 상태.

    content에 문자열을 담아 두는 기존 인터페이스를 그대로 쓴다. 여기에
    파일 경로·종류·바이트 수를 얹어서 /preview가 뭘 보여줄지 결정한다.
    """

    content: str | None = None
    type: str | None = None  # "code", "markdown", "image", "diff" 등
    language: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    # --- 기존 인터페이스 ----------------------------------------------------

    def set(self, content: str, type_: str, language: str | None = None, **metadata: Any) -> None:
        """미리보기 내용을 직접 지정한다."""
        self.content = content
        self.type = type_
        self.language = language
        self.metadata = metadata

    def clear(self) -> None:
        """미리보기를 비운다."""
        self.content = None
        self.type = None
        self.language = None
        self.metadata.clear()

    def is_empty(self) -> bool:
        return self.content is None

    def has_content(self) -> bool:
        return self.content is not None

    # --- 파일 ---------------------------------------------------------------

    def show_file(self, path: str | Path, *, max_lines: int = MAX_LINES) -> bool:
        """파일을 읽어 미리보기에 올린다. 실패하면 False.

        이력(history)에도 함께 기록한다. 미리보기와 이력은 같은 파일
        목록을 보고 있어서, 한쪽만 갱신하면 둘이 어긋난 화면이 된다.
        """
        target = Path(path).expanduser()
        try:
            target = target.resolve()
        except (OSError, RuntimeError):
            pass

        if not target.is_file():
            self.clear()
            return False

        kind = detect_kind(target)
        try:
            size = target.stat().st_size
        except OSError:
            size = 0

        text: str | None = None
        if kind != "image":
            try:
                raw = target.read_bytes()
            except OSError:
                return False
            truncated = len(raw) > MAX_BYTES
            try:
                text = raw[:MAX_BYTES].decode("utf-8", errors="replace")
            except (UnicodeDecodeError, AttributeError):
                return False
            if truncated:
                text += "\n\n… (파일이 커서 여기서 잘랐습니다)"

        self.content = text if text is not None else ""
        self.type = kind
        self.language = _LANGUAGE_BY_KIND.get(kind)
        self.metadata = {
            "path": str(target),
            "name": target.name,
            "size": size,
            "lines": text.count("\n") + 1 if text is not None else 0,
            "truncated": truncated,
            "kind": kind,
        }

        from wx.ui.history import get_history

        get_history().record(target, size)
        return True

    def open_in_browser(self) -> tuple[bool, str]:
        """html을 기본 브라우저로 연다."""
        path = self.metadata.get("path")
        if not path:
            return False, "열 파일이 없습니다."
        if self.type != "html":
            return False, f"{self.metadata.get('name', '이 파일')}은 브라우저로 열 수 없습니다."
        try:
            opened = webbrowser.open(Path(path).as_uri())
        except (OSError, ValueError) as exc:
            return False, f"브라우저를 열 수 없습니다: {exc}"
        if opened:
            return True, f"브라우저로 열었습니다: {self.metadata.get('name')}"
        return False, "브라우저 실행에 실패했습니다."

    def commands(self) -> str:
        """하단 조작 힌트."""
        name = self.metadata.get("name", "이 파일")
        if self.type == "html":
            return f"  [브라우저 열기] [전체 보기] [닫기]  ({name})"
        if self.type == "python":
            return f"  [구문 검사됨] [전체 보기] [닫기]  ({name})"
        return f"  [전체 보기] [닫기]  ({name})"

    # --- 검사/실행 ----------------------------------------------------------

    def syntax_check(self) -> tuple[bool, str]:
        """파이썬이면 ast로 구문을 본다."""
        if self.type != "python" or self.content is None:
            return True, ""
        try:
            ast.parse(self.content)
        except SyntaxError as exc:
            line = exc.lineno if exc.lineno is not None else 1
            return False, f"구문 오류 {line}번째 줄: {exc.msg}"
        return True, "구문 검사: 통과"

    def run_python(self, timeout: float = 5.0) -> tuple[bool, str]:
        """파이썬 코드를 실제로 돌린다. timeout초 뒤에는 죽인다.

        미리보기가 기본으로 부르지 않는다. 생성된 코드를 확인 없이 실행하면
        미리보기가 코드 실행기가 되므로, 호출자가 명시적으로 요청해야 한다.
        """
        if self.type != "python" or self.content is None:
            return False, "파이썬 파일이 아닙니다."
        path = self.metadata.get("path")
        if not path or not Path(path).is_file():
            return False, "원본 파일을 찾을 수 없습니다."
        try:
            proc = subprocess.run(  # noqa: S603 - 사용자가 요청한 파일만 실행
                [sys.executable, str(path)],
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=str(Path(path).parent),
            )
        except subprocess.TimeoutExpired:
            return False, f"실행 시간 초과 ({timeout}초)"
        except OSError as exc:
            return False, f"실행 실패: {exc}"
        if proc.returncode != 0:
            return False, (proc.stderr or "실행 실패").strip()[:500]
        return True, (proc.stdout or "(출력 없음)").rstrip()[:500]

    # --- 렌더링 -------------------------------------------------------------

    def render(self) -> str:
        """박스 전체를 그린다. 내용이 없으면 그 상태 문구."""
        if self.content is None:
            return "미리보기: 없음"
        if not self.metadata:
            # set()으로 직접 넣은 내용 — 파일 경로가 없다.
            type_str = self.type or "unknown"
            lang_str = f" ({self.language})" if self.language else ""
            return f"[미리보기: {type_str}{lang_str}] {len(self.content)} bytes"

        name = self.metadata.get("name", "?")
        size = self.metadata.get("size", 0)
        kind = self.metadata.get("kind", "text")
        body = self._body_lines()

        inner = PREVIEW_BOX_WIDTH - 4
        ul, ur, ll, lr, v, h = self._glyphs()

        def row(text: str) -> str:
            return f"{v}{pad_to(truncate_to(' ' + text, inner), inner)}{v}"

        title = f" 미리보기: {name} "
        dash = max(0, inner - len(title) - 2)
        top = f"{ul}{h}{title}{h * dash}{ur}"
        lines = [top, row(f"{kind} · {size:,} bytes · {self.metadata.get('lines', 0)} 줄")]
        lines.append(f"{v}{h * (inner + 2)}{v}")
        for text in body:
            lines.append(row(text))
        if body:
            lines.append(f"{v}{h * (inner + 2)}{v}")
            ok, note = self.syntax_check()
            if note:
                lines.append(row(f"{'✓' if ok else '✗'} {note}"))
        lines.append(row(""))
        lines.append(row(self.commands()))
        lines.append(f"{ll}{h * (inner + 2)}{lr}")
        return "\n".join(lines)

    def _body_lines(self) -> list[str]:
        inner = PREVIEW_BOX_WIDTH - 4
        text = self.content or ""
        lines = text.splitlines()

        if self.type == "markdown":
            rendered = _render_markdown(text, inner - 2)
            return rendered[:MAX_LINES]

        if self.type == "json":
            try:
                pretty = _json.dumps(_json.loads(text), indent=2, ensure_ascii=False)
            except _json.JSONDecodeError:
                pass
            else:
                lines = pretty.splitlines()

        body = lines[:MAX_LINES]
        if len(lines) > MAX_LINES:
            body.append(f"… (외 {len(lines) - MAX_LINES}줄)  /preview <파일> 로 전체")
        return body

    @staticmethod
    def _glyphs() -> tuple[str, str, str, str, str, str]:
        if brand.supports_unicode():
            return "┌", "┐", "└", "┘", "│", "─"
        return "+", "+", "+", "+", "|", "-"


def _resolve_target(arg: str) -> Path | None:
    """/preview 인자를 경로로 바꾼다. 번호면 이력에서 찾는다."""
    from wx.ui.history import get_history

    if not arg:
        entries = get_history().entries("today") or get_history().entries("all")
        return Path(entries[0].path) if entries else None

    candidate = Path(arg).expanduser()
    if candidate.is_file():
        return candidate

    if arg.isdecimal():
        entries = get_history().entries("all")
        idx = int(arg) - 1
        if 0 <= idx < len(entries):
            return Path(entries[idx].path)
        return None

    matches = get_history().find(arg)
    return Path(matches[0].path) if matches else None


def run_preview_command(arg: str = "", *, run: bool = False, color: bool = False) -> str:
    """`/preview`, `/preview <번호|파일>` 처리. 출력 문자열을 돌려준다."""
    arg = (arg or "").strip()
    if arg == "--run":
        arg, run = "", True

    target = _resolve_target(arg)
    preview = get_preview()

    if target is None:
        if arg:
            return f"미리보기 대상을 찾을 수 없습니다: {arg}"
        return "미리보기: 없음 (생성된 파일이 없습니다)"

    if not preview.show_file(target):
        return f"미리보기를 열 수 없습니다: {target}"

    if run:
        ok, output = preview.run_python()
        header = "실행 결과" if ok else "실행 실패"
        return f"{preview.render()}\n\n  [{header}]\n{output}"

    return preview.render()


# 전역 미리보기 인스턴스
_preview: Preview | None = None


def get_preview() -> Preview:
    """전역 미리보기 인스턴스."""
    global _preview
    if _preview is None:
        _preview = Preview()
    return _preview


def set_preview(preview: Preview) -> None:
    """전역 미리보기를 갈아 끼운다 (테스트용)."""
    global _preview
    _preview = preview


def auto_preview_enabled(cfg=None) -> bool:
    """config.json의 ui.auto_preview. 기본은 켜짐.

    끄려면 config.json에 {"ui": {"auto_preview": false}}.
    """
    if cfg is None:
        return True
    ui = getattr(cfg, "ui", None)
    if not isinstance(ui, dict):
        return True
    return bool(ui.get("auto_preview", True))


def show_last_created(cfg=None) -> str:
    """파일 쓰기 직후 자동 미리보기. 이력이 비면 빈 문자열."""
    if not auto_preview_enabled(cfg):
        return ""
    from wx.ui.history import get_history

    entries = get_history().entries("today") or get_history().entries("all")
    if not entries:
        return ""
    return run_preview_command(entries[0].path)