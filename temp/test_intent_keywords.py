# -*- coding: utf-8 -*-
"""intent 전용 키워드와 Solidity 식별자의 충돌 회귀 (2026-11-13, B' 렉서 action + intentIdentifier).

`array`·`not`·`Entry`·`floor` 같은 intent 단어는 **코드에서는 일반 식별자**, **intent 줄에서만 키워드**다.
intent 의 이름 자리(`@Param <이름>`, varRef 의 첫 이름·멤버)는 키워드와 같은 이름도 받는다.
사용: PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe temp/test_intent_keywords.py
"""
import contextlib, io, os, sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from antlr4 import CommonTokenStream
from antlr4.tree.Tree import TerminalNode
import engine_api
import incremental
from Dependencies.main import slice_solidity
from Parser.SolidityLexer import SolidityLexer
from Parser.SolidityParser import SolidityParser
from Utils.Helper import ParserHelpers

fails = 0
def check(name, ok, extra=""):
    global fails
    print(("  ok  " if ok else "  FAIL ") + name + ("" if ok else "  " + str(extra)))
    fails += 0 if ok else 1

def types(src, intent):
    lx = ParserHelpers.new_lexer(src, intent=intent)
    lx.removeErrorListeners()
    return [t.type for t in lx.getAllTokens()]

def alts(src):
    """intentUnit 파스 트리의 Context 이름들."""
    lx = ParserHelpers.new_lexer(src, intent=True); lx.removeErrorListeners()
    p = SolidityParser(CommonTokenStream(lx)); p.removeErrorListeners()
    out = []
    def walk(t):
        if not isinstance(t, TerminalNode):
            out.append(type(t).__name__)
            for c in (t.children or []):
                walk(c)
    walk(p.intentUnit())
    return out

print("[1] 렉싱: 코드에서는 Identifier, intent 에서는 키워드")
I = SolidityLexer.Identifier
for word, tok in (("array", SolidityLexer.ARRAY_KW), ("not", SolidityLexer.NOT_KW), ("Entry", SolidityLexer.ENTRY),
                  ("floor", SolidityLexer.FLOOR_KW), ("any", SolidityLexer.ANY_KW), ("Before", SolidityLexer.BEFORE)):
    check("코드 %s" % word, types(word, False) == [I], types(word, False))
    check("intent %s" % word, types(word, True) == [tok], types(word, True))
check("긴 이름 arrayAddress·EntryCount 는 코드에서 Identifier", types("arrayAddress EntryCount", False) == [I, I])
check("기본값은 코드 모드", SolidityLexer.intentMode is False)

print("[2] 전체 소스 문법 검사(incremental.syntax_valid)")
for name, src in (("array 변수", "contract C { uint256 array; function f() public { array = 1; } }"),
                  ("not 함수", "contract C { function not(bool b) public pure returns (bool) { return !b; } "
                               "function g() public pure returns (bool) { return not(true); } }"),
                  ("struct Entry", "contract C { struct Entry { uint256 a; } mapping(uint256 => Entry) es; "
                                   "function f() public { es[0] = Entry({a: 1}); } }"),
                  ("floor·any·changed 이름", "contract C { uint256 floor; bool any; uint256 changed; }")):
    ok, diags = incremental.syntax_valid(src)
    check(name, ok, diags[:1])

print("[3] 어노테이션: 기존 대안 보존 + 같은 이름 허용")
MATRIX = [
    ("@Param array = array [1, 2]", "DebugIntArrayContext"),
    ("@StateVar Entry = [0, 10]", "DebugIntIntervalContext"),
    ("@Post Entry(Exit) == 3", "VarRefAtExitContext"),
    ("@During x(Before) < x(After)", "VarRefAtBeforeContext"),
    ("@During f.arg[0] == 10", "DuringFunctionArgContext"),
    ("@Post x not in [0, 10]", "RelationalCmpContext"),
    ("@Param flag = any", "DebugBoolTokenContext"),
    ("@Post x == floor(y, 2)", "FloorContext"),
    ("@Post x == ceil(y, 2)", "CeilContext"),
    ("@Post x == PercentOf(y, 5)", "PercentOfContext"),
    ("@Post changed(x, true)", "VarChangedEvalContext"),
    ("@Post floor == 3", "RelationalCmpContext"),
    ("@Post not == 3", "RelationalCmpContext"),
    ("@Post s.array.length > 0", "IntentMemberAccessContext"),
    ("@During require feasible", "DuringFeasibleContext"),
    ("@Param xs = arrayAddress [1, 2]", "DebugAddressArrayContext"),
    ("@Param a = symbolicAddress 3", "DebugSymbolicAddressContext"),
    ("@GlobalVar msg.sender = symbolicAddress 1", "GlobalVarSimpleContext"),
    ("@Post returnExpression == 3", "ReturnExprCmpContext"),
    ("@IReturn feed.floor() = [1,1]", "IReturnPatternAContext"),
]
for src, want in MATRIX:
    errs = ParserHelpers.annotation_syntax_errors(src)
    check(src, not errs and want in alts(src), errs[:1] or alts(src))
for src in ("@During require  feasible", "@Param x = array [1,]"):
    check("오류 유지: " + src, bool(ParserHelpers.annotation_syntax_errors(src)))

print("[4] 엔드투엔드: intent 단어와 같은 이름의 파라미터·상태변수에 값을 넣는다")
SRC = """contract C {
    uint256 public total;
    uint256 floor;
    function f(uint256 array) public {
        total = array + floor;
    }
}"""
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    s = engine_api.EngineSession()
    st = s.analyze(slice_solidity(SRC), contract="C", func="f")
    ev = s.evaluate({"array": {"t": "interval", "lo": "1", "hi": "2"},
                     "floor": {"t": "interval", "lo": "3", "hi": "3"},
                     "total": {"t": "interval", "lo": "0", "hi": "0"}}, contract="C", func="f")
check("진단 없음", not st.diagnostics, st.diagnostics[:1])
check("입력 오류 없음", not getattr(s, "_last_value_errors", []), getattr(s, "_last_value_errors", [])[:1])
total = next(n for n in ev.cfg.nodes if n.id == ev.cfg.exit).state.get("total")
check("EXIT total = [4,5]", total == "[4,5]", total)
with contextlib.redirect_stdout(buf):
    vs = s.verify([engine_api.IntentItem(scopeKind="Post", text="total >= floor + 1"),      # [4,5] >= [4,4]
                   engine_api.IntentItem(scopeKind="Post", text="total > floor + 2")],      # [4,5] >  [5,5] 위반
                  contract="C", func="f")
st_ = [str(v.status).lower() for v in vs]
check("@Post 가 키워드 이름을 참조 (만족 / 위반)", st_ == ["satisfied", "violated"],
      [(v.status, v.message) for v in vs])

print("실패 %d" % fails)
sys.exit(1 if fails else 0)
