"""WX-1 데이터셋 자동 생성기.

train_qlora.py 의 format_chat() 이 요구하는 스키마:
    {"messages": [{"role": "user", ...}, {"role": "assistant", ...}]}

사용법:
    python training/auto_build_datasets.py            # 5000개 (기본)
    python training/auto_build_datasets.py 8000       # 8000개
"""
import argparse
import hashlib
import json
import random
from pathlib import Path

# --------------------------------------------------------------------------
# 언어 / 주제 목록
# --------------------------------------------------------------------------
LANGS = [
    "Python", "JavaScript", "TypeScript", "Go", "Rust",
    "C++", "Java", "C#", "Swift", "Kotlin", "Ruby", "PHP",
]

TASKS = [
    "파일 읽기/쓰기", "HTTP 요청 보내기", "JSON 파싱", "정규식 매칭",
    "날짜/시간 포맷팅", "문자열 조작", "리스트 정렬", "딕셔너리 병합",
    "예외 처리", "로깅 설정", "환경 변수 읽기", "명령행 인자 파싱",
    "디렉터리 순회", "압축/해제", "Base64 인코딩", "해시 계산",
    "CSV 파싱", "XML 파싱", "YAML 파싱", "TOML 파싱",
    "소켓 통신", "웹소켓 클라이언트", "gRPC 클라이언트", "GraphQL 쿼리",
    "데이터베이스 연결", "마이그레이션 실행", "트랜잭션 처리", "커넥션 풀",
    "캐시 데코레이터", "레이트 리미터", "재시도 로직", "서킷 브레이커",
    "파일 감시", "프로세스 실행", "시그널 처리", "데몬화",
    "메트릭 수집", "분산 트레이싱", "헬스 체크", "그레이스풀 셧다운",
]

CONCEPTS = [
    "클로저", "데코레이터", "제너레이터", "비동기/대기", "프로미스",
    "이터레이터", "컨텍스트 매니저", "메타클래스", "타입 힌트",
    "인터페이스", "트레이트", "모나드", "커링", "메모이제이션",
    "의존성 주입", "팩토리 패턴", "빌더 패턴", "옵저버 패턴",
    "전략 패턴", "상태 패턴", "템플릿 메서드", "책임 연쇄",
    "프록시 패턴", "어댑터 패턴", "퍼사드 패턴", "데코레이터 패턴",
    "싱글톤 패턴", "플라이웨이트", "컴포지트", "브리지",
    "타입 가드", "프로토콜", "데이터클래스", "패턴 매칭",
]

ALGORITHMS = [
    "퀵 정렬", "병합 정렬", "이진 탐색", "다익스트라", "깊이 우선 탐색",
    "너비 우선 탐색", "피보나치 DP", "KMP 문자열 매칭",
    "최소 힙", "트라이", "유니온 파인드", "위상 정렬",
    "A* 탐색", "벨만-포드", "플로이드-워셜", "크루스칼",
    "최대 유량", "이분 매칭", "최장 공통 부분수열", "편집 거리",
    "배낭 문제", "허프만 코딩", "라빈-카프", "보이어-무어",
    "블룸 필터", "하이퍼로그로그", "스킵 리스트", "트리 맵",
]

PATTERNS = [
    "싱글톤", "팩토리", "빌더", "옵저버", "전략", "데코레이터",
    "어댑터", "퍼사드", "프록시", "커맨드", "상태", "템플릿 메서드",
    "책임 연쇄", "메디에이터", "메멘토",
    "비지터", "인터프리터", "플라이웨이트", "컴포지트", "브리지",
    "프로토타입", "추상 팩토리", "객체 풀", "서비스 로케이터",
]

