import json
import random
from pathlib import Path

SEED_SAMPLES = [
    {
        "instruction": "Python으로 파일 읽기/쓰기 함수를 작성하세요.",
        "input": "",
        "output": "```python\ndef read_file(path):\n    with open(path, 'r', encoding='utf-8') as f:\n        return f.read()\n\ndef write_file(path, content):\n    with open(path, 'w', encoding='utf-8') as f:\n        f.write(content)\n```"
    },
    {
        "instruction": "리스트에서 중복을 제거하면서 순서를 유지하는 코드를 작성하세요.",
        "input": "",
        "output": "```python\ndef dedupe_preserve_order(seq):\n    seen = set()\n    return [x for x in seq if not (x in seen or seen.add(x))]\n```"
    },
    {
        "instruction": "딕셔너리 리스트를 특정 키로 정렬하는 함수를 만드세요.",
        "input": "",
        "output": "```python\ndef sort_dicts_by_key(dicts, key, reverse=False):\n    return sorted(dicts, key=lambda d: d.get(key, ''), reverse=reverse)\n```"
    },
    {
        "instruction": "JSON 파일을 읽어서 파싱하고 예외 처리하는 코드를 작성하세요.",
        "input": "",
        "output": "```python\nimport json\n\ndef load_json_safe(path):\n    try:\n        with open(path, 'r', encoding='utf-8') as f:\n            return json.load(f)\n    except (FileNotFoundError, json.JSONDecodeError) as e:\n        print(f'Error loading {path}: {e}')\n        return None\n```"
    },
    {
        "instruction": "문자열에서 이메일 주소를 추출하는 정규식 패턴을 작성하세요.",
        "input": "",
        "output": "```python\nimport re\n\ndef extract_emails(text):\n    pattern = r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\\.[a-zA-Z]{2,}'\n    return re.findall(pattern, text)\n```"
    },
]

TEMPLATES = [
    {
        "instruction": "{lang}으로 {task} 함수를 작성하세요.",
        "input": "",
        "output": "```{lang}\n{code}\n```"
    },
    {
        "instruction": "{lang}에서 {concept}을 설명하고 예제 코드를 보여주세요.",
        "input": "",
        "output": "{explanation}\n\n```{lang}\n{code}\n```"
    },
    {
        "instruction": "다음 {lang} 코드의 버그를 찾고 수정하세요:\n{buggy_code}",
        "input": "",
        "output": "버그: {bug_desc}\n\n수정된 코드:\n```{lang}\n{fixed_code}\n```"
    },
    {
        "instruction": "{lang}으로 {algorithm} 알고리즘을 구현하세요.",
        "input": "",
        "output": "```{lang}\n{code}\n```"
    },
    {
        "instruction": "{lang}에서 {pattern} 디자인 패턴을 구현하는 예제를 보여주세요.",
        "input": "",
        "output": "```{lang}\n{code}\n```"
    },
]

LANGS = ["Python", "JavaScript", "TypeScript", "Go", "Rust", "C++", "Java", "C#"]
TASKS = [
    "파일 읽기/쓰기", "HTTP 요청 보내기", "JSON 파싱", "정규식 매칭",
    "날짜/시간 포맷팅", "문자열 조작", "리스트/배열 정렬", "딕셔너리/맵 병합",
    "예외 처리", "로깅 설정", "환경 변수 읽기", "명령행 인자 파싱",
    "디렉터리 순회", "압축/해제", "Base64 인코딩", "해시 계산",
]
CONCEPTS = [
    "클로저", "데코레이터", "제너레이터", "비동기/대기", "프로미스",
    "이터레이터", "컨텍스트 매니저", "메타클래스", "타입 힌트",
    "인터페이스", "트레이트", "모나드", "커링", "메모이제이션",
]
ALGORITHMS = [
    "퀵 정렬", "병합 정렬", "이진 탐색", "다익스트라", "깊이 우선 탐색",
    "너비 우선 탐색", "다이나믹 프로그래밍 - 피보나치", "KMP 문자열 매칭",
    "최소 힙", "트라이", "유니온 파인드", "토폴로지 정렬",
]
PATTERNS = [
    "싱글톤", "팩토리", "빌더", "옵저버", "전략", "데코레이터",
    "어댑터", "퍼사드", "프록시", "커맨드", "상태", "템플릿 메서드",
]


def render_template(template, **kwargs):
    return {
        "instruction": template["instruction"].format(**kwargs),
        "input": template["input"].format(**kwargs) if template["input"] else "",
        "output": template["output"].format(**kwargs),
    }


