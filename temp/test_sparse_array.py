"""희소 원소별 배열 (2026-10-23, 사용자 승인) — A = (N 길이, D summary, M 구체 셀, S 선택자 셀).

  · 큰 정적 배열(`uint256[16777215]`, struct 원소) 선언이 원소를 만들지 않는다
  · 구체 인덱스 쓰기 = strong, 미확정 인덱스 쓰기 = weak(겹치는 셀 + D)
  · 분기 join = 키 합집합에서 Read₁(k) ⊔ Read₂(k)
  · 지역 정적 배열은 0, delete 는 0, 정적 범위 밖 디버그 입력은 오류
"""
import sys, pathlib, time, tracemalloc
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
import engine_api
from Dependencies.main import slice_solidity
from Domain.Variable import ArrayVariable

passed = 0; failed = 0
def check(label, cond, extra=""):
    global passed, failed
    if cond: passed += 1; print(f"  OK  {label} {extra}")
    else:    failed += 1; print(f"  FAIL {label} {extra}")

IV = lambda lo, hi: {"t": "interval", "lo": str(lo), "hi": str(hi)}

SRC = """pragma solidity ^0.8.0;

contract C {
    uint24 public constant N = 16777215;

    struct Obs {
        uint208 amount;
        uint32 timestamp;
    }

    uint256[N] public big;
    Obs[N] public obs;

    function f(uint256 i, bool c) public returns (uint256) {
        big[3] = 10;
        big[i] = 20;
        if (c) {
            big[100] = 7;
        }
        uint256[4] memory loc;
        loc[1] = 5;
        obs[2].amount = 9;
        uint256 a = big[3];
        uint256 b = big[100];
        uint256 z = loc[0];
        uint256 w = obs[2].amount;
        return a + b + z + w;
    }
}
"""

tracemalloc.start()
t0 = time.perf_counter()
s = engine_api.EngineSession()
st = s.analyze(slice_solidity(SRC), contract="C", func="f")
check("진단 없음", st.diagnostics == [], str(st.diagnostics))
ev = s.evaluate({"i": IV(3, 5), "c": {"t": "bool", "v": "any"} if False else IV(0, 1)}, contract="C", func="f")
dt = time.perf_counter() - t0
peak = tracemalloc.get_traced_memory()[1] / 1e6
check("큰 정적 배열이어도 빠르고 작다", dt < 60 and peak < 500, f"{dt:.1f}s peak {peak:.0f}MB")

ret = next(n for n in ev.cfg.nodes if n.name.startswith("Return"))
stt = ret.state or {}
check("a = big[3] ∈ [10,20] (구체 쓰기 뒤 미확정 쓰기 i∈[3,5] — weak)", str(stt.get("a")) == "[10,20]", str(stt.get("a")))
check("b = big[100] = ⊤ ⊔ 7 (분기 한쪽만 씀, 다른 쪽은 진입 ⊤)", str(stt.get("b")).startswith("[0,"), str(stt.get("b")))
check("z = loc[0] = 0 (지역 정적 배열 0 초기화)", str(stt.get("z")) == "[0,0]", str(stt.get("z")))
check("w = obs[2].amount = 9 (struct 원소 구체화)", str(stt.get("w")) == "[9,9]", str(stt.get("w")))

f = s._ca.contract_cfgs["C"].get_function_cfg("f")
big = f.related_variables.get("big") or s._ca.contract_cfgs["C"].state_variable_node.variables["big"]
check("선언만으로는 원소를 만들지 않는다", isinstance(big, ArrayVariable) and len(big.elements) < 10, str(len(big.elements)))

print("\n=== 정적 범위 밖 디버그 입력 ===")
SRC2 = """pragma solidity ^0.8.0;

contract D {
    uint256[3] public xs;

    function g() public view returns (uint256) {
        return xs[1];
    }
}
"""
s2 = engine_api.EngineSession()
s2.analyze(slice_solidity(SRC2), contract="D", func="g")
ev2 = s2.evaluate({"xs[1]": IV(4, 4)}, contract="D", func="g")
r2 = next(n for n in ev2.cfg.nodes if n.name.startswith("Return"))
check("xs[1] 입력 반영", "[4,4]" in str((r2.state or {}).get("xs[1]", "")) or "[4,4]" in str(r2.state), str(r2.state)[:120])
try:
    s2.evaluate({"xs[5]": IV(1, 1)}, contract="D", func="g")
    check("xs[5] = 잘못된 디버깅 입력", False)
except Exception as ex:
    check("xs[5] = 잘못된 디버깅 입력", "범위 밖" in str(ex) or "slot" in str(ex).lower(), str(ex)[:90])

print(f"\n=== RESULT: {passed} passed, {failed} failed ===")
sys.exit(1 if failed else 0)