DATA_STRUCTURES = [
    "연결 리스트", "이중 연결 리스트", "원형 버퍼", "스택", "큐",
    "우선순위 큐", "덱", "해시 테이블", "트리 맵", "레드-블랙 트리",
    "AVL 트리", "B-트리", "트라이", "압축 트라이",
    "세그먼트 트리", "펜윅 트리", "스파스 테이블", "디스조인트 셋",
    "블룸 필터", "쿠쿠 필터", "LSM 트리", "스킵 리스트",
    "카르테시안 트리", "링 버퍼", "우선순위 큐(힙)",
]

PROTOCOLS = [
    "HTTP/1.1", "HTTP/2", "HTTP/3", "WebSocket", "gRPC",
    "GraphQL", "MQTT", "AMQP", "Redis 프로토콜", "Memcached 프로토콜",
    "Kafka 프로토콜", "NATS 프로토콜",
]

CATEGORIES = [
    ("task", TASKS),
    ("concept", CONCEPTS),
    ("algorithm", ALGORITHMS),
    ("pattern", PATTERNS),
    ("data_structure", DATA_STRUCTURES),
    ("protocol", PROTOCOLS),
]

MODIFIERS = ["", " — 타입 힌트 포함", " — 에러 처리 포함", " — 테스트 코드 포함"]

