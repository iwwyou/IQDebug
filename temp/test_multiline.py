# -*- coding: utf-8 -*-
"""여러 줄 헤더(`_insert_lines` 시작 줄 배치)·조건 수정(`modify`)·import 레코드 회귀 (2026-11-06).
사용: PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe temp/test_multiline.py
"""
import contextlib, io, os, sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import engine_api
from Dependencies.main import slice_solidity
from Utils.Errors import SourceError

fails = 0
def check(name, ok, extra=""):
    global fails
    print(("  ok  " if ok else "  FAIL ") + name + ("" if ok else "  " + str(extra)))
    fails += 0 if ok else 1

HEAD = ("contract C {\n    uint256 public total;\n"
        "    function g(uint256 a, uint256 b, uint256 c) public pure returns (uint256) {\n        return a + b + c;\n    }\n"
        "    function f(uint256 n) public {\n")
def wrap(b):
    return HEAD + "\n".join("        " + x for x in b.split("\n")) + "\n    }\n}"
V = {"n": {"t": "interval", "lo": "0", "hi": "10"}, "total": {"t": "interval", "lo": "0", "hi": "0"}}

def session(src):
    s = engine_api.EngineSession()
    s.analyze_source(src, contract="C", func="f")
    return s

def total(s):
    ev = s.evaluate(V, contract="C", func="f")
    return next(n for n in ev.cfg.nodes if n.id == ev.cfg.exit).state.get("total"), ev

def quiet(fn):
    b = io.StringIO()
    with contextlib.redirect_stdout(b), contextlib.redirect_stderr(b):
        return fn()

print("[1] 여러 줄 문장 = 한 줄 문장, 조건 노드 줄 = 원본 줄")
CASES = [
    ("if 3줄", "if (\n    n > 5\n) {\n    total = 1;\n}\ntotal = total + 2;", "if (n > 5) {\ntotal = 1;\n}\ntotal = total + 2;"),
    ("if 2줄", "if (n > 5 &&\n    n < 9) {\n    total = 1;\n}", "if (n > 5 && n < 9) {\ntotal = 1;\n}"),
    ("else if 3줄", "if (n > 8) {\n    total = 1;\n} else if (\n    n > 5\n) {\n    total = 2;\n} else {\n    total = 3;\n}", "if (n > 8) {\ntotal = 1;\n} else if (n > 5) {\ntotal = 2;\n} else {\ntotal = 3;\n}"),
    ("연속 else if", "if (n > 8) {\n    total = 1;\n} else if (\n    n > 5\n) {\n    total = 2;\n} else if (\n    n > 3\n) {\n    total = 3;\n}", "if (n > 8) {\ntotal = 1;\n} else if (n > 5) {\ntotal = 2;\n} else if (n > 3) {\ntotal = 3;\n}"),
    ("while 3줄", "uint256 i = 0;\nwhile (\n    i < 3\n) {\n    i = i + 1;\n}\ntotal = i;", "uint256 i = 0;\nwhile (i < 3) {\ni = i + 1;\n}\ntotal = i;"),
    ("for 5줄", "for (\n    uint256 i = 0;\n    i < 3;\n    i++\n) {\n    total = total + 1;\n}", "for (uint256 i = 0; i < 3; i++) {\ntotal = total + 1;\n}"),
    ("호출 여러 줄", "total = g(\n    n,\n    1,\n    2\n);\ntotal = total + 1;", "total = g(n, 1, 2);\ntotal = total + 1;"),
]
for name, a, b in CASES:
    src = wrap(a)
    got, ev = quiet(lambda: total(session(src)))
    want, _ = quiet(lambda: total(session(wrap(b))))
    check(name + " 값", got == want, (got, want))
    lines = src.split("\n")
    bad = [(n.name, lines[n.src_line - 1].strip()) for n in ev.cfg.nodes
           if "cond" in n.name and n.src_line > 0
           and not any(k in lines[n.src_line - 1] for k in ("if", "while", "for"))]
    check(name + " 조건 노드 줄", not bad, bad)

print("[2] 증분 편집 = 새 분석 (한 줄·여러 줄)")
EDITS = [
    ("본문 추가", "if (n > 5) {\n    total = 1;\n}\ntotal = total + 2;", "if (n > 5) {\n    total = 1;\n    total = total + 1;\n}\ntotal = total + 2;"),
    ("조건 수정", "if (n > 5) {\n    total = 1;\n}\ntotal = total + 2;", "if (n > 7) {\n    total = 1;\n}\ntotal = total + 2;"),
    ("else if 조건 수정", "if (n > 8) {\n    total = 1;\n} else if (n > 5) {\n    total = 2;\n}\ntotal = total + 1;", "if (n > 8) {\n    total = 1;\n} else if (n > 6) {\n    total = 2;\n}\ntotal = total + 1;"),
    ("앞에 추가", "if (n > 5) {\n    total = 1;\n}\ntotal = total + 2;", "total = 9;\nif (n > 5) {\n    total = 1;\n}\ntotal = total + 2;"),
]
def multi(s):
    for c in ("5", "6", "7"):
        s = s.replace("if (n > %s) {" % c, "if (\n    n > %s\n) {" % c)
    return s
for mode, f in (("한 줄", lambda s: s), ("여러 줄", multi)):
    for name, a, b in EDITS:
        def go():
            s = session(wrap(f(a)))
            s.update(wrap(f(b)))
            n = session(wrap(f(b)))
            return total(s)[0], total(n)[0], s._sa.full_code_lines == n._sa.full_code_lines
        got, want, same = quiet(go)
        check("%s %s 값" % (mode, name), got == want, (got, want))
        check("%s %s 저장소" % (mode, name), same)

print("[3] import 레코드")
def recs(body):
    return [r for r in slice_solidity(body) if r["code"].startswith("import")]
R = recs('import { A, B } from "./A.sol";\ncontract C {\n}')
check("named 한 줄", len(R) == 1 and (R[0]["startLine"], R[0]["endLine"]) == (1, 1), R)
R = recs('import {\n    A,\n    B\n} from "./A.sol";\ncontract C {\n}')
check("named 여러 줄", len(R) == 1 and (R[0]["startLine"], R[0]["endLine"]) == (1, 4)
      and R[0]["code"] == 'import { A, B } from "./A.sol";', R)
R = recs('import{A} from "./A.sol";\ncontract C {\n}')
check("공백 없는 import", R and R[0]["code"].startswith("import {"), R)
for name, src in (("{A as Z}", 'import { A as Z } from "./A.sol";'), ("* as X", 'import * as X from "./A.sol";'),
                  ('"x" as X', 'import "./A.sol" as X;')):
    try:
        slice_solidity(src)
        check("별칭 거부 " + name, False)
    except SourceError as e:
        check("별칭 거부 " + name, "별칭" in str(e), e)
try:
    slice_solidity('import "./as.sol";')
    check("경로 안 as 허용", True)
except SourceError as e:
    check("경로 안 as 허용", False, e)
try:
    slice_solidity('import "a.sol"; import "b.sol";')
    check("두 문장 거부", False)
except SourceError:
    check("두 문장 거부", True)
src = 'import {\n    A\n} from "./A.sol";\n' + wrap("total = n;")
got, _ = quiet(lambda: total(session(src)))
check("여러 줄 import 뒤 분석", got == "[0,10]", got)

print("실패 %d" % fails)
sys.exit(1 if fails else 0)
