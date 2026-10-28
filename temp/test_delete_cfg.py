"""delete_cfg_range 등가 검증 (§5.6 2단계).
full 빌드 → delete_cfg_range → evaluate  ==  reduced 소스 fresh 빌드 → evaluate.
(evaluate 는 수술된 graph 위에서 full fixpoint 재해석 → graph 수술 정확성을 검증.)"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
from Dependencies.main import slice_solidity
import engine_api

A = {"a": {"t": "interval", "lo": "0", "hi": "5"}}

def build(src):
    sess = engine_api.EngineSession()
    sess.analyze(slice_solidity(src))
    return sess

def fcfg_of(sess):
    return sess._resolve_fcfg(None, None)[2]

def b_fingerprint(ev):
    """모든 노드의 state['b'] 다중집합 (의미 fingerprint)."""
    vals = []
    for n in ev.cfg.nodes:
        if n.state and "b" in n.state:
            vals.append(str(n.state["b"]))
    return sorted(vals)

def node_count(ev):
    return len(ev.cfg.nodes)

passed = 0; failed = 0
def check(label, cond, extra=""):
    global passed, failed
    if cond: passed += 1; print(f"  OK  {label} {extra}")
    else:    failed += 1; print(f"  FAIL {label} {extra}")

def equiv(label, full_src, reduced_src, del_line):
    print(f"\n=== {label} (delete L{del_line}) ===")
    # delete path
    sA = build(full_src)
    seeds = sA._ca.builder.delete_cfg_range(fcfg=fcfg_of(sA), start_line=del_line)
    evA = sA.evaluate(A)
    fpA, ncA = b_fingerprint(evA), node_count(evA)
    # fresh reduced
    sB = build(reduced_src)
    evB = sB.evaluate(A)
    fpB, ncB = b_fingerprint(evB), node_count(evB)
    print(f"   delete:  nodes={ncA} b={fpA}")
    print(f"   reduced: nodes={ncB} b={fpB}")
    check(f"{label}: b-fingerprint 등가", fpA == fpB)
    check(f"{label}: node 수 등가", ncA == ncB, f"({ncA} vs {ncB})")
    sA.close(); sB.close()

IF_FULL = """contract C {
    function f(uint256 a) public pure returns (uint256) {
        uint256 b = a + 1;
        if (a > 10) {
            b = b + a;
            b = b + 1;
        }
        return b;
    }
}"""
IF_NOBLOCK = """contract C {
    function f(uint256 a) public pure returns (uint256) {
        uint256 b = a + 1;
        return b;
    }
}"""
IF_ONELINE = """contract C {
    function f(uint256 a) public pure returns (uint256) {
        uint256 b = a + 1;
        if (a > 10) {
            b = b + a;
        }
        return b;
    }
}"""
WHILE_FULL = """contract C {
    function f(uint256 a) public pure returns (uint256) {
        uint256 b = 0;
        while (b < a) {
            b = b + 1;
        }
        return b;
    }
}"""
WHILE_NOLOOP = """contract C {
    function f(uint256 a) public pure returns (uint256) {
        uint256 b = 0;
        return b;
    }
}"""

# 1) 단순 statement 삭제: if 본문의 'b = b + 1;'(L6) 제거 → if 안에 b=b+a 만
equiv("simple-stmt delete", IF_FULL, IF_ONELINE, del_line=6)

# 2) if 블록 통째 삭제 (cond@L4) → 블록 없음
equiv("if-block delete", IF_FULL, IF_NOBLOCK, del_line=4)

# 3) while 블록 통째 삭제 (cond@L4) → 루프 없음
equiv("while-block delete", WHILE_FULL, WHILE_NOLOOP, del_line=4)

# 4) reinterpret=True 경로가 예외 없이 도는지 (incremental 재해석)
print("\n=== delete smoke (Phase 3a: 재해석 없음) ===")
try:
    sC = build(IF_FULL)
    sC._ca.builder.delete_cfg_range(fcfg=fcfg_of(sC), start_line=4)
    check("delete 무예외", True)
    sC.close()
except Exception as ex:
    import traceback; traceback.print_exc()
    check("delete 무예외", False, str(ex))

print(f"\n=== RESULT: {passed} passed, {failed} failed ===")
sys.exit(1 if failed else 0)
