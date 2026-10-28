"""빈 줄 @During = 그 위치의 프로그램 지점 (2026-10-23 사용자 결정).

  · 같은 블록 앞 문장 뒤        → 그 문장 실행 후 상태
  · 블록 첫 줄                  → 블록 진입 상태(if 참 분기·else·함수 ENTRY)
  · 블록 문장(`}`) 뒤           → 합류 후 상태
"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
import engine_api
from engine_api import IntentItem
from Dependencies.main import slice_solidity

passed = 0; failed = 0
def check(label, cond, extra=""):
    global passed, failed
    if cond: passed += 1; print(f"  OK  {label} {extra}")
    else:    failed += 1; print(f"  FAIL {label} {extra}")

SRC = """pragma solidity ^0.8.0;

contract C {
    function f(uint256 a) public returns (uint256) {

        uint256 x = a;

        if (a > 10) {

            x = 1;
        } else {

            x = 2;
        }

        return x;
    }
}
"""
#  줄: 5 = 함수 첫 줄(빈 줄), 7 = x 선언 뒤, 9 = if 참 분기 첫 줄, 12 = else 첫 줄, 15 = if/else 뒤
s = engine_api.EngineSession()
st = s.analyze(slice_solidity(SRC), contract="C", func="f")
check("진단 없음", st.diagnostics == [], str(st.diagnostics))
lines = SRC.splitlines()
for ln in (5, 7, 9, 12, 15):
    check(f"L{ln} 빈 줄", lines[ln - 1].strip() == "", repr(lines[ln - 1]))

A = {"a": {"t": "interval", "lo": "0", "hi": "20"}}


def verdict(text, line):
    s.evaluate(A, contract="C", func="f")
    vr = s.verify([IntentItem(scopeKind="During", text=text, line=line)], contract="C", func="f", debug_values=A)
    return vr[0] if vr else None


v = verdict("a <= 20", 5)
check("L5 함수 ENTRY: a <= 20 Satisfied", v is not None and v.status == "Satisfied", str(v))
v = verdict("x <= 20", 7)
check("L7 선언 뒤: x <= 20 Satisfied (구간 도메인이라 x == a 는 증명 못 함)", v is not None and v.status == "Satisfied", str(v))
v = verdict("a > 10", 9)
check("L9 if 참 분기 진입: a > 10 Satisfied (예전: if 조건 노드 = 분기 전 → Warning)", v is not None and v.status == "Satisfied", str(v))
v = verdict("a <= 10", 12)
check("L12 else 진입: a <= 10 Satisfied", v is not None and v.status == "Satisfied", str(v))
v = verdict("x <= 2", 15)
check("L15 합류 뒤: x <= 2 Satisfied (x ∈ {1,2})", v is not None and v.status == "Satisfied", str(v))
v = verdict("x == 1", 15)
check("L15 합류 뒤: x == 1 은 Satisfied 아님(두 분기 합쳐짐)", v is not None and v.status != "Satisfied", str(v))

print(f"\n=== RESULT: {passed} passed, {failed} failed ===")
sys.exit(1 if failed else 0)
