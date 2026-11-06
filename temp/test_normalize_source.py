# -*- coding: utf-8 -*-
"""evaluation/Dataset/normalize_source.py + 합친 문장 게이트 회귀.

각 무중괄호 형태를 정규화해 엔진에 넣고, 손으로 쓴 중괄호 버전과 EXIT 값이 같은지 본다.
사용: PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe temp/test_normalize_source.py
"""
import contextlib, io, os, re, sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "evaluation", "Dataset")]
import engine_api
from Dependencies.main import slice_solidity
from Utils.Errors import SourceError
from normalize_source import blank_comments, find_braceless, normalize

fails = 0
def check(name, ok, extra=""):
    global fails
    print(("  ok  " if ok else "  FAIL ") + name + ("" if ok else "  " + str(extra)))
    fails += 0 if ok else 1

def wrap(b):
    return ("contract C {\n    uint256 public total;\n    function f(uint256 n) public {\n"
            + "\n".join("        " + x for x in b.split("\n")) + "\n    }\n}\n")

def run(src):
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            s = engine_api.EngineSession()
            st = s.analyze_source(src, contract="C", func="f")
            if st.diagnostics:
                return "진단: " + str(getattr(st.diagnostics[0], "message", st.diagnostics[0]))
            ev = s.evaluate({"n": {"t": "interval", "lo": "0", "hi": "10"},
                             "total": {"t": "interval", "lo": "0", "hi": "0"}}, contract="C", func="f")
        return next(n for n in ev.cfg.nodes if n.id == ev.cfg.exit).state.get("total")
    except Exception as e:
        return "예외 %s: %s" % (type(e).__name__, str(e)[:90])

print("[1] 무중괄호 → 중괄호 버전과 동치")
PAIRS = [
    ("같은 줄 if", "if (n > 5) total = 1;", "if (n > 5) {\ntotal = 1;\n}"),
    ("다음 줄 if", "if (n > 5)\n    total = 1;\ntotal = total + 2;", "if (n > 5) {\ntotal = 1;\n}\ntotal = total + 2;"),
    ("다음 줄 if/else", "if (n > 5)\n    total = 1;\nelse\n    total = 2;", "if (n > 5) {\ntotal = 1;\n} else {\ntotal = 2;\n}"),
    ("한 줄 if/else", "if (n > 5) total = 1; else total = 2;", "if (n > 5) {\ntotal = 1;\n} else {\ntotal = 2;\n}"),
    ("if 같은 줄 + else 다음 줄", "if (n > 5) total = 1;\nelse total = 2;", "if (n > 5) {\ntotal = 1;\n} else {\ntotal = 2;\n}"),
    ("다음 줄 for", "for (uint256 i = 0; i < 3; i++)\n    total = total + 1;\ntotal = total + 5;", "for (uint256 i = 0; i < 3; i++) {\ntotal = total + 1;\n}\ntotal = total + 5;"),
    ("다음 줄 while", "uint256 i = 0;\nwhile (i < 3)\n    i = i + 1;\ntotal = i;", "uint256 i = 0;\nwhile (i < 3) {\ni = i + 1;\n}\ntotal = i;"),
    ("중첩", "if (n > 5)\n    if (n > 7)\n        total = 1;\ntotal = total + 2;", "if (n > 5) {\nif (n > 7) {\ntotal = 1;\n}\n}\ntotal = total + 2;"),
    ("dangling else", "if (n > 5)\n    if (n > 7)\n        total = 1;\n    else\n        total = 2;", "if (n > 5) {\nif (n > 7) {\ntotal = 1;\n} else {\ntotal = 2;\n}\n}"),
    ("for 안 if", "for (uint256 i = 0; i < 3; i++)\n    if (n > 5)\n        total = total + 1;", "for (uint256 i = 0; i < 3; i++) {\nif (n > 5) {\ntotal = total + 1;\n}\n}"),
    ("else 줄바꿈 if", "if (n > 7)\n    total = 1;\nelse\n    if (n > 5)\n        total = 2;", "if (n > 7) {\ntotal = 1;\n} else if (n > 5) {\ntotal = 2;\n}"),
    ("여러 줄 본문", "if (n > 5)\n    total = n +\n        1;\ntotal = total + 2;", "if (n > 5) {\ntotal = n + 1;\n}\ntotal = total + 2;"),
    ("헤더-본문 사이 주석", "if (n > 5)\n    // comment\n    total = 1;\ntotal = total + 2;", "if (n > 5) {\ntotal = 1;\n}\ntotal = total + 2;"),
    ("본문 return", "if (n > 5)\n    return;\ntotal = 1;", "if (n > 5) {\nreturn;\n}\ntotal = 1;"),
    ("중괄호 then, 무중괄호 else", "if (n > 5) {\n    total = 1;\n} else\n    total = 2;", "if (n > 5) {\ntotal = 1;\n} else {\ntotal = 2;\n}"),
    ("else if 무중괄호", "if (n > 7) total = 1;\nelse if (n > 5) total = 2;\nelse total = 3;", "if (n > 7) {\ntotal = 1;\n} else if (n > 5) {\ntotal = 2;\n} else {\ntotal = 3;\n}"),
]
for name, a, b in PAIRS:
    got, want = run(normalize(wrap(a)).text), run(wrap(b))
    check(name, got == want and not str(got).startswith(("진단", "예외")), (got, want))

