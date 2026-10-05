"""CLOVA Studio (HyperCLOVA X) 기반 WX-1 데이터셋 생성기.

auto_build_datasets.py(템플릿 방식)와 별개로, LLM이 실제로 코드를 작성해서
高质量 샘플을 만든다. 출력 스키마는 동일하게 messages 포맷.

사용법:
    # 실제 호출
    python training/clova_build_datasets.py --target 5000 --model HCX-007

    # 파이프라인 검증 (API 키 불필요, 목 응답 사용)
    python training/clova_build_datasets.py --target 50 --mock

    # 템플릿 데이터와 합치기
    python training/clova_build_datasets.py --target 5000 --merge datasets/wx_coder.jsonl

환경변수:
    CLOVA_STUDIO_API_KEY  (또는 --api-key)
"""
import argparse
import hashlib
import json
import os
import random
import re
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

API_URL = "https://clovastudio.stream.ntruss.com/v1/openai/chat/completions"

DEFAULT_MODEL = "HCX-007"
VALID_MODELS = ["HCX-007", "HCX-005", "HCX-DASH-002", "HCX-003", "HCX-DASH-001"]

SYSTEM_PROMPT = (
    "당신은 시니어 소프트웨어 엔지니어이자 기술 문서 작성자입니다. "
    "코드 학습 데이터 생성을 위해 정확하고 실행 가능한 코드를 작성합니다."
)


# --------------------------------------------------------------------------
# 프롬프트 빌더
# --------------------------------------------------------------------------
def build_user_prompt(lang, topic, k):
    return f"""다음 조건에 맞는 {k}개의 고유한 코드 학습 예제를 생성하세요.

언어: {lang}
주제: {topic}

JSON 배열로만 응답하세요. 마크다운 코드펜스(```json)는 절대 사용하지 마세요.

스키마:
[{{"instruction": "개발 질문", "response": "설정 + 코드"}}]

요구사항:
1. instruction은 자연스러운 한국어 개발 질문 1문장 (구체적으로).
2. response는 설명 1~2줄 뒤에 해당 언어로 작성된 코드 블록을 포함.
3. {k}개는 모두 서로 달라야 함: 함수명, 변수명, 접근 방식, 파라미터 구조를 다르게.
4. 코드는 실제로 실행 가능하고 문법 오류가 없어야 함.
5. 주제("{topic}")와 언어("{lang}")에 맞는 내용만.
6. 과도한 반복(같은 코드 여러 번) 금지.

즉시 JSON 배열로 응답하세요."""


def prompt_hash(model, lang, topic, k):
    raw = f"{model}|{lang}|{topic}|{k}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------
# API 호출 (표준 라이브러리만 사용 — 의존성 없음)
# --------------------------------------------------------------------------
class Stats:
    def __init__(self):
        self.lock = threading.Lock()
        self.calls = 0
        self.ok = 0
        self.failed = 0
        self.samples = 0

    def bump(self, field, n=1):
        with self.lock:
            setattr(self, field, getattr(self, field) + n)

    def snapshot(self):
        with self.lock:
            return dict(calls=self.calls, ok=self.ok, failed=self.failed, samples=self.samples)


def post_json(url, payload, api_key, timeout=180):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", f"Bearer {api_key}")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def call_clova(api_key, model, lang, topic, k, stats, max_retries=5, max_tokens=3000):
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": build_user_prompt(lang, topic, k)},
        ],
        "temperature": 0.9,
        "top_p": 0.95,
        "max_tokens": max_tokens,
        "response_format": {"type": "json_schema"},
    }

    last_err = None
    for attempt in range(max_retries):
        try:
            stats.bump("calls")
            raw = post_json(API_URL, payload, api_key)
            content = raw["choices"][0]["message"]["content"]
            stats.bump("ok")
            return parse_samples(content)
        except urllib.error.HTTPError as e:
            last_err = f"HTTP {e.code}: {e.read()[:300].decode('utf-8', 'replace')}"
            if e.code in (400, 401, 403, 404):
                break
            sleep_s = min(60, 2 ** attempt * 3)
        except Exception as e:  # noqa: BLE001
            last_err = f"{type(e).__name__}: {e}"
            sleep_s = min(60, 2 ** attempt * 3)
        time.sleep(sleep_s)

    stats.bump("failed")
    print(f"[WARN] {lang}/{topic} 실패: {last_err}", file=sys.stderr)
    return []


# --------------------------------------------------------------------------
# 응답 파싱 (방어적)
# --------------------------------------------------------------------------
def strip_fences(text):
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*", "", t)
    t = re.sub(r"\s*```$", "", t)
    return t.strip()


def _decode_at(text, idx):
    """idx 에서 JSON 값 하나를 파싱. 성공하면 (obj, end), 실패하면 None."""
    try:
        obj, end = json.JSONDecoder().raw_decode(text, idx)
        return obj, end
    except ValueError:
        return None