# --------------------------------------------------------------------------
# 실제 코드 스니펫: (lang, item) -> [variant, ...]
# --------------------------------------------------------------------------
CORE_SNIPPETS = {
    ("Python", "파일 읽기/쓰기"): [
        "def read_file(path):\n    with open(path, 'r', encoding='utf-8') as f:\n        return f.read()\n\n\ndef write_file(path, content):\n    with open(path, 'w', encoding='utf-8') as f:\n        f.write(content)",
        "from pathlib import Path\n\n\ndef read_text(p):\n    return Path(p).read_text(encoding='utf-8')\n\n\ndef write_text(p, text):\n    Path(p).write_text(text, encoding='utf-8')",
    ],
    ("Python", "HTTP 요청 보내기"): [
        "import requests\n\n\ndef fetch_json(url, timeout=10):\n    resp = requests.get(url, timeout=timeout)\n    resp.raise_for_status()\n    return resp.json()",
        "from urllib.request import urlopen\nimport json\n\n\ndef fetch_json(url):\n    with urlopen(url, timeout=10) as resp:\n        return json.loads(resp.read().decode('utf-8'))",
    ],
    ("Python", "JSON 파싱"): [
        "import json\n\n\ndef loads_safe(raw, default=None):\n    try:\n        return json.loads(raw)\n    except json.JSONDecodeError:\n        return default",
        "import json\nfrom pathlib import Path\n\n\ndef load_config(path):\n    return json.loads(Path(path).read_text(encoding='utf-8'))\n\n\ndef dump_config(obj, path):\n    Path(path).write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding='utf-8')",
    ],
    ("Python", "정규식 매칭"): [
        "import re\n\n\ndef find_all(pattern, text):\n    return re.findall(pattern, text)\n\n\ndef substitute(pattern, repl, text):\n    return re.sub(pattern, repl, text)",
        "import re\n\nEMAIL = re.compile(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\\.[a-zA-Z]{2,}')\n\n\ndef extract_emails(text):\n    return EMAIL.findall(text)",
    ],
    ("Python", "CSV 파싱"): [
        "import csv\n\n\ndef load_csv(path):\n    with open(path, newline='', encoding='utf-8') as f:\n        return list(csv.DictReader(f))",
        "import csv\n\n\ndef write_csv(path, rows, fieldnames):\n    with open(path, 'w', newline='', encoding='utf-8') as f:\n        writer = csv.DictWriter(f, fieldnames=fieldnames)\n        writer.writeheader()\n        writer.writerows(rows)",
    ],
    ("Python", "디렉터리 순회"): [
        "from pathlib import Path\n\n\ndef walk(root, suffix='.py'):\n    for p in Path(root).rglob(f'*{suffix}'):\n        yield p",
        "import os\n\n\ndef walk_all(root):\n    for dirpath, dirnames, filenames in os.walk(root):\n        for name in filenames:\n            yield os.path.join(dirpath, name)",
    ],
    ("Python", "재시도 로직"): [
        "import time\n\n\ndef retry(fn, attempts=3, delay=1.0):\n    last = None\n    for i in range(attempts):\n        try:\n            return fn()\n        except Exception as e:\n            last = e\n            time.sleep(delay * (2 ** i))\n    raise last",
        "import functools\nimport time\n\n\ndef with_retry(attempts=3, delay=1.0):\n    def deco(fn):\n        @functools.wraps(fn)\n        def wrapper(*a, **kw):\n            for i in range(attempts):\n                try:\n                    return fn(*a, **kw)\n                except Exception:\n                    if i == attempts - 1:\n                        raise\n                    time.sleep(delay * (2 ** i))\n        return wrapper\n    return deco",
    ],
    ("Python", "캐시 데코레이터"): [
        "import functools\n\n\ndef memoize(fn):\n    cache = {}\n\n    @functools.wraps(fn)\n    def wrapper(*args):\n        if args not in cache:\n            cache[args] = fn(*args)\n        return cache[args]\n\n    return wrapper",
    ],
    ("Python", "Base64 인코딩"): [
        "import base64\n\n\ndef b64encode(text):\n    return base64.b64encode(text.encode('utf-8')).decode('ascii')\n\n\ndef b64decode(token):\n    return base64.b64decode(token.encode('ascii')).decode('utf-8')",
    ],
    ("Python", "해시 계산"): [
        "import hashlib\n\n\ndef sha256_file(path, chunk=65536):\n    h = hashlib.sha256()\n    with open(path, 'rb') as f:\n        for block in iter(lambda: f.read(chunk), b''):\n            h.update(block)\n    return h.hexdigest()",
    ],
    ("JavaScript", "HTTP 요청 보내기"): [
        "async function fetchJson(url, options = {}) {\n  const res = await fetch(url, options);\n  if (!res.ok) throw new Error(`HTTP ${res.status}`);\n  return res.json();\n}",
        "const fetchJson = async (url) => {\n  const res = await fetch(url, { headers: { Accept: 'application/json' } });\n  return res.json();\n};",
    ],
    ("JavaScript", "파일 읽기/쓰기"): [
        "const fs = require('fs/promises');\n\nasync function readFile(path) {\n  return fs.readFile(path, 'utf-8');\n}\n\nasync function writeFile(path, data) {\n  await fs.writeFile(path, data, 'utf-8');\n}",
    ],
    ("JavaScript", "정규식 매칭"): [
        "function extractEmails(text) {\n  const re = /[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\\.[a-zA-Z]{2,}/g;\n  return text.match(re) ?? [];\n}",
    ],
    ("TypeScript", "타입 힌트"): [
        "interface User {\n  id: number;\n  name: string;\n  email: string;\n}\n\nasync function getUser(id: number): Promise<User> {\n  const res = await fetch(`/api/users/${id}`);\n  return res.json() as Promise<User>;",
    ],
    ("Go", "HTTP 요청 보내기"): [
        "package main\n\nimport (\n\t\"io\"\n\t\"net/http\"\n)\n\nfunc fetch(url string) ([]byte, error) {\n\tresp, err := http.Get(url)\n\tif err != nil {\n\t\treturn nil, err\n\t}\n\tdefer resp.Body.Close()\n\treturn io.ReadAll(resp.Body)\n}",
    ],
    ("Go", "고루틴 동시 처리"): [
        "package main\n\nfunc worker(jobs <-chan int, results chan<- int) {\n\tfor j := range jobs {\n\t\tresults <- j * 2\n\t}\n}\n\nfunc run(n int) []int {\n\tjobs := make(chan int, n)\n\tresults := make(chan int, n)\n\tfor w := 0; w < 4; w++ {\n\t\tgo worker(jobs, results)\n\t}\n\tout := make([]int, 0, n)\n\tfor i := 0; i < n; i++ {\n\t\tjobs <- i\n\t}\n\tclose(jobs)\n\tfor i := 0; i < n; i++ {\n\t\tout = append(out, <-results)\n\t}\n\treturn out\n}",
    ],
    ("Rust", "에러 처리"): [
        "use std::fs;\nuse std::io;\n\npub fn read_to_string(path: &str) -> Result<String, io::Error> {\n    fs::read_to_string(path)\n}",
        "use std::io::{self, Read};\nuse std::fs::File;\n\npub fn read_all(path: &str) -> Result<String, io::Error> {\n    let mut file = File::open(path)?;\n    let mut buf = String::new();\n    file.read_to_string(&mut buf)?;\n    Ok(buf)\n}",
    ],
    ("Rust", "반복자 이터레이터"): [
        "pub fn evens(limit: u32) -> impl Iterator<Item = u32> {\n    (0..limit).filter(|n| n % 2 == 0)\n}",
    ],
    ("C++", "템플릿 유틸리티"): [
        "#include <algorithm>\n#include <vector>\n\ntemplate <typename T>\nT max_of(const std::vector<T>& v) {\n    return *std::max_element(v.begin(), v.end());\n}",
    ],
    ("Java", "스트림 API"): [
        "import java.util.List;\nimport java.util.stream.Collectors;\n\nList<String> upper = names.stream()\n    .filter(n -> n.length() > 3)\n    .map(String::toUpperCase)\n    .collect(Collectors.toList());",
    ],
    ("C#", "LINQ 질의"): [
        "var result = numbers\n    .Where(n => n % 2 == 0)\n    .Select(n => n * n)\n    .OrderByDescending(n => n)\n    .ToList();",
    ],
    ("Swift", "옵셔널 처리"): [
        "func greet(name: String?) -> String {\n    guard let name else { return \"Hello, stranger\" }\n    return \"Hello, \\(name)\"\n}",
    ],
    ("Kotlin", "널 안전성"): [
        "fun lengthOf(text: String?): Int = text?.length ?: 0",
        "fun requireName(name: String?): String = requireNotNull(name) { \"name is required\" }",
    ],
    ("Ruby", "블록 단위 실행"): [
        "def with_lock(lock)\n  lock.lock\n  begin\n    yield\n  ensure\n    lock.unlock\n  end\nend",
    ],
    ("PHP", "배열 변환"): [
        "<?php\n$users = [\n  ['id' => 1, 'name' => 'John'],\n  ['id' => 2, 'name' => 'Jane'],\n];\n$names = array_column($users, 'name');",
    ],
}

