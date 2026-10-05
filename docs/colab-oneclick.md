# WX-1 자동 학습 — One-Click

플랫폼마다 노트북이 둘입니다. 베이스 모델 크기가 다르기 때문입니다.

| 노트북 | 플랫폼 | 베이스 | 병합 RAM | 실행 |
|--------|--------|--------|----------|------|
| `WX1_Colab_OneClick.ipynb` | Google Colab | 1.5B | 4 GB | Run all |
| `WX1_Kaggle_OneClick.ipynb` | Kaggle | **7B** | 15.2 GB | Save Version |

둘 다 **셀 순서 그대로 한 번 실행**하면 끝입니다. 중간의 수동 클릭 없음.

---

## 왜 플랫폼을 나눴는가

7B 파인튜닝은 병합 단계에서 막힙니다. 병합은 CPU 에서 fp16 으로 되므로
**VRAM 이 아니라 시스템 RAM** 이 걸립니다.

| | Colab 무료 | Kaggle 무료 |
|---|---|---|
| 시스템 RAM | 12.7 GB | **29 GB** |
| GPU | T4 16 GB | T4 x2 (32 GB) |
| 7B fp16 병합 (15.2 GB) | ❌ Killed | ✅ 통과 |
| GPU 주配额 | 12 시간 | 30 시간 |
| 탭 닫고 백그라운드 실행 | ❌ | ✅ Save Version |

Kaggle 이 7B 를 가능하게 하는 이유는 RAM 29 GB 하나입니다.

---

## Colab 실행

`training/WX1_Colab_OneClick.ipynb` 열고 **런타임 → T4 GPU** → **Ctrl+F9**.

| 셀 | 단계 | 시간 |
|----|------|------|
| 1 | 설정 (베이스/샘플수/에폭) | — |
| 2 | GPU 확인 | 5초 |
| 3 | 라이브러리 설치 | 1-2분 |
| 4 | (재시작 불필요 안내 — **실행해도 되고 안 해도 됨**) | — |
| 5 | Drive 마운트 | 10초 |
| 6 | 리포지토리 클론 | 30초 |
| 7 | `train_qlora.py` 자동 패치 | 2초 |
| 8 | 데이터셋 생성 (5000개) | 10초 |
| 9 | config 조정 | 1초 |
| 10 | **학습 (메인)** | **1.5~2.5시간** |
| 11 | LoRA 즉시 백업 → Drive | 30초 |
| 12 | 병합 | 1-2분 |
| 13 | llama.cpp 설치 | 3-5분 |
| 14 | GGUF f16 변환 | 2-3분 |
| 15 | cmake 빌드 | 2-3분 |
| 16 | Q4_K_M 양자화 | 3-5분 |
| 17 | Drive 저장 + 다운로드 | 30초 |
| 18 | 완료 | — |

## Kaggle 실행 (7B)

`training/WX1_Kaggle_OneClick.ipynb` 를 Kaggle 노트북으로 연 뒤:

1. **Settings → Accelerator: GPU T4 x2**
   - P100 은 compute capability 6.0 (sm_60). PyTorch 2.7+ 에서 지원이 끊겨
     커널이 조용히 죽습니다. **반드시 T4.**
2. **Settings → Internet: On**
   - 꺼져 있으면 `pip install` 과 `git clone` 이 둘 다 실패합니다.
3. **Save Version → Run All**
   - 탭을 닫아도 백그라운드로 계속 실행됩니다.
4. 완료 후 **Output** 탭에서 `.gguf` / `.zip` 수신

CLI 로 올릴 때는 `training/kernel-metadata.json` 을 쓰고
`kaggle kernels push -p training` 하세요. `machine_shape` 키가 반드시 있어야
T4 로 잡힙니다 — 이것 없으면 P100 으로 떨어집니다.

| 셀 | 단계 | 시간 |
|----|------|------|
| 1 | 환경 확인 (GPU compute cap · RAM · 디스크) | 5초 |
| 2 | 빌드 도구 (cmake / g++) | 1분 |
| 3 | 라이브러리 설치 | 1-2분 |
| 4 | (재시작 불필요 안내) | — |
| 5 | 작업 디렉터리 분리 | 1초 |
| 6 | 리포지토리 클론 | 30초 |
| 7 | `train_qlora.py` 자동 패치 | 2초 |
| 8 | 데이터셋 생성 (5000개) | 10초 |
| 9 | config 조정 | 1초 |
| 10 | **학습 7B QLoRA (메인)** | **5~8시간** |
| 11 | LoRA 즉시 백업 | 30초 |
| 12 | 병합 (CPU fp16, 15.2GB) | 3-5분 |
| 13 | **디스크 가드** | 1초 |
| 14 | llama.cpp 설치 | 3-5분 |
| 15 | GGUF f16 변환 | 5-10분 |
| 16 | 병합 원본 삭제 (15.2GB 회수) | 30초 |
| 17 | cmake 빌드 | 2-3분 |
| 18 | Q4_K_M 양자화 | 5-10분 |
| 19 | 임시물 정리 + 산출물 이동 | 1분 |
| 20 | 산출물 목록 | 1초 |
| 21 | 완료 | — |

