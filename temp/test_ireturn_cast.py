"""@IReturn 캐스트 호출 조회 (2026-10-25).

- `IF(a).get()` 에 준 값은 같은 함수의 `IF(b).get()` 에 들어가지 않는다(예전 대체 조회는 주소를 보지 않았다).
- 캐스트 인자가 식(`IF(feeds[t])`)이면 입력 키가 없으므로 ⊤.
- `IF.get.selector`(29_H_08 처럼 abi.encodeWithSelector 안) 는 호출이 아니다 — IReturn 슬롯이 없다.
- 본문 있는 컨트랙트 타입 캐스트(`D(a).g()`)는 IReturn 입력이 아니다 — 슬롯이 없고, 직접 넣어도 명시적 오류.
"""
import io, contextlib, os, sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from engine_api import EngineSession  # noqa: E402

SRC = """pragma solidity ^0.8.0;
interface IF {
    function get() external view returns (uint256);
}
contract D {
    function g() external view returns (uint256) {
        return 1;
    }
}
contract C {
    address a;
    address b;
    mapping(uint256 => address) feeds;
    uint256 x;
    uint256 y;
    uint256 z;
    bytes d;
    function f(uint256 t) public {
        x = IF(a).get();
        y = IF(b).get();
        z = IF(feeds[t]).get();
        d = abi.encodeWithSelector(IF.get.selector, t);
    }
}
"""
ok = fail = 0


def check(name, cond, detail=""):
    global ok, fail
    ok += bool(cond)
    fail += (not cond)
    print(("PASS " if cond else "FAIL ") + name + (f"  {detail}" if detail and not cond else ""))


with contextlib.redirect_stdout(io.StringIO()):
    s = EngineSession()
    st = s.analyze_source(SRC, contract="C", func="f")
keys = {sl.key for sl in st.debug_slots if sl.kind == "IReturn"}
check("IReturn slots", keys == {"IF(a).get()", "IF(b).get()", "IF(feeds[t]).get()"}, str(sorted(keys)))
check("no selector slot", not any("selector" in k for k in keys), str(sorted(keys)))

with contextlib.redirect_stdout(io.StringIO()):
    ev = s.evaluate({"IF(a).get()": {"t": "interval", "lo": "7", "hi": "7"},
                     "t": {"t": "interval", "lo": "1", "hi": "1"}}, contract="C", func="f")
exit_state = next(n.state for n in ev.cfg.nodes if n.id == ev.cfg.exit) if hasattr(ev.cfg.nodes[0], "id") else None
if exit_state is None:
    exit_state = next(n for n in ev.cfg.nodes if getattr(n, "uid", None) == ev.cfg.exit).state
TOP = f"[0,{(1 << 256) - 1}]"
check("IF(a).get() = 7", exit_state.get("x") == "[7,7]", str(exit_state.get("x")))
check("IF(b).get() not given -> top", exit_state.get("y") == TOP, str(exit_state.get("y")))
check("IF(feeds[t]).get() -> top", exit_state.get("z") == TOP, str(exit_state.get("z")))
check("no diagnostics", not ev.diagnostics, str(ev.diagnostics))

with contextlib.redirect_stdout(io.StringIO()):
    ev = s.evaluate({"IF(feeds[t]).get()": {"t": "interval", "lo": "7", "hi": "7"}}, contract="C", func="f")
check("expression-cast key = explicit input error",
      any("읽을 수 없는 키" in d.message for d in ev.diagnostics), str(ev.diagnostics))

check("no contract-cast slot", not any(k.startswith("D(") for k in keys), str(sorted(keys)))
with contextlib.redirect_stdout(io.StringIO()):
    ev = s.evaluate({"D(a).g()": {"t": "interval", "lo": "7", "hi": "7"}}, contract="C", func="f")
check("contract-cast key rejected by API", any("디버그 슬롯이 아니다" in d.message for d in ev.diagnostics), str(ev.diagnostics))
try:
    s._ca.process_ireturn_cast("D", "a", "g", (), 7)
    check("contract-cast input rejected (Pattern B)", False, "no error")
except ValueError as e:
    check("contract-cast input rejected (Pattern B)", "인터페이스 호출 전용" in str(e), str(e))

print(f"{ok} passed, {fail} failed")