def parse_samples(content):
    """LLM 응답에서 {instruction, response} 리스트 추출.

    응답 안에 코드 블록이 들어가면 중괄호/대괄호가 중첩되므로
    정규식이 아니라 JSONDecoder.raw_decode 로 스캔한다.
    """
    if not content:
        return []

    text = strip_fences(content)

    # 1) ```json 펜스 내부 (중첩 펜스로 잘릴 수 있으므로 실패해도 계속)
    for m in re.finditer(r"```json\s*(.+?)\s*```", text, re.DOTALL):
        rows = normalize(try_json(m.group(1)))
        if rows:
            return rows

    # 2) 통째로
    rows = normalize(try_json(text))
    if rows:
        return rows

    # 3) 모든 [ / { 위치에서 raw_decode 시도
    for idx, ch in enumerate(text):
        if ch not in "[{":
            continue
        got = _decode_at(text, idx)
        if not got:
            continue
        rows = normalize(got[0])
        if rows:
            return rows

    # 4) 절단된 JSON 복구용: 키/값 쌍 직접 추출
    return salvage_pairs(text)


def try_json(text):
    try:
        return json.loads(text)
    except Exception:  # noqa: BLE001
        return None


STR_RE = r'((?:[^"\\]|\\.)*)'


def _unescape(text):
    """문자열 복구용 느슨한 이스케이프 해제."""
    try:
        return json.loads(f'"{text}"')
    except Exception:  # noqa: BLE001
        out = text
        out = out.replace("\\n", "\n").replace("\\t", "\t").replace("\\r", "\r")
        out = out.replace('\\"', '"').replace("\\\\", "\\")
        return out


def salvage_pairs(text):
    """JSON 이 절단됐거나 깨졌어도 instruction/response 쌍은 건져낸다."""
    inst = re.findall(r'"instruction"\s*:\s*"' + STR_RE + '"', text)
    resp = re.findall(r'"response"\s*:\s*"' + STR_RE + '"', text)
    if not resp:
        resp = re.findall(r'"response"\s*:\s*"' + STR_RE + r'$', text, re.DOTALL)

    rows = []
    for i, iv in enumerate(inst):
        if i >= len(resp):
            break
        instruction = _unescape(iv).strip()
        response = _unescape(resp[i]).strip()
        if instruction and response:
            rows.append({"instruction": instruction, "response": response})
    return rows


def normalize(obj):
    if isinstance(obj, dict):
        for key in ("samples", "data", "result", "examples", "items"):
            if key in obj and isinstance(obj[key], list):
                return [x for x in obj[key] if isinstance(x, dict)]
        return [obj] if ("instruction" in obj or "response" in obj) else []

    if isinstance(obj, list):
        return [x for x in obj if isinstance(x, dict)]

    return []


def validate(row, lang):
    instruction = str(row.get("instruction", "") or row.get("prompt", "") or "").strip()
    response = str(row.get("response", "") or row.get("output", "") or row.get("completion", "") or "").strip()

    if len(instruction) < 8 or len(response) < 40:
        return None
    if lang.lower() not in response.lower():
        return None

    return {"instruction": instruction, "response": response}


# --------------------------------------------------------------------------
# 캐시
# --------------------------------------------------------------------------
def load_cache(path):
    cache = {}
    if not path.exists():
        return cache
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            key = rec.get("key")
            if key:
                cache[key] = rec.get("rows", [])
    return cache


def append_cache(path, key, rows):
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"key": key, "rows": rows}, ensure_ascii=False) + "\n")


# --------------------------------------------------------------------------
# 시드 조합 생성
# --------------------------------------------------------------------------
def load_taxonomy(path):
    """auto_build_datasets.py 에서 주제 목록을 재사용."""
    sys.path.insert(0, str(Path(path).parent))
    import auto_build_datasets as ab  # noqa: E402

    return ab.LANGS, ab.CATEGORIES


