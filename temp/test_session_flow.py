"""서버가 오케스트레이션하는 전체 흐름: analyze → update(증분) → evaluate → verify.
증분 update 후에도 states/verify 가 정상인지(갱신된 graph 위에서)."""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
import engine_api
from engine_api import IntentItem
from Dependencies.main import slice_solidity

A = {"a": {"t": "interval", "lo": "0", "hi": "5"}}
passed = 0; failed = 0
def check(label, cond, extra=""):
    global passed, failed
    if cond: passed += 1; print(f"  OK  {label} {extra}")
    else:    failed += 1; print(f"  FAIL {label} {extra}")

BASE = """contract C {
    function f(uint256 a) public pure returns (uint256) {
        uint256 b = a + 1;
        b = b + 2;
        return b;
    }
}"""
EDIT = BASE.replace("b = b + 2;", "b = b + 5;")  # 값 변경 (modify)

# 1) 첫 analyze (fresh)
sess = engine_api.EngineSession()
st0 = sess.analyze(slice_solidity(BASE))
check("analyze: slots 존재", len(st0.debug_slots) >= 1)

# 2) 증분 update (값 변경)
res = sess.update(EDIT)
check("update ok", res.get("ok") is True)

# 3) structure() 재직렬화 (서버가 호출)
st1 = sess.structure()
check("structure 후 cfg nodes>0", len(st1.cfg.nodes) > 0)

# 4) evaluate (update 후 states)
ev = sess.evaluate(A)
bvals = {str(n.state.get("b")) for n in ev.cfg.nodes if n.state and "b" in n.state}
check("evaluate: b=[6,11] 반영(수정값)", "[6,11]" in bvals, str(sorted(bvals)))

# 5) verify (standalone P2, update+evaluate 후)
vd = sess.verify([IntentItem(scopeKind="During", text="b > 0", line=3)])
check("verify: 결과 1개", len(vd) == 1, vd[0].status if vd else "")
check("verify: b>0 Satisfied", vd and vd[0].status == "Satisfied", vd[0].status if vd else "")

vd2 = sess.verify([IntentItem(scopeKind="During", text="b > 100", line=3)])
check("verify: b>100 Violated/Warning", vd2 and vd2[0].status in ("Violated", "Warning"),
      vd2[0].status if vd2 else "")

sess.close()
print(f"\n=== RESULT: {passed} passed, {failed} failed ===")
sys.exit(1 if failed else 0)
