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
│ ● ● ● │ wx │ +                    ← 탭                     │
├───────────────┬──────────────────────────────────────────┤
│ 세션 이름      │  대화 │ 검토 │ 파일                        │
│               ├──────────────────────────────────────────┤
│ 메시지 목록    │  모델 선택    🔍 파일 필터      base_url  │
│               │                                          │
│               │  (어두운 콘솔)                              │
│               │                                          │
│ 모델 · 추론 ⬆ │                                          │
├───────────────┴──────────────────────────────────────────┤
│ model · provider │ workspace │ in/out tokens · latency     │
└──────────────────────────────────────────────────────────┘
```

왼쪽은 대화의 요약, 오른쪽은 같은 대화의 전문입니다. 하나의 `turns` 배열이
둘 다 그립니다.

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

둘 다 창 안에 콘솔이 없는 상태에서 발견됐습니다. 전자는 흰 사각형,
후자는 조용한 종료입니다. 그래서 GUI 는 `%LOCALAPPDATA%\wuxicode\gui.log`
에 수명주기를 씁니다 — 창이 안 뜨면 그 파일이 유일한 단서입니다.

## 한계

- Windows 전용 (WebView2 + pythonnet)
- 탭은 UI만 있고 세션은 공유 — 탭마다 독립 컨텍스트는 아직
- 파일 패널은 읽기 전용
- 코드 뷰 / diff 뷰 없음 (Phase 4)
- 단일 인스턴스 (두 번째 실행은 포트를 새 잡음)
