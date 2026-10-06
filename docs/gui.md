# WUXICODE GUI

The desktop window. A windowed EXE, one file, no Python required on the
target machine.

```
dist\WUXICODE-GUI.exe
```

## 왜 pywebview인가

| | Tauri | Electron | **pywebview** |
|---|---|---|---|
| 툴체인 | Rust + Node | Node | 없음 |
| 번들 크기 | ~5MB | ~150MB | **28MB** |
| 브라우저 | 사용자가 설치 | 같이 낌 | **OS에 이미 있음** |
| 코드베이스 | 프론트/백엔드 분리 | 분리 | **파이썬 재사용** |

Windows 10/11에는 WebView2 런타임이 기본 설치되어 있습니다. Electron은 그
브라우저를 150MB에 다시 넣어 보내고, Tauri는 Rust 툴체인을 새로 요구합니다.
둘 다 여기서 얻는 것이 없었습니다.

**에이전트는 재구현하지 않았습니다.** 같은 `AgentLoop`, 같은 라우터, 같은
도구를 쓰고 이벤트를 콜백으로 받는 쪽만 다릅니다. `wx.agent.loop`를 고치면
GUI는 재빌드 없이 다음 실행에서 그 수정을 받습니다.

```
python/wx/gui/
├── session.py   에이전트 소유. 한 번에 한 턴, 취소 가능.
├── pool.py      탭 → 세션. 파일 읽기와 git.
├── server.py    stdlib HTTP. 정적 파일 + 턴당 SSE 스트림 하나.
├── app.py       pywebview 창과 수명주기
└── ui/          index.html · app.css · app.js
```

웹 프레임워크를 넣지 않았습니다. 정적 디렉터리 하나와 JSON 엔드포인트 둘,
SSE 스트림 하나가 전부인 표면을 라우팅하라고 의존성을 추가할 이유가 없고,
PyInstaller가 28MB 바이너리에 동결해야 합니다.

## 실행

```powershell
# 소스에서
$env:PYTHONPATH = "python"
python -m wx.gui.app                 # 창
python -m wx.gui.app C:\path\to\repo # 특정 프로젝트
python -m wx.gui.app --browser       # 창 없이, 브라우저로

# EXE
.\dist\WUXICODE-GUI.exe
.\dist\WUXICODE-GUI.exe --doctor
```

`--doctor` 는 창이 없는 바이너리에서 상태를 확인하는 유일한 방법입니다.

