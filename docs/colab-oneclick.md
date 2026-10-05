# WX-1 자동 학습 — Colab One-Click 가이드

## 개요
이 노트북 하나만 열어서 **Run all** 누르면 끝입니다.
- 데이터셋 자동 확장 (5개 → 300개)
- WX-1-coder 학습 (약 2시간, T4/A100)
- LoRA 병합
- GGUF Q4_K_M 양자화
- Google Drive 자동 저장 + 브라우저 다운로드

**로컬 컴퓨터 불필요. 손 안 대고 완전 자동.**

---

## 사전 준비
1. **Google 계정** (Colab + Drive 사용)
2. **GitHub 저장소**: `ijeonghu522-stack/wuxicode` (public 또는 본인 fork)
   - `training/train_qlora.py`
   - `training/config/wx-1-coder.yaml`
   - `training/merge_adapter.py`
   - `training/auto_build_datasets.py`
   - `training/fix_train_qlora.py` 이 안에 있어야 함

---

## 실행 순서

### 1. Colab에서 노트북 열기
- `training/WX1_Colab_OneClick.ipynb` 업로드 또는 GitHub에서 직접 열기
- **런타임 → 런타임 유형 변경 → GPU (T4 또는 A100) 선택**

### 2. Run All
- 메뉴: **런타임 → 모두 실행** 또는 `Ctrl+F9`
- **셀 4(라이브러리 설치) 완료 후** 반드시 **런타임 재시작** (셀 5 안내 따름)

### 3. 대기 (약 2시간)
| 단계 | 예상 시간 | 비고 |
|------|-----------|------|
| 라이브러리 설치 | 1-2분 | 셀 4 |
| 런타임 재시작 | 30초 | 셀 5 수동 클릭 |
| Drive 마운트 | 10초 | 셀 6 인증 필요 |
| 리포지토리 클론 | 30초 | 셀 7 |
| train_qlora.py 패치 | 5초 | 셀 8 |
| 데이터 확장 | 10초 | 셀 9 |
| 설정 변경 | 5초 | 셀 10 |
| **학습 (메인)** | **~2시간** | 셀 11 |
| LoRA 백업 | 10초 | 셀 12 |
| 병합 | 1-2분 | 셀 13 |
| GGUF 변환 | 2-3분 | 셀 14 |
| Q4_K_M 양자화 | 1분 | 셀 15 |
| Drive 저장 + 다운로드 | 30초 | 셀 16 |

### 4. 결과 확인
- **Google Drive**: `/MyDrive/wx-1-coder-q4.gguf` (약 4-5GB)
- **브라우저 다운로드**: 자동으로 `wx-1-coder-q4.gguf` 다운로드 시작

---

## 중요 포인트

### ⚠️ 런타임 재시작 (필수)
셀 4 실행 후 **반드시** `런타임 → 런타임 다시 시작` 클릭하세요.
- numpy/transformers/trl 버전 충돌 방지
- 재시작 후 셀 6부터 계속 실행

### 🔑 Drive 마운트 인증
셀 6 실행 시 권한 허용 팝업 → **Google 계정 선택 → 허용**

### 📦 LoRA 중간 백업 (셀 12)
학습 완료 즉시 `wx-1-coder-lora.zip` 이 Drive에 저장됨.
- 학습 중단이어도 LoRA는 살릴 수 있음
- 병합/양자화 다시 할 때 사용 가능

---

## 트러블슈팅

| 증상 | 원인 | 해결 |
|------|------|------|
| `IndentationError` | 노트북 들여쓰기 깨짐 | 원본 노트북 다시 업로드 |
| `ModuleNotFoundError: trl` | 런타임 재시작 안 함 | 셀 4 후 런타임 재시작 필수 |
| `max_samples: 5` 그대로 학습됨 | config 수정 안 됨 | 셀 10 실행 확인 |
| `merge_adapter.py` 없음 | 리포지토리 파일 누락 | GitHub에 파일 푸시 확인 |
| GGUF 변환 실패 | protobuf 버전 | 셀 14 `protobuf>=5.29.0` 설치 확인 |
| 양자화 실패 | llama.cpp 빌드 안 됨 | 셀 15 `make -j4 llama-quantize` 확인 |
| Drive 저장 안 됨 | 마운트 안 됨 | 셀 6 재실행 |

---

## 파일 구조
```
wuxicode/
├── training/
│   ├── train_qlora.py          # 원본 학습 스크립트 (패치 대상)
│   ├── config/wx-1-coder.yaml  # 학습 설정 (max_samples: 5 → 300)
│   ├── merge_adapter.py        # LoRA 병합 (필수!)
│   ├── auto_build_datasets.py  # 5개 → 300개 자동 확장
│   ├── fix_train_qlora.py      # 패치 스크립트 (참고용)
│   └── WX1_Colab_OneClick.ipynb # 이 노트북
├── datasets/
│   └── wx_coder.jsonl          # 생성된 데이터셋 (300줄)
└── output/
    ├── wx-1-coder-lora/        # LoRA 어댑터
    ├── wx-1-coder-merged/      # 병합된 모델
    ├── wx-1-coder-f16.gguf     # FP16 GGUF
    └── wx-1-coder-q4.gguf      # 최종 Q4_K_M (배포용)
```

---

## 커스텀 설정

### 모델 변경
```python
# 셀 2에서 변경
TRAIN_MODEL = "wx-1-coder"
# 병합 시 base 모델도 함께 변경 필요 (셀 13)
# --base Qwen/Qwen2.5-Coder-7B-Instruct
```

### 샘플 수 변경
```python
# 셀 2에서 변경
MAX_SAMPLES = 300  # 100, 500, 1000 등
```

### GitHub 저장소 변경
```python
# 셀 2에서 변경
GITHUB_USER = "your-username"
GITHUB_REPO = "your-repo"
```

---

## 라이선스
- 베이스 모델: Qwen2.5-Coder-7B-Instruct (Apache 2.0)
- 학습 코드/데이터: MIT
- 출력 모델: Apache 2.0 파생물

---

## 업데이트 이력
- v1.0: 초기 버전 (들여쓰기 수정, 최신 라이브러리, merge_adapter.py 포함)
- 이전 버전 실패 원인: IndentationError, 구버전 라이브러리, merge_adapter.py 누락, 검증 없음