print("[2] 주석·문자열·체인")
def body(s):
    return normalize(wrap(s)).text
check("문자열 안 //", '"http://x"' in body('string memory u = "http://x"; // tail'))
check("꼬리 주석 제거", "tail" not in body('total = 1; // tail'))
check("블록 주석 줄 보존", normalize("a\n/* x\ny */\nb\n").text == "a\n\n\nb\n", repr(normalize("a\n/* x\ny */\nb\n").text))
check("문자열 안 키워드 무시", normalize(wrap('string memory s = "if (x) y;";')).braces_inserted == 0)
check("주석 안 키워드 무시", normalize(wrap('// if (x) y;\ntotal = 1;')).braces_inserted == 0)
check("do-while 꼬리 제외", normalize(wrap("do {\n    total = 1;\n} while (n > 5);")).braces_inserted == 0)
check("else if 의 else 는 감싸지 않음", normalize(wrap("if (n > 1) {\n    total = 1;\n} else if (n > 5) {\n    total = 2;\n}")).braces_inserted == 0)
yul = "assembly {\n    if lt(n, 1) { revert(0, 0) }\n    for { } lt(n, 1) { } { }\n}"
check("assembly 제외", normalize(wrap(yul)).braces_inserted == 0)
check("멤버 이름 .if 제외", not find_braceless(blank_comments("x.if (a) b;")[1])[0])

print("[3] 줄 대응·멱등")
r = normalize("if (a)\n    b();\nc();\n")
check("줄 대응", [r.line_map[k] for k in sorted(r.line_map)][:4] == [1, 2, 2, 3], r.line_map)
for name, a, _ in PAIRS:
    once = normalize(wrap(a)).text
    if normalize(once).text != once:
        check("멱등 " + name, False)
check("멱등 전체", True)

print("[4] 게이트: 합친 문장")
for name, src in (("다음 줄 if", "if (n > 5)\n    total = 1;"), ("다음 줄 for", "for (uint256 i = 0; i < 3; i++)\n    total = 1;"),
                  ("다음 줄 else", "if (n > 5) {\n    total = 1;\n} else\n    total = 2;"), ("중첩 헤더", "if (n > 5)\n    if (n > 7) {\n        total = 1;\n    }")):
    try:
        slice_solidity(wrap(src)); check(name, False, "통과함")
    except SourceError as e:
        check(name, "입력 전제 위반" in str(e), e)
for name, src in (("여러 줄 호출", "total = uint256(\n    n\n);"), ("여러 줄 조건", "if (\n    n > 5\n) {\n    total = 1;\n}"),
                  ("여러 줄 for", "for (\n    uint256 i = 0;\n    i < 3;\n    i++\n) {\n    total = 1;\n}"), ("여러 줄 require", 'require(\n    n > 5,\n    "x;y"\n);')):
    try:
        slice_solidity(wrap(src)); check(name + " 허용", True)
    except SourceError as e:
        check(name + " 허용", False, e)

print("실패 %d" % fails)
sys.exit(1 if fails else 0)
