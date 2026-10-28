"""의존성 레지스트리 (2026-10-23, R0~R5) 검증.

  · 파일 단위 + 고정점: 인터페이스 타입 순환(IA ↔ IB)이 빌드된다
  · 부모 누락 = Pending(미해석 이름), 부모 추가(R4) → 자식만 다시 분석해 Complete, 부모 struct 상속(A-2)
  · 교체·삭제(R4) → 영향받는 단위만 다시 분석
  · 로드: 선언당 객체 하나(사본 없음), 타깃 단위 대체 규칙, R5 게이트
"""
import pathlib
import shutil
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
from Analyzer.SolidityAnalyzer import SolidityAnalyzer  # noqa: E402
from Dependencies import registry as R  # noqa: E402

passed = 0; failed = 0
def check(label, cond, extra=""):
    global passed, failed
    if cond: passed += 1; print(f"  OK  {label} {extra}")
    else:    failed += 1; print(f"  FAIL {label} {extra}")

quiet = lambda *a, **k: None

IA = """pragma solidity ^0.8.0;

interface IA {
    function b() external returns (IB);
}
"""
IB = """pragma solidity ^0.8.0;

interface IB {
    function a() external returns (IA);
}
"""
C = """pragma solidity ^0.8.0;

contract C is P {
    S public s;
    function get() public view returns (uint256) {
        return s.x;
    }
}
"""
P = """pragma solidity ^0.8.0;

contract P {
    struct S {
        uint256 x;
    }
}
"""
P_NO_S = """pragma solidity ^0.8.0;

contract P {
    uint256 public y;
}
"""
FWD = """pragma solidity ^0.8.0;

interface IUser {
    function v() external returns (Kind);
}

enum Kind {
    A,
    B
}
"""


def statuses(root):
    _, m = R.current_build(root)
    return {u: x["status"] for u, x in m["units"].items()}