# 버그 수정 쌍
BUG_PAIRS = {
    "Python": (
        "for i in range(10):\n    print(i)\nprint('done')",
        "들여쓰기 오류: print('done')이 for 루프 밖이라 루프마다 실행되지 않습니다.",
        "for i in range(10):\n    print(i)\nprint('done')",
    ),
    "JavaScript": (
        "for (var i = 0; i < 10; i++) {\n  setTimeout(() => console.log(i), 100);\n}",
        "var는 함수 스코프라 모든 콜백이 마지막 i(=10)를 출력합니다. let이 필요합니다.",
        "for (let i = 0; i < 10; i++) {\n  setTimeout(() => console.log(i), 100);\n}",
    ),
    "TypeScript": (
        "function add(a: number, b: number) {\n  return a + b;\n}\nadd('1', '2');",
        "컴파일 타임 타입 에러: 문자열을 number 파라미터로 전달했습니다.",
        "function add(a: number, b: number): number {\n  return a + b;\n}\nadd(1, 2);",
    ),
    "Go": (
        "package main\n\nfunc main() {\n\tm := map[string]int{}\n\tm[\"k\"] = 1\n\tdelete(m, \"k\")\n\tprintln(m[\"k\"])\n}",
        "삭제된 키를 읽으면 zero value(0)가 나오며 존재 여부는 ok로 확인해야 합니다.",
        "package main\n\nfunc main() {\n\tm := map[string]int{}\n\tm[\"k\"] = 1\n\tdelete(m, \"k\")\n\tif v, ok := m[\"k\"]; ok {\n\t\tprintln(v)\n\t}\n}",
    ),
    "Rust": (
        "fn main() {\n    let v = vec![1, 2, 3];\n    println!(\"{}\", v[3]);\n}",
        "인덱스가 범위를 벗어났습니다. 0..=2만 유효하며 get()을 쓰면 안전합니다.",
        "fn main() {\n    let v = vec![1, 2, 3];\n    match v.get(3) {\n        Some(val) => println!(\"{}\", val),\n        None => println!(\"out of range\"),\n    }\n}",
    ),
    "C++": (
        "#include <vector>\n#include <iostream>\n\nint main() {\n    std::vector<int> v = {1, 2, 3};\n    std::cout << v[3];\n}",
        "operator[]는 범위 검사를 하지 않아 정의되지 않은 동작입니다. at()을 쓰세요.",
        "#include <vector>\n#include <iostream>\n#include <stdexcept>\n\nint main() {\n    std::vector<int> v = {1, 2, 3};\n    try {\n        std::cout << v.at(3);\n    } catch (const std::out_of_range& e) {\n        std::cerr << e.what();\n    }\n}",
    ),
    "Java": (
        "import java.util.*;\n\nList<String> list = new ArrayList<>();\nlist.add(\"a\");\nSystem.out.println(list.get(1));",
        "IndexOutOfBoundsException: 크기가 1인데 인덱스 1에 접근했습니다.",
        "import java.util.*;\n\nList<String> list = new ArrayList<>();\nlist.add(\"a\");\nif (!list.isEmpty()) {\n    System.out.println(list.get(0));\n}",
    ),
    "C#": (
        "using System.Collections.Generic;\n\nvar list = new List<int> { 1, 2, 3 };\nConsole.WriteLine(list[3]);",
        "ArgumentOutOfRangeException: Count는 3인데 인덱스 3은 유효하지 않습니다.",
        "using System.Collections.Generic;\n\nvar list = new List<int> { 1, 2, 3 };\nif (list.Count > 3) Console.WriteLine(list[3]);\nelse Console.WriteLine(\"out of range\");",
    ),
    "Swift": (
        "var name: String? = \"John\"\nprint(name.count)",
        "Optional에 count를 직접 접근할 수 없습니다. unwrap이 필요합니다.",
        "var name: String? = \"John\"\nprint(name?.count ?? 0)",
    ),
    "Kotlin": (
        "fun greet(name: String?): Int {\n    return name.length\n}",
        "name이 nullable이므로 .length 접근이 컴파일되지 않습니다.",
        "fun greet(name: String?): Int {\n    return name?.length ?: 0\n}",
    ),
    "Ruby": (
        "def sum(numbers)\n  numbers.each { |n| total += n }\n  total\nend",
        "total이 초기화되지 않아 NameError가 발생합니다.",
        "def sum(numbers)\n  total = 0\n  numbers.each { |n| total += n }\n  total\nend",
    ),
    "PHP": (
        "<?php\nfunction getName($user) {\n  return $user['name'];\n}",
        "존재하지 않는 키 접근 시 Warning이 발생하고 null을 반환합니다.",
        "<?php\nfunction getName(array $user): string {\n  return $user['name'] ?? 'unknown';\n}",
    ),
}


