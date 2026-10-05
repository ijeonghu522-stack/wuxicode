# LICENSE-PLAN — WUXICODE 상업 라이선스 계획

이 문서는 WUXICODE가 어떤 라이선스로 배포되고, 각 티어가 무엇을 허용하는지,
그리고 서버가 그것을 어떻게 강제하는지에 대한 권위 있는 문서다.

코드와의 관계:

- **법적 텍스트** → `LICENSE` (WX Commercial License v0.2.0)
- **메타데이터** → `version.json` (`license.spdx` = `LicenseRef-WX-Commercial`)
- **요금제/엔티틀먼트 단일 원천** → `license_server/tiers.py`
- **이 문서** → 사람이 읽는 상업 조약

`LICENSE`와 이 문서가 충돌하면 `LICENSE`가 우선한다. `tiers.py`와 이 문서의
숫자가 충돌하면 `tiers.py`가 단일 원천이며, 이 문서는 그때 갱신해야 한다.


## 1. 왜 MIT를 버렸나

0.1.0은 MIT였다. 그 상태로는 다음이 불가능했다:

- WX PLUS / WX ZEN 요금을 실제로 청구할 수 없음 (재배포·파생 배포가 자유)
- 라이선스 키, 토큰 계량, 동시 실행 제한을 법적 근거 없이 걸 수 없음
- 소스를 본 경쟁자가 유료 티어와 동일한 제품을 다시 배포할 수 있음
- 상업적 사용과 개인 사용의 경계가 계약 문서로 존재하지 않음

0.2.0부터 **source-available 상업 라이선스**로 전환한다. OSI open source는
아니며, 그걸 주장하지도 않는다.


## 2. SPDX 처리

커스텀 라이선스는 OSI-approved 식별자가 없으므로 SPDX custom-ref 형식을 쓴다.

| 필드 | 값 |
|---|---|
| SPDX 식별자 | `LicenseRef-WX-Commercial` |
| 라이선스 버전 | `0.2.0` |
| 파일 | `LICENSE` |
| PEP 639 | `license = "LicenseRef-WX-Commercial"`, `license-files = ["LICENSE"]` |

`LicenseRef-` 접두사는 SPDX 사양에서 커스텀 라이선스에 대해 예약된 접두사다.
이 식별자를 쓰면 PyPI / wheel 메타데이터가 라이선스를 기계가 읽을 수 있게
남으면서도 "어떤 오픈소스 라이선스다"라고 잘못 표기하지 않는다.

> 빌드 파이프라인 주의: `LicenseRef-` 표기는 setuptools ≥ 77 (PEP 639)이 필요하다.
> 이전 setuptools는 이 값을 거부하고 빌드를 실패시킨다. scikit-build-core는
> 프론트엔드라 해당 버전으로 해석된다. **한 번 확인해둬라.**


## 3. 티어별 허용 범위

`license_server/tiers.py`의 `TIERS`가 값을 정의한다. 이 표는 그 값을
사람이 읽는 정리이며, 숫자가 어긋나면 `tiers.py`를 고친 뒤 여기를 맞춰라.

| | **WX (Free)** | **WX PLUS** | **WX ZEN** |
|---|---|---|---|
| 키 접두사 | (키 불필요) | `WXP-` | `WXZ-` |
| 가격 | 0원 / $0 | 13,000원 / $10 | 20,000원 / $15 |
| 일일 토큰 | 50,000 | 500,000 | 2,000,000 |
| 월간 토큰 | 1,000,000 | 10,000,000 | 50,000,000 |
| 동시 세션 | 1 | 3 | 10 |
| 파일 쓰기/편집 | ✗ | ✓ | ✓ |
| bash 실행 | ✗ | ✓ | ✓ |
| git 도구 | ✗ | ✓ | ✓ |
| preview | ✗ | ✓ | ✓ |
| 모델 | `wx-1` | + `space-bunny-free` | + `qwen/qwen3.8-27b`, `gpt-4o-mini`, `claude-sonnet` |
| 우선 지원 | ✗ | ✗ | ✓ |
| **사용 범위** | **개인·비영리** | **상업·프로덕션 허용** | **상업·프로덕션 허용** |

