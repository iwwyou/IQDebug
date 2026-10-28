"""try/catch 빌드·해석 (2026-10-23).

  · try 는 성공(True)·실패(False) 분기 모두 진행, catch 마다 False 분기 하나
  · 본문 문장이 각 분기 블록 안에 놓인다(예전: 모두 join 뒤에 차례로 붙어 순서대로 실행됐다)
  · if 안의 try/catch 뒤 `} else {` (ERC721._checkOnERC721Received 모양)
"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
import engine_api
from Dependencies.main import slice_solidity

passed = 0; failed = 0
def check(label, cond, extra=""):
    global passed, failed
    if cond: passed += 1; print(f"  OK  {label} {extra}")
    else:    failed += 1; print(f"  FAIL {label} {extra}")

IV = lambda lo, hi: {"t": "interval", "lo": str(lo), "hi": str(hi)}

MULTI = """pragma solidity ^0.8.0;

interface IR {
    function ping(uint256 a) external returns (uint256);
}

contract C {
    uint256 public last;
    uint256 public fails;

    function f(address r, uint256 a) public returns (uint256) {
        uint256 out = 0;
        try IR(r).ping(a) returns (uint256 v) {
            out = v;
            last = 1;
        } catch Error(string memory reason) {
            fails = fails + 1;
            out = 7;
        } catch (bytes memory data) {
            out = 9;
        }
        return out;
    }
}
"""
NESTED = """pragma solidity ^0.8.0;

interface IR {
    function ping(uint256 a) external returns (uint256);
}

contract C {
    uint256 public last;

    function f(address r, uint256 a) public returns (uint256) {
        if (a > 0) {
            try IR(r).ping(a) returns (uint256 v) {
                return v;
            } catch (bytes memory reason) {
                if (reason.length == 0) {
                    return 4;
                } else {
                    return 5;
                }
            }
        } else {
            return 1;
        }
    }
}
"""


def node_state(ev, prefix):
    return next((n.state for n in ev.cfg.nodes if n.name.startswith(prefix)), None)


print("=== T1. 다중 catch ===")
s = engine_api.EngineSession()
st = s.analyze(slice_solidity(MULTI), contract="C", func="f")
check("진단 없음", st.diagnostics == [], str(st.diagnostics))
f = s._ca.contract_cfgs["C"].get_function_cfg("f")
G = f.graph
cond = next(n for n in G.nodes if n.name.startswith("try_cond_"))
falses = sorted(b.name for b in G.successors(cond) if G[cond][b].get("condition") is False)
check("catch 2개 = False 분기 2개", falses == ["catch_entry_16", "catch_entry_19"], str(falses))
check("본문이 try_true 뒤", [b.name for b in G.successors(next(n for n in G.nodes if n.name.startswith("try_true_")))] == ["Assign_14"])
ev = s.evaluate({"a": IV(1, 1), "last": IV(0, 0), "fails": IV(3, 3)}, contract="C", func="f")
c1, c2, j = node_state(ev, "catch_end_16"), node_state(ev, "catch_end_19"), node_state(ev, "try_join_")
check("catch1: fails=4, out=7", str(c1.get("fails")) == "[4,4]" and str(c1.get("out")) == "[7,7]", str(c1)[:120])
check("catch2: fails=3, out=9", str(c2.get("fails")) == "[3,3]" and str(c2.get("out")) == "[9,9]", str(c2)[:120])
check("join: last=[0,1], fails=[3,4]", str(j.get("last")) == "[0,1]" and str(j.get("fails")) == "[3,4]", str(j)[:120])

print("\n=== T2. if 안의 try/catch 뒤 else ===")
s = engine_api.EngineSession()
st = s.analyze(slice_solidity(NESTED), contract="C", func="f")
check("진단 없음", st.diagnostics == [], str(st.diagnostics))
f = s._ca.contract_cfgs["C"].get_function_cfg("f")
icond = next(n for n in f.graph.nodes if n.name == "if_condition_11")
check("바깥 else 가 if_condition_11 의 False 분기",
      any(b.name.startswith("else_block_") and f.graph[icond][b].get("condition") is False for b in f.graph.successors(icond)))
ev = s.evaluate({"a": IV(0, 0)}, contract="C", func="f")
check("a=0 → try 분기 도달 불가(⊥)", str((node_state(ev, "try_cond_") or {}).get("a")) == "⊥", str(node_state(ev, "try_cond_"))[:80])

print(f"\n=== RESULT: {passed} passed, {failed} failed ===")
sys.exit(1 if failed else 0)