## 빌드

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build_exe.ps1 -Gui
```

`-Gui` 는 `-Windowed` 를 함의하고, 이름을 `WUXICODE-GUI.exe` 로 바꿔
CLI 바이너리와 `dist/` 에 나란히 둡니다.

## 구조

```
┌──────────────────────────────────────────────────────────┐
│ ● ● ● │ wx │ wx-2 │ +                  ← 탭 = 에이전트     │
├───────────────┬──────────────────────────────────────────┤
│ 세션 이름      │ 대화 │ 변경 │ 파일 │ 검토 │ 기록           │
│               ├──────────────────────────────────────────┤
│ 메시지 목록    │ 브랜치  +3/1   🔍 파일 필터      base_url  │
│               │                                          │
│               │  (어두운 콘솔)                              │
│               │                                          │
│ 모델 · 추론 ⬆ │                                          │
├───────────────┴──────────────────────────────────────────┤
│ model · provider │ workspace │ in/out tokens · latency     │
└──────────────────────────────────────────────────────────┘
```

왼쪽은 대화의 요약, 오른쪽은 같은 대화의 전문입니다. 탭마다 `turns` 배열이
따로 있고 둘 다 그립니다.

## 탭 = 에이전트

탭 두 개가 동시에 질문하면 **같은 대화**가 되는 문제가 있었습니다. 표면의
버그가 아니라 실질적으로, `AgentLoop` 하나를 공유하면 두 턴의 델타가 한 메시지에
끼어들고 "이 파일에 뭐가 있었지"의 답이 다른 파일에 대한 답 옆에 붙습니다.

그래서 `pool.py` 가 탭 id → `Session` 을 소유하고, 모든 요청이 `?tab=N` 으로
자신의 탭을 밝힙니다. `app.js` 의 탭 상태는 전부 `Tab` 객체 안에 있고 모듈
변수에 하나도 없습니다.

창을 닫을 때는 보이는 탭이 아니라 **전체** 세션을 정리합니다. 실행 중인 턴이
남으면 도구 서브프로세스가 고아로 남고, Windows 에서는 아무도 못 보는 핸들을
들고 있게 됩니다.

## 코드 · diff

`pool.py` 가 셸을 직접 부릅니다 (`git -C … status/diff/log`, 파일은 직접
읽음). 파일 경로는 `_resolve_in()` 이 작업 디렉터리 안인지 확인합니다 —
이 서버는 셸 도구를 돌리는 프로세스에게 바이트를 넘기기 때문에, 이 검사
없이는 파일 패널이 `C:\Users\…\.ssh` 를 읽는 통로가 됩니다.

텍스트 판정은 앞 8KB 의 N 바이트, git이 쓰는 가장 싼 방법입니다. cp949 는
한국어 Windows 기본이라 한 번 더 시도합니다.

프런트는 전부 `esc()` 를 먼저 거칩니다. **이스케이프한 텍스트에만** 인라인
포맷터를 적용합니다. 순서를 바꾸면 도구 결과 안의 `<img onerror=…>` 가
스크립트 실행이 됩니다 — 그게 이 파일의 렌더러가 실제로 조심하는 이유입니다.

## SSE 대신 streaming fetch

`EventSource` 는 GET 전용입니다. 그런데 턴은 POST 입니다. 그래서
`fetch` 의 응답 body 를 읽으면서 SSE 프레임을 직접 풀어야 했습니다.

대안은 두 번째 GET 으로 받는 것인데, 그러면 POST 의 답을 받을 GET 이 같은
세션에 경쟁합니다. 직접 파싱하는 쪽이 그 관습보다 짧습니다.

## 포팅할 때 남은 함정 두 개

**1. WebView2 DLL 이 번들에 없으면 창이 빈 사각형으로 열립니다.**
`--collect-all webview` 로는 안 잡힙니다. CLR 이 **런타임에** 로드하는
.dll 이기 때문에요. 빌드 스크립트가 `site-packages` 에서 찾아서
번들 루트에 복사합니다 (CLR 이 먼저 찾는 위치).

**2. `webview.start(func=...)` 는 `func` 를 두 번 줍니다.**
첫 번째 위치 인자가 이미 `func` 이고, 창이 **닫힐 때** 실행됩니다.
"창이 떴을 때"는 `window.events.shown` 입니다.

**3. POST 본문을 읽지 않는 라우트는 keep-alive 연결을 망가뜨립니다.**
`/api/abort` 와 `/api/tab/close` 가 본문을 읽지 않아 그 바이트가 소켓에
남고, 다음 요청이 **이전 본문의 끝**부터 파싱됩니다. 겉보기엔 무관한 요청이
몇 분 뒤 랜덤하게 `WSAECONNABORTED` 로 죽습니다. 본문은 `do_POST` 맨 앞에서
한 번만 읽습니다.

셋 다 창 안에 콘솔이 없는 상태에서 발견됐습니다. 전자는 흰 사각형,
두 번째는 조용한 종료, 셋째는 아무 데도 안 보이는 간헐적 실패입니다. 그래서
GUI 는 `%LOCALAPPDATA%\wuxicode\gui.log` 에 수명주기를 씁니다 — 창이 안 뜨면
그 파일이 유일한 단서입니다.

## 플랫폼

`app.py` 의 `GUI_BACKENDS` 가 플랫폼별 백엔드를 정합니다.

| | |
|---|---|
| win32 | `edgechromium` (WebView2 + pythonnet) |
| linux | `gtk` |
| darwin | `cocoa` |

Windows 경로만 실제로 빌드·실행 검증됐습니다. 다른 둘은 이름이 아니라
검증이라는 말로 적지 않았습니다. 모르는 플랫폼은 경고만 남기고 pywebview
자동 감지에 넘깁니다.

## 한계

- Windows 경로만 빌드 검증됨
- 파일 패널은 읽기 전용
- 단일 인스턴스 (두 번째 실행은 포트를 새 잡음)
