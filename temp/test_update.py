"""EngineSession.update (B1 증분 apply) 등가 검증 (§5.6 5단계).
build(prev) → update(new)  ==  build(new) fresh. (evaluate 가 수술된 graph 전체 재해석.)"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
import engine_api
from Dependencies.main import slice_solidity

A = {"a": {"t": "interval", "lo": "0", "hi": "5"}}
def build(src):
    s = engine_api.EngineSession(); s.analyze(slice_solidity(src)); return s
def fp(ev): return sorted(str(n.state["b"]) for n in ev.cfg.nodes if n.state and "b" in n.state)

passed = 0; failed = 0
def check(label, cond, extra=""):
    global passed, failed
    if cond: passed += 1; print(f"  OK  {label} {extra}")
    else:    failed += 1; print(f"  FAIL {label} {extra}")

def equiv(label, prev, new):
    print(f"\n=== {label} ===")
    sA = build(prev)
    res = sA.update(new)
    check(f"{label}: update ok", res.get("ok") is True, str(res)[:80])
    if not res.get("ok"):
        return
    evA = sA.evaluate(A); fpA, ncA = fp(evA), len(evA.cfg.nodes)
    sB = build(new); evB = sB.evaluate(A); fpB, ncB = fp(evB), len(evB.cfg.nodes)
    print(f"   update: nodes={ncA} b={fpA}")
    print(f"   fresh : nodes={ncB} b={fpB}")
    check(f"{label}: b-fingerprint 등가", fpA == fpB)
    check(f"{label}: node 수 등가", ncA == ncB, f"({ncA} vs {ncB})")
    sA.close(); sB.close()

BASE = """contract C {
    function f(uint256 a) public pure returns (uint256) {
        uint256 b = a + 1;
        b = b + 2;
        return b;
    }
}"""

# 1) 단순 modify: 값 변경 b=b+2 → b=b+5
equiv("simple modify (value)", BASE, BASE.replace("b = b + 2;", "b = b + 5;"))

# 2) add: 새 statement 삽입
equiv("add statement", BASE,
      BASE.replace("        return b;", "        b = b * 2;\n        return b;"))

# 3) delete: statement 제거
equiv("delete statement", BASE, BASE.replace("        b = b + 2;\n", ""))

# 4) invalid 거부: dangling else
print("\n=== invalid reject ===")
sX = build(BASE)
resX = sX.update(BASE.replace("        b = b + 2;", "        else { b = b + 2; }"))
check("invalid → ok=False", resX.get("ok") is False, str(resX.get("diagnostics", []))[:60])
sX.close()

print(f"\n=== RESULT: {passed} passed, {failed} failed ===")
sys.exit(1 if failed else 0)
