# WUXICODE EXE 빌드 가이드

`scripts/build_exe.ps1` 하나로 `dist/WUXICODE.exe`가 나옵니다. 아이콘이 없으면
먼저 만들고, 없으면 설치하고, 빌드하고, 실행해서 확인까지 합니다.

## 결과물

| 항목 | 값 |
|---|---|
| 경로 | `dist/WUXICODE.exe` |
| 크기 | 약 21 MB |
| 아이콘 | 보라-시안 WX 로고 (16/24/32/48/64/128/256) |
| 서브시스템 | console (터미널 창 유지) |
| 네이티브 코어 | 번들됨 (`_wx_core`, `--doctor`에서 확인) |
| 버전 속성 | `version.json`에서 자동 생성 |

## 실행

```powershell
# 저장소 루트에서
powershell -ExecutionPolicy Bypass -File scripts\build_exe.ps1
```

7단계가 돌고 마지막에 자동 검증까지 합니다.

```
==> Preflight          python 3.12.10 · native core OK
==> Icon               assets\icon.ico (46 KB)
==> Version metadata   version 0.2.0 dev -> build\wx_version_info.txt
==> Version sources    version sources agree: 0.2.0
==> PyInstaller        pyinstaller 6.22.3
==> Verify             WUXICODE.exe 20.9 MB (0.4 min)
  [OK] runs: WUXICODE 0.2.0
  [OK] native core inside the bundle
```

## 옵션

```powershell
scripts\build_exe.ps1 -Clean         # build/ dist/ 를 지우고 처음부터
scripts\build_exe.ps1 -Name WX       # 다른 이름으로
scripts\build_exe.ps1 -Windowed      # 콘솔 창 숨김 (GUI 준비되면)
scripts\build_exe.ps1 -SkipIcon      # 아이콘 재생성 건너뛰기
scripts\build_exe.ps1 -Icon assets\icon.ico
```

## 수동으로 나눠서 하기

```powershell
# 1) 버전 확인
python scripts\check_version.py

# 2) 아이콘 (assets/logo.svg -> icon.ico + PNG)
pip install pillow
python scripts\make_icon.py

# 3) 빌드
powershell -ExecutionPolicy Bypass -File scripts\build_exe.ps1

# 4) 실행
.\dist\WUXICODE.exe --doctor
```

## 사전 조건

- Python 3.10 이상 (개발 환경: 3.12.10)
- `pip install -e .` 로 네이티브 코어(`_wx_core`) 빌드 완료
  - CMake 3.24 이상, MSVC 필요
  - 안 되어 있으면 스크립트가 경고하고 계속 진행하지만, `--doctor`의
    tokenizer/SSE가 비활성화됩니다
- PyInstaller 6.x (없으면 스크립트가 설치)

## 버전 관리

버전은 `version.json` 하나에서 나옵니다.

```
version.json        ← 유일한 원본
    ├─ CMakeLists.txt   → WX_VERSION_STRING를 _wx_core에 컴파일 타임으로 굽는다
    ├─ pyproject.toml   → pip / scikit-build-core (수동 관리)
    └─ wx/__init__.py   → import 시 version.json을 읽는다 (리터럴 없음)
```

예전엔 `wx/__init__.py`에 리터럴이 있어서 세 곳이 어긋났고, 실제로
`--version`이 0.1.0을 출력하는데 EXE 파일 속성은 0.2.0이었습니다. 이제 리터럴이
없어서 잊을 수 없습니다. 남은 두 곳은 `scripts/check_version.py`가 검사하고,
빌드 스크립트가 그걸 실행하므로 어긋나면 EXE를 만들지 않습니다.

```powershell
python scripts\check_version.py
```

네이티브 코어는 예전 문자열을 물고 있으므로, 버전만 올렸다면 반드시 다시 빌드해야
합니다. CMake가 `version.json`을 읽어 컴파일 타임에 굽기 때문입니다.

## 매일 도는 루프

```powershell
python scripts\daily.py                  # 전체 (학습 → 테스트 → 빌드 → 사이트)
python scripts\daily.py --skip-build     # 학습 + 테스트만 (수 초)
```

순서:

| 단계 | 하는 일 |
|---|---|
| verify versions | `version.json` / `pyproject.toml` / `wx.__version__` / `_wx_core.version()` 일치 확인 |
| learn | 최근 툴 호출을 규칙으로 증류하고 `learned-rules.md`에 게시 |
| pytest | 전체 테스트 |
| ruff | F, E9, B 규칙 (실제 버그가 나는 것들) |
| site check | 웹사이트 구조 |
| native rebuild | `cpp/`나 `version.json`이 바뀐 경우에만 |
| build EXE | PyInstaller + `--doctor` 자동 검증 |
| refresh site | `web/`에 버전과 아이콘 동기화 |

매 실행마다 `docs/reports/YYYYMMDD-HHMM.json`에 결과가 쌓입니다. 성공한 단계만
아니라 마지막 몇 줄 출력까지 들어가서, 나중에 "그날 왜 깨졌지"를 되짚을 수
있습니다.

스케줄에 걸려면 (PowerShell):