# --------------------------------------------------------------------------
# 언어별 일반 코드 빌더 (스니펫에 없는 조합용)
# --------------------------------------------------------------------------
def _slug(topic):
    ascii_only = "".join(ch if ch.isascii() and ch.isalnum() else " " for ch in topic)
    parts = ascii_only.split()
    if parts:
        return "".join(w.capitalize() for w in parts) or "Topic"
    return "".join(chr(ord("a") + (ord(c) % 26)) for c in topic)


def _topic_comment(topic):
    return topic


def build_generic(lang, topic, variant):
    name = _slug(topic)
    note = _topic_comment(topic)
    if lang == "Python":
        return (
            f"def {name.lower()}(value):\n"
            f'    """{note} 처리 함수."""\n'
            f"    if value is None:\n"
            f"        raise ValueError('value must not be None')\n"
            f"    return value\n"
        )
    if lang == "JavaScript":
        return (
            f"function {name[0].lower() + name[1:]}(value) {{\n"
            f"  // {note}\n"
            f"  if (value === undefined || value === null) {{\n"
            f"    throw new TypeError('value is required');\n"
            f"  }}\n"
            f"  return value;\n"
            f"}}\n"
        )
    if lang == "TypeScript":
        return (
            f"export function {name[0].lower() + name[1:]}(value: string): string {{\n"
            f"  // {note}\n"
            f"  if (!value) throw new Error('value is required');\n"
            f"  return value;\n"
            f"}}\n"
        )
    if lang == "Go":
        return (
            "package main\n\n"
            f"// {note}\n"
            f"func {name}(value string) (string, error) {{\n"
            f"\tif value == \"\" {{\n"
            f"\t\treturn \"\", ErrEmptyValue\n"
            f"\t}}\n"
            f"\treturn value, nil\n"
            f"}}\n"
        )
    if lang == "Rust":
        return (
            f"/// {note}\n"
            f"pub fn {name[0].lower() + name[1:]}(value: &str) -> Result<&str, String> {{\n"
            f"    if value.is_empty() {{\n"
            f"        return Err(\"empty value\".to_string());\n"
            f"    }}\n"
            f"    Ok(value)\n"
            f"}}\n"
        )
    if lang == "C++":
        return (
            "#include <stdexcept>\n#include <string>\n\n"
            f"// {note}\n"
            f"std::string {name}(const std::string& value) {{\n"
            f"    if (value.empty()) {{\n"
            f"        throw std::invalid_argument(\"empty value\");\n"
            f"    }}\n"
            f"    return value;\n"
            f"}}\n"
        )
    if lang == "Java":
        return (
            "import java.util.Objects;\n\n"
            f"/** {note} */\n"
            f"public final class {name} {{\n"
            f"    public static String apply(String value) {{\n"
            f"        Objects.requireNonNull(value, \"value\");\n"
            f"        return value;\n"
            f"    }}\n"
            f"}}\n"
        )
    if lang == "C#":
        return (
            "using System;\n\n"
            f"// {note}\n"
            f"public static class {name} {{\n"
            f"    public static string Apply(string value) {{\n"
            f"        return value ?? throw new ArgumentNullException(nameof(value));\n"
            f"    }}\n"
            f"}}\n"
        )
    if lang == "Swift":
        return (
            f"/// {note}\n"
            f"func {name[0].lower() + name[1:]}(_ value: String) throws -> String {{\n"
            f"    guard !value.isEmpty else {{\n"
            f"        throw NSError(domain: \"invalid\", code: 1)\n"
            f"    }}\n"
            f"    return value\n"
            f"}}\n"
        )
    if lang == "Kotlin":
        return (
            f"/** {note} */\n"
            f"fun {name[0].lower() + name[1:]}(value: String?): String {{\n"
            f"    return requireNotNull(value) {{ \"value is required\" }}\n"
            f"}}\n"
        )
    if lang == "Ruby":
        return (
            f"# {note}\n"
            f"def {name[0].lower() + name[1:]}(value)\n"
            f"  raise ArgumentError, 'value is required' if value.nil?\n"
            f"  value\n"
            f"end\n"
        )
    return (
        f"<?php\n/** {note} */\n"
        f"function {name[0].lower() + name[1:]}($value): string {{\n"
        f"    if ($value === null) {{\n"
        f"        throw new InvalidArgumentException('value is required');\n"
        f"    }}\n"
        f"    return $value;\n"
        f"}}\n"
    )


