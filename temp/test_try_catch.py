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


def at(ev, line):
    """그 **표시 줄**의 노드 하나. 줄 단사성(S2)이 깨지면 바로 드러나게 유일성을 요구한다.

    평문 문장 줄에만 쓴다 — 조건 줄에는 분기 더미(`try_true`/`catch_true`)가 같은 줄에 있다.
    노드 이름으로 찾지 않는 이유: 구조가 바뀌면 이름 기반 단언은 전부 깨진다.
    """
    ns = [n for n in ev.cfg.nodes if n.display_line == line]
    if len(ns) != 1:
        raise AssertionError(f"L{line}: 표시 줄의 노드가 {len(ns)}개 "
                             f"({[n.name for n in ns]}) — 하나여야 한다")
    return ns[0]


def st_out(ev, line, var):
    return at(ev, line).state.get(var)


def st_in(ev, line, var):
    """IN 상태. 루프 본문 문장 노드는 IN 이 비어 있으므로(`engine_api._in_of`) OUT 으로 읽는다."""
    n = at(ev, line)
    return (n.in_state if n.in_state else n.state).get(var)


# (2026-10-29 재설계) catch 들은 if/else-if 와 **동형인 순서 있는 조건 체인**이다.
#   MULTI 의 줄 배치:  L13 try / L14,L15 try 본문 / L16 catch Error / L17,L18 본문
#                      L19 catch (bytes) / L20 본문 / L21 체인 닫는 `}` / L22 return
print("=== T1. 다중 catch — 조건 체인 ===")
s = engine_api.EngineSession()
st = s.analyze(slice_solidity(MULTI), contract="C", func="f")
check("진단 없음", st.diagnostics == [], str(st.diagnostics))
f = s._ca.contract_cfgs["C"].get_function_cfg("f")
G = f.graph


def succ_on(G, n, want):
    return [b for b in G.successors(n) if G[n][b].get("condition") is want]


def ctype(n):
    return getattr(n, "condition_node_type", None)


cond = next(n for n in G.nodes if n.name.startswith("try_cond_"))
check("try 의 condition_expr 는 None (술어가 아니다)", cond.condition_expr is None, str(cond.condition_expr))
check("try 가 호출을 statement 로 들고 있다",
      any(getattr(st_, "statement_type", None) == "variableDeclaration" for st_ in cond.statements),
      str([getattr(x, "statement_type", None) for x in cond.statements]))
check("try true 후속 1개", len(succ_on(G, cond, True)) == 1)
fs = succ_on(G, cond, False)
check("try false 후속 = 첫 catch cond 하나",
      len(fs) == 1 and ctype(fs[0]) == "catch", str([b.name for b in fs]))
c1 = fs[0]
fs2 = succ_on(G, c1, False)
check("catch1 --false--> catch2 (체인)",
      len(fs2) == 1 and ctype(fs2[0]) == "catch", str([b.name for b in fs2]))
c2 = fs2[0]
check("마지막 catch 에는 false 간선이 없다 (맞는 절이 없으면 예외가 전파된다)",
      succ_on(G, c2, False) == [], str([b.name for b in succ_on(G, c2, False)]))
check("try 본문이 try_true 뒤",
      [b.name for b in G.successors(succ_on(G, cond, True)[0])] == ["Assign_14"])

ev = s.evaluate({"a": IV(1, 1), "last": IV(0, 0), "fails": IV(3, 3)}, contract="C", func="f")
#  각 절 "자기 경로" 의 상태는 그 절 본문 마지막 문장에서 본다 — 체인이라 `catch_join_16` 은
#  catch1 ⊔ catch2 의 join 이다(else-if 의 `else_if_join` 과 같다).
check("catch Error 경로: fails=4, out=7",
      st_out(ev, 18, "fails") == "[4,4]" and st_out(ev, 18, "out") == "[7,7]",
      f"L18 fails={st_out(ev, 18, 'fails')} out={st_out(ev, 18, 'out')}")
check("catch (bytes) 경로: fails=3, out=9",
      st_out(ev, 20, "fails") == "[3,3]" and st_out(ev, 20, "out") == "[9,9]",
      f"L20 fails={st_out(ev, 20, 'fails')} out={st_out(ev, 20, 'out')}")
j = node_state(ev, "try_join_")
check("join: last=[0,1], fails=[3,4]",
      str(j.get("last")) == "[0,1]" and str(j.get("fails")) == "[3,4]", str(j)[:120])