def generate_code_snippet(lang, task):
    snippets = {
        ("Python", "파일 읽기/쓰기"): "def read_file(p):\n    with open(p, 'r', encoding='utf-8') as f:\n        return f.read()\n\ndef write_file(p, c):\n    with open(p, 'w', encoding='utf-8') as f:\n        f.write(c)",
        ("Python", "HTTP 요청 보내기"): "import requests\n\nresp = requests.get('https://api.example.com/data', timeout=10)\nresp.raise_for_status()\ndata = resp.json()",
        ("Python", "JSON 파싱"): "import json\n\ndata = json.loads(json_string)\njson_string = json.dumps(data, ensure_ascii=False, indent=2)",
        ("Python", "정규식 매칭"): "import re\n\nmatches = re.findall(r'pattern', text)\nreplaced = re.sub(r'pattern', 'replacement', text)",
        ("JavaScript", "파일 읽기/쓰기"): "const fs = require('fs');\n\nconst data = fs.readFileSync('file.txt', 'utf-8');\nfs.writeFileSync('file.txt', data);",
        ("JavaScript", "HTTP 요청 보내기"): "const res = await fetch('https://api.example.com/data');\nconst data = await res.json();",
        ("TypeScript", "타입 힌트"): "interface User {\n    id: number;\n    name: string;\n    email: string;\n}\n\nfunction getUser(id: number): Promise<User> {\n    return fetch(`/api/users/${id}`).then(r => r.json());\n}",
        ("Go", "HTTP 요청 보내기"): "resp, err := http.Get(\"https://api.example.com/data\")\nif err != nil { log.Fatal(err) }\ndefer resp.Body.Close()\nbody, _ := io.ReadAll(resp.Body)",
        ("Rust", "파일 읽기/쓰기"): "use std::fs;\n\nlet content = fs::read_to_string(\"file.txt\")?;\nfs::write(\"file.txt\", content)?;",
        ("C++", "파일 읽기/쓰기"): "#include <fstream>\n#include <string>\n\nstd::string read(const std::string& path) {\n    std::ifstream f(path);\n    return std::string(std::istreambuf_iterator<char>(f), {});\n}",
        ("Java", "파일 읽기/쓰기"): "import java.nio.file.*;\nimport java.io.IOException;\n\nString content = Files.readString(Path.of(\"file.txt\"));\nFiles.writeString(Path.of(\"file.txt\"), content);",
        ("C#", "파일 읽기/쓰기"): "using System.IO;\n\nstring content = File.ReadAllText(\"file.txt\");\nFile.WriteAllText(\"file.txt\", content);",
    }
    return snippets.get((lang, task), f"// {lang} {task} 구현")


def generate_buggy_code(lang):
    bugs = {
        "Python": ("for i in range(10):\n    print(i)\nprint('done')", "들여쓰기 오류: print('done')이 루프 밖에 있어야 함"),
        "JavaScript": ("for (let i = 0; i < 10; i++) {\n    setTimeout(() => console.log(i), 100);\n}", "var 대신 let 사용 필요 (클로저 문제)"),
        "TypeScript": ("function add(a: number, b: number) {\n    return a + b;\n}\nadd('1', '2');", "타입 불일치: string을 number로 전달"),
        "Go": ("func main() {\n    m := map[string]int{}\n    m[\"key\"] = 1\n    delete(m, \"key\")\n    fmt.Println(m[\"key\"])", "삭제된 키 접근 시 zero value 반환"),
        "Rust": ("fn main() {\n    let v = vec![1, 2, 3];\n    println!(\"{}\", v[3]);", "인덱스 범위 초과 (0-2만 유효)"),
    }
    return bugs.get(lang, ("// 버그 코드", "// 설명"))


def generate_fixed_code(lang):
    fixes = {
        "Python": "for i in range(10):\n    print(i)\nprint('done')  # 올바른 들여쓰기",
        "JavaScript": "for (let i = 0; i < 10; i++) {\n    setTimeout(() => console.log(i), 100);\n}",
        "TypeScript": "function add(a: number, b: number): number {\n    return a + b;\n}\nadd(1, 2);",
        "Go": "func main() {\n    m := map[string]int{}\n    m[\"key\"] = 1\n    delete(m, \"key\")\n    if v, ok := m[\"key\"]; ok {\n        fmt.Println(v)\n    }\n}",
        "Rust": "fn main() {\n    let v = vec![1, 2, 3];\n    if let Some(val) = v.get(3) {\n        println!(\"{}\", val);\n    }\n}",
    }
    return fixes.get(lang, "// 수정된 코드")


def main():
    out_path = Path("datasets/wx_coder.jsonl")
    out_path.parent.mkdir(parents=True, exist_ok=True)

    all_samples = []

    # 시드 샘플 추가
    for s in SEED_SAMPLES:
        all_samples.append({
            "instruction": s["instruction"],
            "input": s["input"],
            "output": s["output"],
        })

    # 템플릿 기반 생성
    random.seed(42)
    while len(all_samples) < 300:
        template = random.choice(TEMPLATES)
        lang = random.choice(LANGS)

        if "{task}" in template["instruction"]:
            task = random.choice(TASKS)
            code = generate_code_snippet(lang, task)
            sample = render_template(template, lang=lang, task=task, code=code)

        elif "{concept}" in template["instruction"]:
            concept = random.choice(CONCEPTS)
            code = f"// {lang}에서 {concept} 예제"
            explanation = f"{lang}에서 {concept}은(는) ..."
            sample = render_template(template, lang=lang, concept=concept, code=code, explanation=explanation)

        elif "{buggy_code}" in template["instruction"]:
            buggy, bug_desc = generate_buggy_code(lang)
            fixed = generate_fixed_code(lang)
            sample = render_template(template, lang=lang, buggy_code=buggy, bug_desc=bug_desc, fixed_code=fixed)

        elif "{algorithm}" in template["instruction"]:
            algorithm = random.choice(ALGORITHMS)
            code = f"// {algorithm} 구현"
            sample = render_template(template, lang=lang, algorithm=algorithm, code=code)

        elif "{pattern}" in template["instruction"]:
            pattern = random.choice(PATTERNS)
            code = f"// {pattern} 패턴 구현"
            sample = render_template(template, lang=lang, pattern=pattern, code=code)

        else:
            continue

        all_samples.append(sample)

    # 정확히 300개로 자르기
    all_samples = all_samples[:300]

    # 저장
    with out_path.open("w", encoding="utf-8") as f:
        for s in all_samples:
            f.write(json.dumps(s, ensure_ascii=False) + "\n")

    print(f"✓ 데이터셋 생성 완료: {len(all_samples)}개 샘플 -> {out_path}")


if __name__ == "__main__":
    main()