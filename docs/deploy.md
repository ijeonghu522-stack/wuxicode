# WUXICODE 배포 가이드

`web/` 폴더째 올리면 끝입니다. 빌드 스텝 없습니다 — HTML/CSS/JS 세 파일이고
서버도 필요 없습니다.

---

## 먼저, 무료 도메인에 대해 정직하게

**무료 TLD(최상위 도메인)는 더 이상 없습니다.**

Freenom이 `.tk` `.ml` `.ga` `.cf` `.gq` 를 무료로 줬었는데, 2023년 Meta가
피싱·사이버스쿼팅 비중을 근거로 소송을 넣었고 Freenom이 새 등록을 중단,
2024년에 서비스가 종료됐습니다. 그 뒤 그 자리에 들어온 곳은 없고, 다른
Registrar도 규모 있는 무료 TLD를 내놓지 않고 있습니다.

그래서 지금 선택지는 셋입니다.

| | 주소 형태 | 비용 | 소유 | 이전 가능 |
|---|---|---|---|---|
| **호스팅 제공자 서브도메인** | `wuxicode.pages.dev` | 무료 | ❌ | ❌ |
| **EU.org** | `wuxicode.eu.org` | 무료 | ❌ (서브도메인) | ❌ |
| **실제 도메인** | `wuxicode.com` | 연 ~$10 | ✅ | ✅ |

서브도메인은 안 됩니다 — 도메인 자체를 당신 것이 아니고, 플랫폼이 언젠가
끊을 수 있고, URL에 `pages.dev`가 박혀 있습니다. **포트폴리오나 개인
프로젝트에는 그럴듯한데, 유료 라이선스를 파는 제품 URL로는 브랜딩에 갉아먹힙니다.**

그래서 제 추천은 이겁니다:

1. **지금 바로** 무료로 올려서 확인 (Cloudflare Pages)
2. 나중에 진짜 도메인 사서 붙이기 — 무료 호스팅에도 커스텀 도메인 붙일 수 있음

---

## 옵션 1 — Cloudflare Pages (추천)

무료 티어에 대역폭 무제한이고 CDN이 전 세계 엣지입니다. 나중에 도메인 사면
설정에서 CNAME 하나만 바꾸면 됩니다. 카드 등록도 안 함.

1. [dash.cloudflare.com](https://dash.cloudflare.com) 가입
2. **Workers & Pages** → **Create** → **Pages** → **Upload assets**
3. 프로젝트 이름: `wuxicode` (없으면 `wxcode`, `wuxicode-dev` 순)
4. 드래그앤드롭으로 **`web` 폴더 내용물**을 올립니다
   (`web` 폴더 자체가 아니라 **안의 파일들**)
5. 끝. `https://wuxicode.pages.dev`

`_headers` 파일이 CSP·캐시 정책까지 적용해 줍니다. 그래서 `index.html`은
`no-cache` — 버전 올렸는데 방문자가 옛 페이지를 계속 보는 일이 없습니다.

CLI로 하려면:

```powershell
npm i -g wrangler
wrangler pages project create wuxicode
wrangler pages deploy web --project-name wuxicode
```

## 옵션 2 — GitHub Pages

이미 GitHub 계정이 있으니 가장 빠릅니다.

저장소 **Settings → Pages → Source: GitHub Actions** 로 설정하면 끝입니다.
워크플로가 이미 `.github/workflows/pages.yml` 에 있고, `web/**` 가 바뀔 때만
자동 배포하며 구조 검사부터 돌립니다.

주소: `https://ijeonghu522-stack.github.io/wuxicode/`

## 옵션 3 — Netlify

```powershell
# 저장소 루트에서
netlify deploy --dir=web --prod
```

`netlify.toml` 에 빌드 명령이 `python web/check_site.py` 로 되어 있어서 깨진
자산 경로가 배포 전에 걸립니다.

## 옵션 4 — Vercel

```powershell
npm i -g vercel
vercel --prod web
```

Framework preset은 **Other**, 출력 디렉터리는 `web`.

---

## 도메인 후보

`.pages.dev` 에는 짧을수록 좋습니다. 가용성은 대시보드에서 직접 확인해야
합니다 (DNS 레코드가 아니라 계정 등록이라 밖에서는 조회가 안 됩니다).

| 후보 | 주소 | 느낌 |
|---|---|---|
| `wuxicode` | `wuxicode.pages.dev` | 제품명 그대로. 가장 깔끔 |
| `wxcode` | `wxcode.pages.dev` | 짧음. 로고의 WX와 맞음 |
| `wuxicode-dev` | `wuxicode-dev.pages.dev` | 빌드용 |

안 쓰면 `ijeonghu522-stack.github.io/wuxicode/` 가 됩니다.

### 나중에 진짜 도메인을 사면

**.com** — 가장 신뢰가 되고 사람이 치기 쉽습니다. 레지스트rar 비교상
$10~13/년. `.dev` 는 HTTPS 강제라 개발자 대상이라는 신호가 강해서 제품에는
약간 과합니다. `.app` 은 Google 소유라 같은 취향.

Cloudflare Registrar가 원가에 파는 편이라 가장 저렴하고, 이미 Pages를 쓰니
DNS가 한 계정에 모입니다. 도메인 사면 Pages 프로젝트 → **Custom domains** →
도메인 입력 → CNAME 두 개 설정.

---

## 배포 전 확인

```powershell
python web\check_site.py        # 태그, 자산, 앵커, CSS 변수
python scripts\sync_site.py     # 버전 + 아이콘을 web/ 에 복사
python -m http.server -d web 8080   # 로컬에서 실제 확인
```

`check_site.py` 는 `web/*.html` 전부를 검사합니다 — 404 페이지가 자산 경로가
깨져 있어도 인덱스만큼은 똑같이 배포되니까요. 루트 절대 경로(`/assets/...`)도
경고합니다. 호스트에선 되지만 `file://` 로 열면 깨집니다.

---

## 버전 동기화

정적 페이지는 자기가 어떤 빌드를 설명하고 있는지 알 수 없습니다.
`scripts/sync_site.py` 가 `version.json` 과 아이콘을 `web/` 에 복사하고,
페이지가 실행 시 `web/version.json` 을 읽어 푸터·히어로의 버전을 덮어씁니다.

`sync_site.py` 를 빼먹으면 페이지가 이미 구버전 EXE를 광고합니다.
`python scripts/daily.py` 가 마지막 단계로 자동 실행합니다.