def build_jobs(langs, categories, target, k, model):
    jobs = []
    combos = []
    for lang in langs:
        for _, topics in categories:
            for topic in topics:
                combos.append((lang, topic))

    # 셔플하되 재현 가능하게
    random.Random(7).shuffle(combos)

    per_combo = max(1, (target + k - 1) // k)
    seen = set()
    while len(combos) < per_combo:
        combos.append(combos[len(combos) % max(1, len(combos))])

    for lang, topic in combos[:per_combo]:
        key = prompt_hash(model, lang, topic, k)
        if key in seen:
            continue
        seen.add(key)
        jobs.append((key, lang, topic))
    return jobs


# --------------------------------------------------------------------------
# 목 응답 (검증용)
# --------------------------------------------------------------------------
def mock_call(lang, topic, k):
    rows = []
    for i in range(k):
        rows.append({
            "instruction": f"{lang}로 {topic}를 구현하는 방법이 궁금합니다. {i + 1}번 질문입니다.",
            "response": (
                f"{topic}는 다음과 같이 구현할 수 있습니다. {lang} 코드입니다.\n\n"
                f"```{lang}\nfunction {topic.split()[0]}_{i}(value) {{\n  return value;\n}}\n```"
            ),
        })
    return rows


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------
def read_jsonl(path):
    rows = []
    p = Path(path)
    if not p.exists():
        return rows
    with p.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def dedup(existing, incoming):
    seen = set()
    for row in existing:
        for m in row.get("messages", []):
            if m.get("role") == "user":
                seen.add(hashlib.sha1(m["content"].encode("utf-8")).hexdigest())
                break

    added = 0
    for row in incoming:
        msg = row["messages"][1]
        h = hashlib.sha1(msg["content"].encode("utf-8")).hexdigest()
        if h in seen:
            continue
        seen.add(h)
        existing.append(row)
        added += 1
    return existing


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", type=int, default=5000)
    ap.add_argument("--model", default=DEFAULT_MODEL, choices=VALID_MODELS)
    ap.add_argument("--k", type=int, default=10, help="호출 1회당 생성 샘플 수")
    ap.add_argument("--concurrency", type=int, default=5)
    ap.add_argument("--max-tokens", type=int, default=3000)
    ap.add_argument("--api-key", default=None)
    ap.add_argument("--out", default="datasets/wx_coder_clova.jsonl")
    ap.add_argument("--cache", default="datasets/.clova_cache.jsonl")
    ap.add_argument("--merge", default=None, help="기존 jsonl과 합칠 대상")
    ap.add_argument("--mock", action="store_true", help="API 호출 없이 파이프라인만 검증")
    ap.add_argument("--taxonomy", default=str(Path(__file__).with_name("auto_build_datasets.py")))
    args = ap.parse_args()

    api_key = args.api_key or os.environ.get("CLOVA_STUDIO_API_KEY")
    if not args.mock and not api_key:
        raise SystemExit("[FAIL] CLOVA_STUDIO_API_KEY 환경변수 또는 --api-key 가 필요합니다.")

    langs, categories = load_taxonomy(args.taxonomy)
    jobs = build_jobs(langs, categories, args.target, args.k, args.model)
    print(f"[INFO] model={args.model} jobs={len(jobs)} k={args.k} target={args.target}")

    cache_path = Path(args.cache)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache = load_cache(cache_path)

    stats = Stats()
    collected = []
    done = 0
    total = len(jobs)

    def handle(key, lang, topic):
        if key in cache:
            return cache[key]
        if args.mock:
            rows = mock_call(lang, topic, args.k)
        else:
            rows = call_clova(api_key, args.model, lang, topic, args.k, stats, max_tokens=args.max_tokens)
        append_cache(cache_path, key, rows)
        return rows

    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        futures = {pool.submit(handle, k, l, t): (k, l, t) for k, l, t in jobs}
        for fut in as_completed(futures):
            key, lang, topic = futures[fut]
            try:
                rows = fut.result()
            except Exception as e:  # noqa: BLE001
                print(f"[WARN] 예외 {lang}/{topic}: {e}", file=sys.stderr)
                rows = []
            for row in rows:
                clean = validate(row, lang)
                if clean:
                    collected.append(clean)
            done += 1
            if done % 10 == 0 or done == total:
                snap = stats.snapshot()
                print(
                    f"[{done}/{total}] 수집={len(collected)} "
                    f"호출={snap['calls']} 성공={snap['ok']} 실패={snap['failed']}"
                )

    print(f"[INFO] 검증 통과 샘플: {len(collected)}")

    out_rows = []
    seen = set()
    for clean in collected:
        h = hashlib.sha1(clean["instruction"].encode("utf-8")).hexdigest()
        if h in seen:
            continue
        seen.add(h)
        system = "당신은 경력 많은 코딩 어시스턴트입니다."
        out_rows.append({"messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": clean["instruction"]},
            {"role": "assistant", "content": clean["response"]},
        ]})

    if args.merge:
        base_rows = read_jsonl(args.merge)
        print(f"[INFO] 병합 대상 {len(base_rows)}개 로드")
        out_rows = dedup(base_rows, out_rows)
    else:
        out_rows = dedup([], out_rows)

    if not out_rows:
        raise SystemExit("[FAIL] 유효한 샘플 0개. API 응답/모델을 확인하세요.")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as f:
        for row in out_rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    print(f"[OK] {len(out_rows)} samples -> {out}")
    if len(out_rows) < args.target:
        print(f"[WARN] 목표 {args.target}개에 미달. --k 또는 --target 조정 필요.")


if __name__ == "__main__":
    main()