tmp = pathlib.Path(tempfile.mkdtemp(prefix="iq_registry_"))
try:
    root = tmp / "p"
    (root / "sources").mkdir(parents=True)
    for uid, text in (("IA", IA), ("IB", IB), ("C", C)):
        (root / "sources" / f"{uid}.sol").write_text(text, encoding="utf-8")

    print("=== T1. 순환 빌드 + 부모 누락 ===")
    units = [R.SourceUnit(uid=p.stem, text=p.read_text(encoding="utf-8")) for p in sorted((root / "sources").glob("*.sol"))]
    reg = R.ProjectRegistry(units); reg.build(log=quiet); reg.save(root)
    st = statuses(root)
    check("IA·IB Complete (타입 순환)", st["IA"] == st["IB"] == "Complete", str(st))
    check("C Pending (부모 P 없음)", st["C"] == "Pending", str(st))
    _, m = R.current_build(root)
    check("C 미해석에 P", "P" in m["units"]["C"]["unresolved"], str(list(m["units"]["C"]["unresolved"])))

    print("\n=== T2. R4 부모 추가 → 자식만 다시 분석, struct 상속(A-2) ===")
    reg = R.ProjectRegistry.from_build(root)
    done = reg.apply({"P": P}, log=quiet)
    reg.save(root); (root / "sources" / "P.sol").write_text(P, encoding="utf-8")
    check("다시 분석 = {P, C}", done == {"P", "C"}, str(sorted(done)))
    st = statuses(root)
    check("C Complete", st["C"] == "Complete", str(st))

    print("\n=== T3. 로드: 선언당 객체 하나 ===")
    sa = SolidityAnalyzer(); ca = sa.contract_analyzer
    rep = R.load(sa, ca, root, (), verbose=False)
    c, p = ca.contract_cfgs["C"], ca.contract_cfgs["P"]
    check("C.parent_cfgs['P'] is P", c.parent_cfgs["P"] is p)
    check("IA ↔ IB 객체 연결", ca.contract_cfgs["IA"] is not None and "IB" in ca.interface_names and "IA" in ca.interface_names)
    check("C 가 부모 struct S 를 가져옴(owner=P)", "S" in c.structDefs and getattr(c.structDefs["S"], "owner", None) == "P",
          str({k: getattr(v, 'owner', None) for k, v in c.structDefs.items()}))
    check("from_dependency 표시", all(getattr(x, "from_dependency", False) for x in (c, p)))

    print("\n=== T4. 타깃 단위 대체 ===")
    sa = SolidityAnalyzer(); ca = sa.contract_analyzer
    rep = R.load(sa, ca, root, ("C",), verbose=False)
    check("타깃 C → 의존 폐포 {P} 만", rep["units"] == ["P"] and rep["target_unit"] == "C", str(rep["units"]))
    check("C 는 로드되지 않음", "C" not in ca.contract_cfgs)
    try:
        R.load(SolidityAnalyzer(), SolidityAnalyzer().contract_analyzer, root, ("C", "Other"), verbose=False)
        check("선언 집합이 다르면 오류", False)
    except ValueError as ex:
        check("선언 집합이 다르면 오류", "다르다" in str(ex), str(ex)[:60])

    print("\n=== T5. R4 교체(부모 struct 제거) → 자식 Pending, 무관 단위는 그대로 ===")
    reg = R.ProjectRegistry.from_build(root)
    done = reg.apply({"P": P_NO_S}, log=quiet)
    reg.save(root); (root / "sources" / "P.sol").write_text(P_NO_S, encoding="utf-8")
    check("다시 분석 = {P, C}", done == {"P", "C"}, str(sorted(done)))
    _, m = R.current_build(root)
    check("C Pending, 미해석 S", m["units"]["C"]["status"] == "Pending" and "S" in m["units"]["C"]["unresolved"],
          str(m["units"]["C"]["unresolved"]))

    print("\n=== T6. R5 게이트 ===")
    try:
        R.load(SolidityAnalyzer(), SolidityAnalyzer().contract_analyzer, root, ("IA",), verbose=False,
               require_complete=True)
        check("Complete 폐포는 통과", True)
    except R.DependencyNotReady as ex:
        check("Complete 폐포는 통과", False, str(ex)[:80])
    (root / "sources" / "D.sol").write_text("pragma solidity ^0.8.0;\n\ncontract D {\n    C public c;\n}\n", encoding="utf-8")
    reg = R.ProjectRegistry.from_build(root)
    reg.apply({"D": (root / "sources" / "D.sol").read_text(encoding="utf-8")}, log=quiet); reg.save(root)
    try:
        R.load(SolidityAnalyzer(), SolidityAnalyzer().contract_analyzer, root, ("D",), verbose=False,
               require_complete=True)
        check("Pending 의존(C) → 거부", False)
    except R.DependencyNotReady as ex:
        check("Pending 의존(C) → 거부", "C = Pending" in str(ex), str(ex)[:90])

    print("\n=== T7. R4 삭제 ===")
    reg = R.ProjectRegistry.from_build(root)
    done = reg.apply({"P": None}, log=quiet)
    reg.save(root); (root / "sources" / "P.sol").unlink()
    _, m = R.current_build(root)
    check("P 사라짐, C 미해석에 P", "P" not in m["units"] and "P" in m["units"]["C"]["unresolved"],
          str(m["units"]["C"]["unresolved"]))
    check("IA·IB 는 다시 분석 안 함", "IA" not in done and "IB" not in done, str(sorted(done)))

    print("\n=== T8. 같은 이름을 두 단위가 선언 → 둘 다 Invalid ===")
    reg = R.ProjectRegistry.from_build(root)
    reg.apply({"IA2": IA}, log=quiet)
    check("IA·IA2 Invalid", reg.units["IA"].status == reg.units["IA2"].status == "Invalid",
          f"{reg.units['IA'].status} {reg.units['IA2'].status}")

    print("\n=== T9. 같은 파일 안 뒤쪽 최상위 선언 (재배치 없이 — F1 (b) 전까지 Pending 이 정상) ===")
    reg = R.ProjectRegistry([R.SourceUnit(uid="F", text=FWD)]); reg.build(log=quiet)
    check("F = Pending (Kind)", reg.units["F"].status == "Pending" and "Kind" in reg.units["F"].unresolved,
          f"{reg.units['F'].status} {sorted(reg.units['F'].unresolved)}")
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print(f"\n=== RESULT: {passed} passed, {failed} failed ===")
sys.exit(1 if failed else 0)
