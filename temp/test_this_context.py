# -*- coding: utf-8 -*-
"""`this.f()` 호출 문맥 회귀 (2026-11-16, KNOWN_ISSUES I17).

`this.f()` 는 메시지 호출이므로 callee 프레임에서 msg.sender = address(this), msg.value = 0 이고,
복귀하면 호출자의 msg.* 는 원래 값이어야 한다. 예전엔 호출자의 msg.sender 를 그대로 써서
`require(msg.sender == address(this))` 가 거짓 → EXIT ⊥ 였다. 내부 호출은 같은 프레임이라 그대로 공유한다.
사용: PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe temp/test_this_context.py
"""
import contextlib, io, os, sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import engine_api
from Dependencies.main import slice_solidity

fails = 0
def check(name, ok, extra=""):
    global fails
    print(("  ok  " if ok else "  FAIL ") + name + ("" if ok else "\n        " + str(extra)))
    fails += 0 if ok else 1


CASES = [
    ("this 프레임: msg.sender == address(this) 통과", """contract C {
    uint256 public t1;
    function g() external returns (uint256) {
        require(msg.sender == address(this));
        return 1;
    }
    function f() public {
        t1 = this.g();
    }
}""", "t1", "[1,1]"),
    ("복귀 후 호출자 msg.sender 복원", """contract C {
    uint256 public t1;
    function g() external returns (uint256) {
        require(msg.sender == address(this));
        return 1;
    }
    function f() public {
        uint256 a = this.g();
        require(msg.sender != address(this));
        t1 = a + 1;
    }
}""", "t1", "[2,2]"),
    ("this 프레임: msg.value = 0", """contract C {
    uint256 public t1;
    function g() external payable returns (uint256) {
        return msg.value;
    }
    function f() public payable {
        t1 = this.g();
    }
}""", "t1", "[0,0]"),
    ("this 프레임: 호출자가 owner 여도 this 는 owner 가 아님 → revert", """contract C {
    uint256 public t1;
    address public owner;
    function g() external returns (uint256) {
        require(msg.sender == owner);
        return 1;
    }
    function f() public {
        require(msg.sender == owner);
        t1 = this.g();
    }
}""", "t1", "⊥"),
    ("내부 호출은 호출자 msg.sender 공유", """contract C {
    uint256 public t1;
    function g() internal view returns (uint256) {
        require(msg.sender != address(this));
        return 3;
    }
    function f() public {
        t1 = g();
    }
}""", "t1", "[3,3]"),
]

for name, src, var, expect in CASES:
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            s = engine_api.EngineSession()
            st = s.analyze(slice_solidity(src), contract="C", func="f")
            vals = {d.key: {"t": "interval", "lo": "0", "hi": "0"} for d in st.debug_slots
                    if d.kind == "StateVar" and d.valueKind == "interval"}
            ev = s.evaluate(vals, contract="C", func="f")
        ex = next(n for n in ev.cfg.nodes if n.id == ev.cfg.exit)
        got = str(ex.state.get(var))
        check(name, got == expect, "%s = %s, 기대 %s" % (var, got, expect))
    except Exception as e:
        check(name, False, "예외 %s: %s" % (type(e).__name__, e))

print("실패 %d" % fails)
sys.exit(1 if fails else 0)
