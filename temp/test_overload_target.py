"""회귀 가드 — overload 타깃 지정(6차 검토 C9, 2026-10-28).

이름만으로 타깃을 지정하면 `Utils/CFG` 의 `name → {signature → FunctionCFG}` 에서
먼저 등록된 overload 가 **조용히** 선택된다. `param_types` 로 시그니처를 못박을 수 있어야 하고,
지정하지 않았을 때는 모호했다는 사실이 `target_resolution()` 에 남아야 한다.
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from engine_api import EngineSession, _norm_sig, _norm_type  # noqa: E402

SRC = """pragma solidity ^0.8.0;

contract Ov {
    uint256 public total;

    function put(uint256 a) public {
        total = total + a;
    }

    function put(address who, uint256 a) public {
        total = total + a + 1;
    }

    function plain(uint256 x) public {
        total = x;
    }
}
"""

fails = []


def check(name, got, want):
    ok = got == want
    print(f"  [{'OK ' if ok else 'FAIL'}] {name}: {got!r}" + ("" if ok else f"  (want {want!r})"))
    if not ok:
        fails.append(name)


def slots_of(st):
    return sorted(s.key for s in st.debug_slots if s.kind == "Param")


def run(func, param_types):
    sess = EngineSession()
    st = sess.analyze_source(SRC, contract="Ov", func=func, param_types=param_types)
    return sess, st


print("1) 타입 정규화")
check("uint -> uint256", _norm_type("uint"), "uint256")
check("user: 접두어 제거", _norm_type("user:IERC20"), "IERC20")
check("data location 제거", _norm_type("uint[] memory"), "uint256[]")
check("시그니처", _norm_sig(["uint", "address"]), "(uint256,address)")

print("\n2) param_types 로 overload 지정")
sess, st = run("put", ["uint256"])
res = sess.target_resolution()
check("선택 방식", res["selected_by"], "param_types")
check("후보 수", len(res["candidates"]), 2)
check("Param 슬롯", slots_of(st), ["a"])

sess, st = run("put", ["address", "uint256"])
res = sess.target_resolution()
check("선택 방식(2)", res["selected_by"], "param_types")
check("Param 슬롯(2)", slots_of(st), ["a", "who"])

print("\n3) 어휘가 달라도 맞춘다 (uint == uint256, user: 접두어)")
sess, st = run("put", ["uint"])
check("정규화 매칭", sess.target_resolution()["selected_by"], "param_types")
check("Param 슬롯(3)", slots_of(st), ["a"])

print("\n4) 지정하지 않으면 모호함이 기록된다")
sess, st = run("put", None)
check("선택 방식(미지정)", sess.target_resolution()["selected_by"], "first_registered_ambiguous")

print("\n5) overload 가 아닌 함수는 그대로 (회귀)")
sess, st = run("plain", None)
check("선택 방식(단일)", sess.target_resolution()["selected_by"], "unique")
check("Param 슬롯(단일)", slots_of(st), ["x"])
sess, st = run("plain", ["uint256"])
check("단일 + param_types", sess.target_resolution()["selected_by"], "unique")

print("\n6) 맞는 overload 가 없으면 오류")
try:
    run("put", ["bytes32", "bytes32", "bytes32"])
    check("ValueError 발생", False, True)
except ValueError as ex:
    check("ValueError 발생", "matches no overload" in str(ex), True)

print("\n" + ("실패 " + ", ".join(fails) if fails else "모두 통과"))
sys.exit(1 if fails else 0)