#  체인의 join 들은 **전부** 체인 닫는 `}` 줄(L21)로 재계산된다(else-if 와 같다). 그중 바깥
#  하나만 착지 노드로 남는 것은 프론트의 join 접기(`isCollapsedJoin`) 몫이고, 줄 단사성(S2)은
#  `temp/test_nav_symmetry.py` 가 착지 규칙 전체로 검사한다.
joins = sorted(n.name for n in ev.cfg.nodes if n.display_line == 21)
check("체인의 join 들이 닫는 `}`(L21) 로 모인다",
      joins == ["catch_join_16", "catch_join_19", "try_join_13"], str(joins))

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

# ══════════════════════════════════════════════════════════════════════════════
#  T3–T7 (2026-10-29) try/catch 조건 체인 재설계의 수용 기준.
#
#  재설계 **전에** 먼저 박아 둔 단언들이다(그때는 10개 전부 실패했다). 노드 이름이 아니라
#  **상태·줄 번호**로 단언하므로 구조가 또 바뀌어도 그대로 쓸 수 있다.
#
#  확정 설계 (evaluation/reviews/round9_operations.md 와 라운드11 논의):
#    · catch 들을 if/else-if 와 동형인 **순서 있는 조건 체인**으로 만든다
#      (`catch_cond_N --true--> catch_true_N --> [body] --> catch_join_N`,
#       새 catch 는 마지막 `catch_cond` 의 false 간선에 붙고, **말단 false 간선은 없다**)
#    · `try_cond`·`catch_cond` 의 `condition_expr = None`
#      → refine·feasibility 경로를 타지 않으므로 두 분기가 **항상** feasible
#    · 호출식은 `try_cond` 의 **statement**(`VarDecl`/`VarDeclTuple`, RHS = 호출)
#      → 반환값 바인딩·callee 등록·평가 1회
#    · `returns` 변수는 `try_join` 의 `scope_anchor` 로 스코프 아웃
#
#  고쳐진 결함(재설계 전 상태): `try_cond` 가 `condition_node=True` 이면서 `condition_expr` 에
#  **호출식**을 들고 있어서, 간선 flow 계산이 그 호출식을 **술어로 취급**했다
#  (`Engine._edge_env_from_pred` / `_edge_flow_from_node_out`). `_branch_feasible` 가 호출
#  결과를 truthiness 로 보므로 결과가 정확히 0/1 이거나 BoolInterval 이면 한쪽 분기가 ⊥ 가 됐다.
#  루프 **밖**에서는 `_run_worklist` 의 try 핸들러가 두 후속을 미리 채워 둬서 그 ⊥ 가 버려졌지만,
#  루프 **안**에서는 `_fixpoint_impl` 에 그런 핸들러가 없어(그쪽 worklist 는 조건 타입으로
#  분기하지 않는다) 그대로 반영됐다 — T3/T4 가 그 반례다.
# ══════════════════════════════════════════════════════════════════════════════

IN_LOOP = """pragma solidity ^0.8.0;

contract C {
    uint256 public total;

    function h(uint256 x) external returns (uint256) {
        return %s;
    }

    function f(uint256 n) public {
        uint256 s = 0;
        while (s < 3) {
            try this.h(n) returns (uint256 r) {
                s = s + 1;
            } catch {
                s = 99;
            }
        }
        total = s;
    }
}
"""

NO_LOOP = """pragma solidity ^0.8.0;

contract C {
    uint256 public total;

    function h(uint256 x) external returns (uint256) {
        return %s;
    }

    function f(uint256 n) public {
        uint256 s = 0;
        try this.h(n) returns (uint256 r) {
            s = %s;
        } catch {
            s = 99;
        }
        total = s;
    }
}
"""

INPUT = {"n": IV(0, 10), "total": IV(0, 0)}


def run_f(src):
    sess = engine_api.EngineSession()
    sess.analyze(slice_solidity(src), contract="C", func="f")
    return sess.evaluate(INPUT, contract="C", func="f")


print("\n=== T3. 루프 안 try/catch — h:return 1 → catch 분기가 죽어서는 안 된다 ===")
ev = run_f(IN_LOOP % "1")
#  L14 `s = s + 1;` (try 본문)  L16 `s = 99;` (catch 본문)  L19 `total = s;`
check("try 본문은 살아 있다 (s=[1,3])", st_out(ev, 14, "s") == "[1,3]", str(st_out(ev, 14, "s")))
check("catch 본문이 ⊥ 가 아니다", st_out(ev, 16, "s") != "⊥", f"L16 OUT s={st_out(ev, 16, 's')}")
check("catch 본문: s=[99,99]", st_out(ev, 16, "s") == "[99,99]", f"L16 OUT s={st_out(ev, 16, 's')}")
check("루프 뒤: s 가 catch 경로를 포함 = [3,99]",
     st_in(ev, 19, "s") == "[3,99]", f"L19 IN s={st_in(ev, 19, 's')}")