### Kaggle 디스크 설계

`/kaggle/working` 은 오토세이브 대상이라 **20 GB** 까지만 올라갑니다.
7B 파이프라인은 중간물이 그 이상이라 두 디렉터리로 나눴습니다.

| 경로 | 용도 | 내용 |
|------|------|------|
| `/kaggle/temp` | 중간물 (세션 종료 시 소멸) | repo 클론, 병합 모델 15.2 GB, f16 GGUF 15.2 GB, llama.cpp |
| `/kaggle/working` | 최종 산출물만 | `wx-1-coder-lora.zip`, `wx-1-coder-q4_k_m.gguf` |

합계 약 4.6 GB — 20 GB 안에 comfortably 들어갑니다.

GGUF 변환 순간만 피크가 약 30 GB 까지 오릅니다 (병합 원본 + f16 GGUF 동시 존재).
셀 13 의 가드가 **변환 직전** 여유 공간을 확인해서, 부족하면 학습 끝나고
지금 멈춥니다. 8시간 뒤에 죽는 대신.

### Kaggle 에서 없는 것 (Colab 전용 API 제거)

| Colab | Kaggle 대체 |
|-------|-------------|
| `from google.colab import drive` / `drive.mount()` | `/kaggle/working` |
| `from google.colab import files` / `files.download()` | Save Version → Output 탭 |

---

## 이 버전이 실제로 고친 것

이전 시도는 실행하면 100% 실패했습니다. 원인은 표면이 아니었습니다.

| 증상 | 진짜 원인 | 조치 |
|------|-----------|------|
| `KeyError: 'messages'` | 데이터가 `instruction/input/output` 형식인데 `train_qlora.py` 의 `format_chat()` 은 `messages` 를 요구 | 생성 스키마를 `messages` 로 통일 |
| `TrainingArguments` / `tokenizer=`.TypeError | 구버전 TRL 전용 인자 | `SFTConfig` + `processing_class` 로 교체 |
| T4 에서 CUDA 오류 | T4 는 compute capability 7.5 → **bf16 미지원**인데 `bf16=True` 하드코딩 | `torch.cuda.is_bf16_supported()` 로 자동 판정 |
| OOM | `max_seq_length: 32768` (4bit 7B 에서 과도) | 4096 으로 하향 |
| 병합 인자 | `torch_dtype` 는 transformers 4.56 부터 deprecation, v5 에서 제거 | 핀을 `>=4.56` 으로 올리고 `dtype=` 로 전환 |
| 병합 OOM | 7B fp16 병합 = RAM 15.2GB (무료 Colab 은 12.7GB) | Colab 은 1.5B, Kaggle(29GB) 은 7B |
| YAML 스캐너 에러 | 노트북의 정규식 치환이 들여쓰기를 2스페이스 → 4스페이스로 망가뜨림 | 들여쓰기 보존 `set_key()` |
| 5000개 목표 미달 | `instruction` 문자열이 (lang, topic) 마다 동일 → 중복으로 전부 버려짐 | 모디파이어 조합으로 고유성 확보 |
| Kaggle 에서 파이프라인 중단 | P100(sm_60) 은 PyTorch 2.7+ 미지원 → 커널 죽음 | 환경 확인 셀이 compute cap < 7.0 이면 중단 |
| Kaggle 오토세이브 초과 | repo·데이터셋까지 working 에 있으면 20GB 초과 | 중간물은 `/kaggle/temp`, working 에는 산출물만 |

`torchao` 는 QLoRA 에 필요 없습니다. 4bit 양자화는 `bitsandbytes` 가 담당하고
설치하면 torch/transformers 버전 해석 충돌만 증가시킵니다.

---

## 데이터 만들기 — 두 가지 방법

### 1) 템플릿 방식 (기본, 무료)

```bash
python training/auto_build_datasets.py 5000
```

LLM 호출 없이 12개 언어 × 6개 주제군 × 모디파이어 조합으로 5000개를 만듭니다.
비용 0, 결정적(시드 고정), 즉시 실행.

### 2) CLOVA Studio 방식 (고품질, 유료)

```bash
export CLOVA_STUDIO_API_KEY=...
python training/clova_build_datasets.py --target 5000 --model HCX-007
```