```powershell
$action = New-ScheduledTaskAction -Execute "python" `
  -Argument "C:\Users\baske\OneDrive\Documents\WUXICODE\scripts\daily.py" `
  -WorkingDirectory "C:\Users\baske\OneDrive\Documents\WUXICODE"
Register-ScheduledTask -TaskName "WUXICODE daily" -Action $action `
  -Trigger (New-ScheduledTaskTrigger -Daily -At 3am)
```

## WX-1 붙이는 법

EXE는 모델을 담지 않습니다. GGUF → Ollama 로 돌리고 엔드포인트를 알려주면 됩니다.

```powershell
# 1) 모델 등록 (예시)
ollama create wx-1 "C:\models\wx-1-q4.gguf"

# 2) 실행 중 주소 지정
$env:WX_LLM_BASE_URL = "http://127.0.0.1:11434/v1"
$env:WX_LLM_MODEL   = "wx-1"
.\dist\WUXICODE.exe
```

또는 `%USERPROFILE%\.wuxicode\config.json` 에 `base_url` / `model` 을 넣습니다
(해당 파일이 EXE 설정보다 우선합니다).

## 구성 (scripts/build_exe.ps1)

| 플래그 | 이유 |
|---|---|
| `--paths python`, `--paths .` | `cli.py`가 스크립트로 실행되므로 `sys.path[0]`가 `python\wx`. 없으면 `from wx import ...` 실패. `.`는 `license_server`용 |
| `--add-data python/wx/config.json;wx` | `config.py`가 `Path(__file__).parent / "config.json"`로 기본값을 찾음 → onefile에선 `_MEIPASS\wx\config.json` |
| `--collect-submodules wx` | `wx.learn` / `wx.context` / `wx.ui`가 `__getattr__`와 함수 안에서 지연 import |
| `--hidden-import _wx_core` | `wx.require_core()` 안에서만 import되는 확장 모듈 |
| `--exclude-module torch,transformers,numpy,...` | 런타임 의존성 아님. site-packages에 남아 있으면 EXE만 부풀어 오름 |
| `--version-file` | `version.json`에서 생성. 파일 속성의 버전과 네이티브 코어가 같은 값을 갖게 됨 |
| `--noupx` | UPX로 묶으면 AV 오탐이 심함 |

## 아이콘 파이프라인

`assets/logo.svg`가 원본입니다. `scripts/make_icon.py`가 두 경로를 시도합니다.

1. **cairosvg** — SVG를 그대로 래스터화
2. **Pillow** — 같은 그림을 코드로 다시 그림

Windows에서 cairosvg는 네이티브 `libcairo` DLL이 필요해서 pip로 설치가 안
됩니다. 그래서 fallback이 필수입니다. 어느 경로로 돌든 같은 결과가 나오도록
기하 수치를 양쪽에 맞췄습니다.

**크기별로 다시 그립니다.** 1024에서 16으로 줄이면 카운터와 W 골이 회색 죽이
됩니다. 각 엔트리를 그 크기에서 4x 슈퍼샘플링해 직접 그리고, 추가로:

| 크기 | 스트로크 | 장식 점 | 글로우 |
|---|---|---|---|
| ≤16 | ×1.75 | 제거 | 제거 |
| ≤24 | ×1.45 | 제거 | 60% |
| ≤32 | ×1.3 | 80% | 60% |
| ≥48 | ×1.0 | 100% | 100% |

## 웹사이트

`web/` 은 빌드 스텝이 없습니다. HTML/CSS/JS 세 파일이고 그대로 열립니다.

```powershell
python web\check_site.py       # 구조 검사 (없는 자산, 닫히지 않은 태그, CSS 변수)
python scripts\sync_site.py    # 버전 + 아이콘을 web/ 으로 복사
```

`sync_site.py` 를 돌리지 않으면 페이지가 이미 구버전 EXE를 광고할 수 있습니다.
`daily.py` 가 마지막 단계로 자동 실행합니다.

배포는 `web/` 폴더를 그대로 올리면 됩니다. 서버 불필요.

## 트러블슈팅

**`ModuleNotFoundError: wx`**
`--paths python`이 빠졌거나 저장소 루트에서 실행한 게 아닙니다.

**`CoreUnavailable: wx._wx_core is not importable`**
네이티브 코어가 안 빌드됨. `pip install -e .` (CMake + MSVC 필요).

**`MISMATCH: extension reports 0.1.0, package reports 0.2.0`**
네이티브 코어가 오래됨. `pip install -e .` 로 다시 빌드하세요.

**`config.json`을 못 읽음**
`--add-data` 누락. `dist\WUXICODE.exe --doctor`의 `base_url`이 빈 값이면
확인하세요.

**구동 시 `%TEMP%`에 풀리는 게 보임**
onefile 특성. 정상입니다. 시작이 2~4초 걸립니다.

**Windows Defender가 막음**
unsigned 바이너리라서 그렇습니다. `--noupx`로 완화되지만 서명 없이는
SmartScreen 경고가 남습니다.

## 한계

- 콘솔 EXE입니다. GUI 아님.
- onefile이라 시작할 때마다 `%TEMP%`로 풀립니다 (2~4초).
- WX-1을 담지 않습니다. Ollama/OpenAI 호환 엔드포인트를 바라봅니다.
- 서명 없음.

## 다음 단계

- 설치 프로그램 (Inno Setup) — 참고: `Default Project\VALTRACKER\VALTRACKER.iss`
- GUI (Tauri)
- 코드 서명
- 자동 업데이트
