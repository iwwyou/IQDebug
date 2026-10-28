"""증분 업데이트 트랜잭션 검증 (2026-10-22 사용자 결정).

update/analyze 가 만드는 연산 전체를 후보(세션 사본)에 적용하고 전부 성공해야 확정한다.
  · 사용자 소스 오류(SourceError) → {"ok": False, diagnostics(semantic)}, 마지막 정상 상태 유지, 세션 계속 사용
  · 엔진 내부 예외 → IncrementalSurgeryError, 마지막 정상 상태 유지
  · 현재 소스에 오류가 있으면 evaluate/verify 거부
"""
import pickle
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
import engine_api
from Dependencies.main import slice_solidity
from Utils.Errors import SourceError

passed = 0; failed = 0
def check(label, cond, extra=""):
    global passed, failed
    if cond: passed += 1; print(f"  OK  {label} {extra}")
    else:    failed += 1; print(f"  FAIL {label} {extra}")

def fp(sess, fn):
    """구조 지문: (노드 id, 이름, 줄) 집합 — 정상 상태가 그대로인지 비교용."""
    st = sess.structure("C", fn)
    return sorted((n.id, n.name, n.src_line) for n in st.cfg.nodes)

S0 = """contract C {
    function g(uint256 a) public pure returns (uint256) {
        uint256 b = a;
        return b;
    }
}"""
S1 = S0[:-1] + """    function f(P memory p) public pure returns (uint256) {
    }
}"""
S3 = """contract C {
    struct P {
        uint256 x;
    }
    function g(uint256 a) public pure returns (uint256) {
        uint256 b = a;
        return b;
    }
}"""
S4 = S3[:-1] + """    function f(P memory p) public pure returns (uint256) {
        return p.x;
    }
}"""

print("=== T1. 오류 → 지움 → struct 추가 → 다시 씀 (사용자 시나리오) ===")
s = engine_api.EngineSession(); s.analyze(slice_solidity(S0), contract="C", func="g")
r = s.update(S1)
check("S1 거부(ok=False)", r["ok"] is False, str(r)[:100])
check("S1 진단 = semantic, 줄 6", r["diagnostics"] and r["diagnostics"][0]["category"] == "semantic"
      and r["diagnostics"][0]["line"] == 6, str(r["diagnostics"])[:120])
check("S2 지움 ok", s.update(S0)["ok"] is True)
check("S3 struct 추가 ok", s.update(S3)["ok"] is True)
check("S4 다시 씀 ok", s.update(S4)["ok"] is True)
ev = s.evaluate({}, contract="C", func="f")
check("S4 후 f evaluate", any(n.name.startswith("Return") for n in ev.cfg.nodes))

print("\n=== T2. 유효한 헤더 → 오류 헤더로 modify → 정상 상태 보존 → 되돌리기 ===")
s = engine_api.EngineSession(); s.analyze(slice_solidity(S4), contract="C", func="f")
before = fp(s, "f")
r = s.update(S4.replace("function f(P memory p)", "function f(Q memory p)"))
check("오류 헤더 거부", r["ok"] is False)
check("f 구조 그대로(id 포함)", fp(s, "f") == before)
try:
    s.evaluate({}, contract="C", func="f"); check("오류 중 evaluate 거부", False)
except SourceError as ex:
    check("오류 중 evaluate 거부", True, str(ex)[:70])
check("되돌리기 ok", s.update(S4)["ok"] is True)
check("되돌린 뒤 evaluate", s.evaluate({}, contract="C", func="f") is not None)

print("\n=== T3. modify = delete 성공 후 add 실패 → 전체 롤백 ===")
s = engine_api.EngineSession(); s.analyze(slice_solidity(S3), contract="C", func="g")
before = fp(s, "g")
r = s.update(S3.replace("uint256 b = a;", "Q memory b;"))
check("거부", r["ok"] is False, str(r["diagnostics"])[:90])
check("g 구조 그대로(지워진 노드 없음)", fp(s, "g") == before)

print("\n=== T4. 여러 연산 중 마지막에서 오류 → 앞 연산도 반영 안 함 ===")
s = engine_api.EngineSession(); s.analyze(slice_solidity(S3), contract="C", func="g")
before = fp(s, "g")
r = s.update(S3.replace("        uint256 b = a;\n", "        uint256 b = a;\n        uint256 c = 1;\n        Q memory d;\n"))
check("거부", r["ok"] is False)
check("앞 연산(c 선언)도 반영 안 됨", fp(s, "g") == before)

print("\n=== T5. 엔진 내부 예외 → IncrementalSurgeryError, 정상 상태 보존, 이후 계속 사용 ===")
s = engine_api.EngineSession(); s.analyze(slice_solidity(S3), contract="C", func="g")
before = fp(s, "g")
orig = engine_api.EngineSession._apply_delete
def boom(self, *a, **k):
    raise RuntimeError("injected")
engine_api.EngineSession._apply_delete = boom
try:
    s.update(S3.replace("        uint256 b = a;\n", "")); check("내부 예외 전파", False)
except engine_api.IncrementalSurgeryError as ex:
    check("내부 예외 = IncrementalSurgeryError (SourceError 로 위장 안 함)", True, str(ex)[:70])
finally:
    engine_api.EngineSession._apply_delete = orig