# --------------------------------------------------------------------------
# 모디파이어 (실제로 코드에 반영)
# --------------------------------------------------------------------------
def apply_modifier(lang, code, modifier):
    if modifier == "":
        return code
    if "테스트 코드" in modifier:
        if lang == "Python":
            return code + "\n\ndef test_smoke():\n    assert isinstance(Process(), object)\n".replace("Process()", _slug_for_test(lang))
        if lang == "JavaScript":
            return code + "\n\nconsole.assert(typeof run === 'function');\n".replace("run", _js_fn_name(code))
        if lang == "Go":
            return code + "\n// go test ./...\n"
        return code + f"\n// 검증: {modifier.strip(' —')}\n"
    if "에러 처리" in modifier:
        if lang == "Python":
            return "try:\n" + _indent(code, "    ") + "\nexcept Exception as e:\n    raise RuntimeError(f'failed: {e}') from e\n"
        if lang == "JavaScript":
            return "try {\n" + _indent(code, "  ") + "\n} catch (err) {\n  console.error(err);\n  throw err;\n}\n"
        return code + f"\n// 에러 처리: {modifier.strip(' —')}\n"
    if "타입 힌트" in modifier:
        if lang == "Python":
            return code + "\n# 타입 힌트: def func(value: str) -> str\n"
        if lang == "TypeScript":
            return code + "\n// 타입 힌트가 이미 포함되어 있습니다.\n"
        return code + f"\n// 타입 힌트 참고: {modifier.strip(' —')}\n"
    return code