핵심 분기점은 하나다: **WX Free는 개인·비영리 전용, PLUS와 ZEN은 상업 사용이
라이선스로 명시적으로 허용된다.** 유료 티어 없이는 회사 업무, 고객 서비스,
프로덕션 파이프라인에 쓸 수 없다.

 읽기 계열 도구(`read`, `glob`, `grep`, `list`)는 Free에서도 열린다. 제품이
시연되지 않으면 티어의 가치를 확인할 방법이 없기 때문이다.


## 4. 강제 방식

라이선스는 문서와 서버 양쪽에서 작동한다.

**문서 측 (LICENSE §2, §3)**
재배포 금지, 파생 제품 금지, 라이선스 표기 제거 금지, 단일 키 공유 금지.

**서버 측 (`python/wx/auth/`)**
1. 키가 `WXP-` / `WXZ-` 접두사를 갖는지 확인 → `tier_from_prefix`
2. 티어별 엔티틀먼트를 서버가 서명해 토큰으로 발급
3. 클라이언트는 **서버가 서명한 토큰의 엔티틀먼트만 신뢰**한다. 클라이언트에
   하드코딩된 티어 판정은 신뢰 경계 밖이다
4. `TierConfig.allows_tool()` 이 도구 단위 권한을 판정. 알 수 없는 도구는
   보수적으로 거부 (`False`)
5. 일일/월간 토큰은 서버 계정에서 차감, `max_concurrent` 초과 시 거부

`can_run_bash`와 `can_use_git`이 Free에서 꺼진 이유: 셸 실행과 git push는
에이전트가 사용자 파일시스템과 원격 저장소에 손을 댈 수 있는 지점이다. Free 티어
에서는 이 두 권한이 없으면 "설치 후 튀어나온 첫 턴에 임의 코드 실행" 위험이
구조적으로 사라진다.


## 5. 키 발급 수명주기

```
발급: key_prefix + "-" + random
  ↓
서명: 서버가 entitlements + expiry 를 서명한 토큰 발급
  ↓
검증: 클라이언트가 공개키로 서명 확인 후 캐시 (ToolCache, Phase 1B)
  ↓
만료: 갱신 요청 → 새 토큰, 구 토큰은 캐시 윈도우 종료 후 폐기
```

Phase 1B에서 C++ 캐시(`cpp/core/cache/`)와 git probe(`git_probe.cpp`)가
구축되어 토큰 캐시와 라이선스 파일 변경 탐지가 준비되었다. 서명 토큰의
오프라인 검증은 **Phase 2 범위**다.


## 6. 다음 단계

- [ ] Phase 2: 서명 토큰 발급/검증 경로 구현 (Tier → entitlements)
- [ ] Phase 2: `allows_tool()` 배선을 실제 도구 디스패처에 연결
- [ ] setuptools ≥ 77 확인 후 `pip wheel` 1회로 PEP 639 메타데이터 검증
- [ ] README에 라이선스 요약 배너 추가 (지금 `README.md:88`에 Phase 5 게이트로만 존재)
- [ ] 배포 시 `LICENSE`를 웹 약관 페이지와 동일 텍스트로 동기화
- [ ] 유료 티어Terms 페이지를 wuxicode.dev에 게시

## 7. 변경 이력

| 버전 | 변경 |
|---|---|
| 0.1.0 | MIT 라이선스. 상업 판매 불가, 키/게이트 법적 근거 없음 |
| 0.2.0 | `LICENSE`를 WX Commercial License로 교체. `LicenseRef-WX-Commercial` 채택. 티어별 상업 사용 범위를 §3에 명시 |