check("정상 상태 보존", fp(s, "g") == before)
check("이후 정상 update", s.update(S3.replace("uint256 b = a;", "uint256 b = a + 1;"))["ok"] is True)

print("\n=== T6. 처음 analyze 부터 오류 → 빈 정상 상태, 고치면 update 로 복구 ===")
s = engine_api.EngineSession()
st = s.analyze(slice_solidity(S1), contract="C", func="f")
check("analyze 진단", len(st.diagnostics) == 1 and st.diagnostics[0].category == "semantic")
check("고친 소스 update ok", s.update(S4)["ok"] is True)
check("f 구조 생김", len(fp(s, "f")) > 4)

print("\n=== T7. 성공한 update 뒤 바뀌지 않은 노드는 id 유지 ===")
s = engine_api.EngineSession(); s.analyze(slice_solidity(S3), contract="C", func="g")
ids0 = {n[1]: n[0] for n in fp(s, "g")}
s.update(S3.replace("        return b;\n", "        b = b + 1;\n        return b;\n"))
ids1 = {n[1]: n[0] for n in fp(s, "g")}
check("VarDecl 노드 id 유지", ids0.get("VarDecl_6") == ids1.get("VarDecl_6"), f"{ids0.get('VarDecl_6')} {ids1.get('VarDecl_6')}")

print("\n=== T8. 의존성 CFG 는 후보와 공유되고 타깃 분석으로 바뀌지 않는다 ===")
root = pathlib.Path(__file__).parent.parent
src = (root / "evaluation/Dataset/target_contraction_auto/web3bugs_70_H_04.sol").read_text(encoding="utf-8")
import re as _re   # 드라이버(run_cases.declared_names)와 같게: 타깃이 스스로 선언하는 이름은 의존성에서 뺀다
_own = sorted(set(_re.findall(r"^\s*(?:abstract\s+)?(?:contract|library|interface)\s+(\w+)",
                               _re.sub(r"//[^\n]*", "", src), _re.M)))
s = engine_api.EngineSession(str(root / "evaluation/Dataset/deps/w70"), tuple(_own))
deps = {k: v for k, v in s._ca.contract_cfgs.items() if getattr(v, "from_dependency", False)}
snap = {k: pickle.dumps(v) for k, v in deps.items()}
s.analyze(slice_solidity(src))
check("analyze 후 의존성 CFG 불변", all(pickle.dumps(deps[k]) == snap[k] for k in deps),
      str([k for k in deps if pickle.dumps(deps[k]) != snap[k]])[:100])
check("후보와 의존성 객체 공유(identity)", all(s._ca.contract_cfgs[k] is deps[k] for k in deps))

print("\n=== T9. 컨트랙트 수준 선언 삭제·수정 = 현재 소스 전체 재분석 (2026-10-22) ===")
s = engine_api.EngineSession(); s.analyze(slice_solidity(S4), contract="C", func="f")
r = s.update(S4.replace("    struct P {\n        uint256 x;\n    }\n", ""))
check("참조되는 struct 삭제 → 거부(semantic)", r["ok"] is False and r["diagnostics"][0]["category"] == "semantic",
      str(r["diagnostics"])[:90])
check("거부 후 정상 상태(P 유지)", "P" in s._ca.contract_cfgs["C"].structDefs)
SD = S4.replace("        uint256 x;\n", "        uint256 x;\n        uint256 y;\n")
s = engine_api.EngineSession(); s.analyze(slice_solidity(SD), contract="C", func="f")
check("멤버 삭제 ok", s.update(S4)["ok"] is True)
check("멤버 y 정의도 사라짐", list(s._ca.contract_cfgs["C"].structDefs["P"].members) == ["x"])   # (2026-10-23) members = {이름: SolType}
SG = S4.replace("    function g(uint256 a)", "    uint256 public t;\n    function g(uint256 a)")
s = engine_api.EngineSession(); s.analyze(slice_solidity(SG), contract="C", func="f")
check("안 쓰는 상태변수 삭제 ok", s.update(S4)["ok"] is True)
_svn = s._ca.contract_cfgs["C"].state_variable_node      # 상태변수가 하나도 없으면 노드 자체가 없다
check("상태변수 t 정의도 사라짐", _svn is None or "t" not in _svn.variables)
check("재분석 뒤 f evaluate", s.evaluate({}, contract="C", func="f") is not None)

print("\n=== T10. 입력 전제 위반(한 입력 = 한 문장) = 진단, 세션 유지 ===")
s = engine_api.EngineSession(); s.analyze(slice_solidity(S3), contract="C", func="g")
before = fp(s, "g")
r = s.update(S3.replace("        return b;\n", "        b = 1; return b;\n"))
check("다중 문장 줄 → 거부", r["ok"] is False, str(r["diagnostics"])[:90])
check("정상 상태 보존", fp(s, "g") == before)
check("고치면 ok", s.update(S3)["ok"] is True)
st = engine_api.EngineSession().analyze_source(S3.replace("    struct P {\n        uint256 x;\n    }\n",
                                                          "    enum E { A, B }\n"))
check("처음 소스의 한 줄 블록 → analyze_source 진단", len(st.diagnostics) == 1, str(st.diagnostics)[:90])

print(f"\n=== RESULT: {passed} passed, {failed} failed ===")
sys.exit(1 if failed else 0)