def _indent(text, pad):
    return "\n".join(pad + line if line.strip() else line for line in text.split("\n"))


def _slug_for_test(lang):
    return "process_value"


def _js_fn_name(code):
    for line in code.split("\n"):
        if line.startswith("function "):
            return line.split("function ", 1)[1].split("(")[0]
    return "run"


# --------------------------------------------------------------------------
# 샘플 조립
# --------------------------------------------------------------------------
def pick_code(lang, topic, variant):
    snippets = CORE_SNIPPETS.get((lang, topic))
    if snippets:
        return snippets[variant % len(snippets)]
    return build_generic(lang, topic, variant)


def make_task_sample(rng, lang, topic, modifier):
    variant = rng.randrange(6)
    code = pick_code(lang, topic, variant)
    code = apply_modifier(lang, code, modifier)
    user = f"{lang}으로 {topic} 구현을 작성해 주세요{modifier}."
    assistant = f"아래는 {lang}로 작성한 {topic} 구현입니다{modifier}.\n\n```{lang}\n{code.rstrip()}\n```"
    return user, assistant


def make_concept_sample(rng, lang, topic, modifier):
    variant = rng.randrange(6)
    code = pick_code(lang, topic, variant)
    code = apply_modifier(lang, code, modifier)
    user = f"{lang}에서 {topic}이 무엇인지 설명하고 예제 코드를 보여 주세요{modifier}."
    assistant = (
        f"{topic}은(는) 코드의 재사용성과 구조를 개선하기 위한 개념입니다.\n\n"
        f"핵심 포인트:\n"
        f"- responsibility 분리: 동작을 하나의 단위로 묶습니다.\n"
        f"- 느슨한 결합: 호출자는 내부 구현을 몰라도 됩니다.\n"
        f"- 테스트 용이성: 각 단위를 독립적으로 검증할 수 있습니다.\n\n"
        f"예제 ({lang}){modifier}:\n\n```{lang}\n{code.rstrip()}\n```"
    )
    return user, assistant


def make_bug_sample(rng, lang, topic, modifier):
    pair = BUG_PAIRS.get(lang)
    if not pair:
        return None
    buggy, desc, fixed = pair
    user = f"다음 {lang} 코드의 버그를 찾고 수정해 주세요.\n\n```{lang}\n{buggy}\n```"
    assistant = f"버그: {desc}\n\n수정 코드:\n\n```{lang}\n{fixed.rstrip()}\n```"
    return user, assistant


