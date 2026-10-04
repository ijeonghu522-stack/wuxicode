"""WUXICODE — 터미널 표시 너비 계산.

터미널은 한글이랑 CJK 문자를 2칸으로 그린다. 파이썬의 len()은
코드포인트 개수만 세므로, 한글이 섞인 문자열로 박스를 그리면
오른쪽 테두리가 딸려간다. 여기 있는 함수들은 실제 표시 칸 수를
기준으로 패딩하기 위한 것이다.

주의: TUI 박스 안의 문자열에는 ANSI 색상 코드가 들어있다. 색상
코드는 화면에 아무것도 그리지 않지만 len()으로는 1칸씩 세어지므로,
str_width()/pad_to()는 코드-sequences를 무시한다. 색상 있는
문자열에도 그대로 쓸 수 있다.
"""

import re
import unicodedata

# CSI/SGR 등 ANSI 제어 시퀀스 (예: \x1b[36m)
_ANSI_RE = re.compile(r'\x1b\[[0-9;]*[A-Za-z]')


def strip_ansi(s: str) -> str:
    """ANSI 제어 시퀀스를 제거한다."""
    return _ANSI_RE.sub('', s)


def char_width(ch: str) -> int:
    """터미널에서 문자가 차지하는 칸 수."""
    if unicodedata.east_asian_width(ch) in ('W', 'F'):
        return 2
    if unicodedata.combining(ch):
        return 0
    return 1


def str_width(s: str) -> int:
    """문자열의 터미널 표시 너비."""
    return sum(char_width(c) for c in strip_ansi(s))


def truncate_to(s: str, width: int) -> str:
    """표시 너비 기준으로 자른다. 잘리면 … 를 붙인다.

    박스 안에서 넘쳐나면 오른쪽 테두리가 다음 줄로 밀린다.
    base_url 같은 긴 값 때문에 필요.
    """
    if str_width(s) <= width:
        return s
    if width <= 1:
        return '…'

    # ANSI를 보존하면서 표시 칸 수만 센다.
    out = []
    used = 0
    limit = width - 1  # 마지막 칸은 '…' 자리에 남긴다
    i = 0
    while i < len(s):
        m = _ANSI_RE.match(s, i)
        if m:
            out.append(m.group())
            i = m.end()
            continue
        w = char_width(s[i])
        if used + w > limit:
            break
        out.append(s[i])
        used += w
        i += 1
    return ''.join(out) + '…'


def pad_to(s: str, width: int, align: str = 'left') -> str:
    """표시 너비 기준으로 패딩.

    순수 패딩이다. 문자열을 자르지 않는다. 박스 안에서 넘쳐나면
    테두리가 깨지므로, 박스를 그릴 때는 truncate_to()를 먼저 쓴다:

        pad_to(truncate_to(text, width), width)
    """
    w = str_width(s)
    pad = max(0, width - w)
    if align == 'left':
        return s + ' ' * pad
    elif align == 'right':
        return ' ' * pad + s
    else:  # center
        left = pad // 2
        right = pad - left
        return ' ' * left + s + ' ' * right