- 엔드포인트: `https://clovastudio.stream.ntruss.com/v1/openai`
- 모델: **HCX-007** (최상위 추론), `HCX-DASH-002` 는 저가/고속
- 호출 1회당 `--k` 개 생성 (기본 10) → 5000개면 약 500회 호출
- **응답은 `datasets/.clova_cache.jsonl` 에 캐시** — 재실행 시 비용 0
- `--merge datasets/wx_coder.jsonl` 로 템플릿 데이터와 병합

파이프라인 검증 (API 키 없이):

```bash
python training/clova_build_datasets.py --target 50 --mock
```

> ⚠️ `HCX-003` / `HCX-DASH-001` 은 구세대 모델입니다. 문서상 최신은
> HCX-007 / HCX-005 / HCX-DASH-002 입니다.

---

## 런타임 재시작이 필요 없는 이유

학습/병합/변환이 전부 `!python ...` **서브프로세스**로 실행됩니다.
새로 설치된 `numpy`/`transformers`/`trl` 을 깨끗한 프로세스에서 로드하므로
커널 재시작이 필요 없습니다.

직접 셀에서 `import transformers` 하려면 재시작하세요.

---

## 모델 크기 선택

| 베이스 | 학습 | 병합 필요 RAM | 완주 가능한 곳 |
|--------|------|--------------|---------------|
| 1.5B-Instruct | 1.5~2.5h | 약 4 GB | Colab 무료, Kaggle |
| 7B-Instruct | 5~8h | 약 15.2 GB | Kaggle (29GB), A100 런타임 |

---

## 트러블슈팅

| 증상 | 원인 | 해결 |
|------|------|------|
| `KeyError: 'messages'` | 템플릿 데이터가 옛 스키마 | `auto_build_datasets.py` 를 다시 실행 |
| `TypeError: unexpected keyword 'tokenizer'` | 패치 안 됨 | 패치 셀 실행 확인 (`[SKIP]` 이면 이미 적용됨) |
| CUDA bf16 오류 | T4 에서 bf16 | 자동 판정됨. 구버전 파일이면 패치 셀 재실행 |
| `Killed` / OOM | 7B 를 Colab 에서 | Colab 노트북은 1.5B. 7B 는 Kaggle 노트북 사용 |
| 병합 시 RAM 초과 | 7B fp16 | Kaggle(29GB) 또는 A100 |
| `[FAIL] compute cap` | P100 으로 떨어짐 | Settings → GPU T4 x2, 또는 `machine_shape` 추가 |
| `[STOP] 디스크 부족` | GGUF 변환 여유 부족 | LoRA 는 이미 백업됨. 1.5B 로 낮추거나 디스크 런타임 상향 |
| `git clone` 실패 | Internet 꺼짐 | Kaggle Settings → Internet On |
| `merge_adapter.py` 어댑터 없음 | 학습 실패 / 경로 오류 | 학습 셀 로그 확인 |
| YAML 스캐너 에러 | config 들여쓰기 파괴 | config 조정 셀을 수정된 그대로 실행 |
| 5000개 미만 | 고유 instruction 부족 | `--k` 또는 `--target` 조정 |
| cmake / g++ 없음 | Kaggle 이미지 | 빌드 도구 셀이 `apt-get install` 로 설치 |

---

## 파일 구조

```
wuxicode/
├── training/
│   ├── train_qlora.py            # 학습 (SFTConfig 로 현대화됨)
│   ├── fix_train_qlora.py        # 구버전 업그레이드 스크립트 (idempotent)
│   ├── merge_adapter.py          # LoRA 병합 (dtype=, transformers 4.56+)
│   ├── auto_build_datasets.py    # 템플릿 데이터 생성 (무료)
│   ├── clova_build_datasets.py   # CLOVA Studio 데이터 생성 (유료, 캐시)
│   ├── config/wx-1-coder.yaml
│   ├── WX1_Colab_OneClick.ipynb  # Colab (1.5B)
│   ├── WX1_Kaggle_OneClick.ipynb # Kaggle (7B)
│   └── kernel-metadata.json      # Kaggle CLI 푸시 설정 (T4 지정)
├── datasets/
│   ├── wx_coder.jsonl            # 템플릿 데이터 (생성물)
│   └── wx_coder_clova.jsonl      # Clova 데이터 (생성물)
└── output/
    ├── wx-1-coder-lora/          # LoRA 어댑터
    ├── wx-1-coder-merged/        # 병합 모델
    └── wx-1-coder-q4_k_m.gguf    # 최종 배포물
```

`datasets/` 와 `output/` 은 생성물이므로 커밋하지 않아도 됩니다
(노트북이 새로 만듭니다).

---

## 로컬에서 학습만 돌리기

```bash
python training/auto_build_datasets.py 5000
python training/fix_train_qlora.py
python training/train_qlora.py --config training/config/wx-1-coder.yaml
```

CPU 로 1.5B QLoRA 는 실용적이 아닙니다 (수 시간 → 수 주).
로컬 GPU 를 쓸 때만.