def make_sample(rng, lang, topic, modifier):
    kind = rng.random()
    if kind < 0.45:
        return make_task_sample(rng, lang, topic, modifier)
    if kind < 0.70:
        return make_concept_sample(rng, lang, topic, modifier)
    return make_bug_sample(rng, lang, topic, modifier)


def key_of(user):
    return hashlib.sha1(user.encode("utf-8")).hexdigest()


def build(target, seed=42):
    rng = random.Random(seed)
    seen = set()
    samples = []

    seed_rows = [
        ("Python으로 JSON 파일을 읽고 예외 처리하는 함수를 작성하세요.",
         "```python\nimport json\n\n\ndef load_json_safe(path):\n    try:\n        with open(path, 'r', encoding='utf-8') as f:\n            return json.load(f)\n    except (FileNotFoundError, json.JSONDecodeError) as e:\n        print(f'Error loading {path}: {e}')\n        return None\n```"),
        ("Python으로 리스트에서 중복을 제거하면서 순서를 유지하는 코드를 작성하세요.",
         "```python\ndef dedupe_preserve_order(seq):\n    seen = set()\n    return [x for x in seq if not (x in seen or seen.add(x))]\n```"),
        ("Python으로 문자열에서 이메일 주소를 추출하는 정규식을 작성하세요.",
         "```python\nimport re\n\nEMAIL = re.compile(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\\.[a-zA-Z]{2,}')\n\n\ndef extract_emails(text):\n    return EMAIL.findall(text)\n```"),
    ]

    for user, assistant in seed_rows:
        row = {"messages": [
            {"role": "system", "content": "당신은 {lang} 코딩 어시스턴트입니다.".replace("{lang}", "경험 많은")},
            {"role": "user", "content": user},
            {"role": "assistant", "content": assistant},
        ]}
        samples.append(row)
        seen.add(key_of(user))

    attempts = 0
    max_attempts = target * 60
    while len(samples) < target and attempts < max_attempts:
        attempts += 1
        lang = rng.choice(LANGS)
        cat_name, cat_list = rng.choice(CATEGORIES)
        topic = rng.choice(cat_list)
        modifier = rng.choice(MODIFIERS)

        made = make_sample(rng, lang, topic, modifier)
        if not made:
            continue
        user, assistant = made
        k = key_of(user)
        if k in seen:
            continue
        seen.add(k)

        if rng.random() < 0.25:
            system = "당신은 뛰어난 소프트웨어 엔지니어이며, 간결하고 정확한 코드를 작성합니다."
        else:
            system = "당신은 경력 많은 코딩 어시스턴트입니다."

        samples.append({"messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
            {"role": "assistant", "content": assistant},
        ]})

    return samples, attempts


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("target", nargs="?", type=int, default=5000)
    ap.add_argument("--out", default="datasets/wx_coder.jsonl")
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    samples, attempts = build(args.target, args.seed)

    if len(samples) < args.target:
        raise SystemExit(
            f"[FAIL] 고유 샘플 {len(samples)}개를 만들었지만 목표는 {args.target}개입니다. "
            f"CATEGORIES/MODIFIERS 를 늘리세요. (시도 {attempts}회)"
        )

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as f:
        for s in samples:
            f.write(json.dumps(s, ensure_ascii=False) + "\n")

    langs = {}
    for s in samples:
        for m in s["messages"]:
            if m["role"] == "assistant":
                break
    print(f"[OK] {len(samples)} samples -> {out}")
    print(f"[OK] 고유 instruction {len(seen_keys(samples))}개, 시도 {attempts}회")


def seen_keys(samples):
    keys = set()
    for s in samples:
        for m in s["messages"]:
            if m["role"] == "user":
                keys.add(key_of(m["content"]))
                break
    return keys


if __name__ == "__main__":
    main()