print("\n=== T4. 루프 안 try/catch — h:return 0 → try 분기가 죽어서는 안 된다 ===")
ev = run_f(IN_LOOP % "0")
check("catch 본문은 살아 있다 (s=[99,99])", st_out(ev, 16, "s") == "[99,99]", str(st_out(ev, 16, "s")))
check("try 본문이 ⊥ 가 아니다", st_out(ev, 14, "s") != "⊥", f"L14 OUT s={st_out(ev, 14, 's')}")
check("try 본문: s=[1,3]", st_out(ev, 14, "s") == "[1,3]", f"L14 OUT s={st_out(ev, 14, 's')}")

print("\n=== T5. 루프 밖 try/catch (대조군 — 지금도 맞아야 한다) ===")
ev = run_f(NO_LOOP % ("1", "1"))
#  L13 `s = 1;` (try 본문)  L15 `s = 99;` (catch 본문)  L17 `total = s;`
check("try 본문: s=[1,1]", st_out(ev, 13, "s") == "[1,1]", str(st_out(ev, 13, "s")))
check("catch 본문: s=[99,99]", st_out(ev, 15, "s") == "[99,99]", str(st_out(ev, 15, "s")))
check("try/catch 뒤: 두 분기 join = [1,99]",
      st_in(ev, 17, "s") == "[1,99]", f"L17 IN s={st_in(ev, 17, 's')}")

print("\n=== T6. `try ... returns (r)` 반환값 바인딩 ===")
ev = run_f(NO_LOOP % ("x + 1", "r"))
check("try 본문에서 r = [1,11]", st_in(ev, 13, "r") == "[1,11]", f"L13 IN r={st_in(ev, 13, 'r')}")
check("s = r → s = [1,11]", st_out(ev, 13, "s") == "[1,11]", f"L13 OUT s={st_out(ev, 13, 's')}")
check("try/catch 뒤: s = [1,99]", st_in(ev, 17, "s") == "[1,99]", f"L17 IN s={st_in(ev, 17, 's')}")

print("\n=== T7. `returns` 변수의 스코프 (try 성공 블록 한정) ===")
#  같은 결과를 재사용한다. r 은 try 앞(L11)·try/catch 뒤(L17)에 보이면 안 된다.
check("try 앞(L11)에 r 이 없다", "r" not in at(ev, 11).in_state,
     f"L11 IN r={at(ev, 11).in_state.get('r')}")
check("try/catch 뒤(L17)에 r 이 없다", "r" not in at(ev, 17).in_state,
     f"L17 IN r={at(ev, 17).in_state.get('r')}")

print("\n=== T8. `try new C(...)` 는 명시적으로 거부한다 (지원 범위 밖) ===")
#  빌더의 try 체인은 "try 표현식 = 외부 함수 호출" 만 다룬다. 데이터셋 전체 0건이라 지원하지
#  않기로 했고, 조용히 오분석하는 대신 입력 단계에서 거부한다(`Dependencies.main._UNSUPPORTED`).
from Utils.Errors import SourceError      # noqa: E402

TRY_NEW = """contract T {
    uint256 public v;
}
contract C {
    T public t;
    function f(uint256 n) public {
        try new T() returns (T c) {
            t = c;
        } catch {
            t = T(address(0));
        }
    }
}"""
try:
    slice_solidity(TRY_NEW)
    check("try new 거부", False, "거부되지 않았다")
except SourceError as ex:
    check("try new 거부 (SourceError)", "지원하지 않는 구문" in str(ex), str(ex)[:110])
except Exception as ex:                   # 다른 예외로 죽으면 그것도 실패다
    check("try new 거부 (SourceError)", False, f"{type(ex).__name__}: {str(ex)[:90]}")

#  오탐 금지 — 문자열·주석 안의 `try new`, 그리고 `new` 단독은 통과해야 한다
NO_FP = {
    "문자열 안": 'contract C {\n    string public s;\n    function f() public {\n'
                 '        s = "try new thing";\n    }\n}',
    "라인 주석 안": 'contract C {\n    uint256 public v;\n    function f() public {\n'
                    '        v = 1; // try new C() here\n    }\n}',
    "`new` 단독": 'contract T {\n    uint256 public v;\n}\ncontract C {\n    T public t;\n'
                  '    function f() public {\n        t = new T();\n    }\n}',
}
for _lbl, _src in NO_FP.items():
    try:
        slice_solidity(_src)
        check(f"오탐 없음: {_lbl}", True)
    except Exception as ex:
        check(f"오탐 없음: {_lbl}", False, f"{type(ex).__name__}: {str(ex)[:90]}")

print(f"\n=== RESULT: {passed} passed, {failed} failed ===")
sys.exit(1 if failed else 0)
