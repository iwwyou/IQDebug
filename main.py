import json
from Analyzer.EnhancedSolidityVisitor import EnhancedSolidityVisitor
from Analyzer.EnhancedYulVisitor import EnhancedYulVisitor
from Analyzer.SolidityAnalyzer import SolidityAnalyzer
from Analyzer.DebugUnitAnalyzer import DebugBatchManager
from Utils.Helper                        import ParserHelpers     # ★ here
from Utils.YulHelper                     import YulParserHelpers
import time

sa                = SolidityAnalyzer()
contract_analyzer = sa.contract_analyzer
snapman           = contract_analyzer.snapman
batch_mgr         = DebugBatchManager(contract_analyzer, snapman)


def load_dependencies(deps_root, exclude=()):
    """프로젝트 단위 의존성 로드(전역 싱글턴 대상) — 본체는 Dependencies.main.load_project_deps (서버와 공용).
    2026-10-22: 전역 공용 트리(Dependencies/objectfile, Libraries/objectfile) 폐기."""
    from Dependencies.main import load_project_deps
    load_project_deps(sa, contract_analyzer, deps_root, exclude)


# 프로젝트 단위 의존성: 실행 드라이버가 환경변수로 지정 (evaluation/intent_validation/run_cases.py · evaluation/refactor/baseline_cases.py)
#   지정이 없으면 의존성 없이 시작한다(타깃이 의존성을 참조하면 분석 시점에 명시적 오류).
import os as _os
if _os.environ.get("IQ_DEPS_ROOT"):
    load_dependencies(_os.environ["IQ_DEPS_ROOT"],
                      [x for x in _os.environ.get("IQ_DEPS_EXCLUDE", "").split(",") if x])


def simulate_inputs(records, silent=False):
    in_testcase = False

    for rec in records:
        code, s, e, ev = \
            rec["code"], rec["startLine"], rec["endLine"], rec["event"]
        close_before = rec.get("closeBefore", False)

        # ───── Solidity 소스 반영 (add/modify/delete) ─────
        sa.update_code(s, e, code, ev, close_before)

        stripped = code.lstrip()

        # ---------- BEGIN / END ---------------------------------
        if stripped.startswith("// @Debugging BEGIN"):
            batch_mgr.reset()
            in_testcase = True
            continue

        if stripped.startswith("// @Debugging END"):
            batch_mgr.flush()           # 전체 해석
            in_testcase = False
            continue

        # ---------- 주석(어노테이션) ----------------------
        if stripped.startswith("// @"):
            # Intent annotation (@During, @Post)은 코드의 일부로 영구 등록
            # → batch_mgr(snapshot/restore)를 거치지 않음
            if stripped.startswith("// @During") or stripped.startswith("// @Post"):
                # update_code 는 루프 맨 위에서 이미 이 레코드에 대해 호출됨(close_before 포함).
                # 여기서 다시 호출하면 같은 라인에 두 번 적용되어, 첫 호출 시점엔 아직 없던
                # 상태(multi-line statement 의 startLine 텍스트가 빈 경우 등)를 오판해
                # 불필요한 라인 밀기가 발생한다.
                tree = ParserHelpers.generate_parse_tree(code, "intentUnit")
                EnhancedSolidityVisitor(contract_analyzer).visit(tree)
                continue

            # Debug annotation (@Param, @StateVar, @GlobalVar, @IReturn)
            # list 기반 batch 전환(2026-07-03): 라인 키가 없어졌으므로 modify/delete 는
            # 개별 라인 갱신이 불가 → add 로 누적만. (레거시 CLI 경로, 회귀 무시 승인됨)
            if ev != "delete":
                batch_mgr.add(code)

            # BEGIN~END 밖이면 즉시 해석
            if not in_testcase:
                batch_mgr.flush()
            continue

        # ---------- 일반 Solidity / Yul 한 줄 --------------------
        if code.strip():           # 공백 라인은 생략
            ctx = contract_analyzer.get_current_context_type()
            if ctx == "assembly":
                # Yul parser + visitor
                tree = YulParserHelpers.generate_parse_tree(code)
                EnhancedYulVisitor(contract_analyzer).visit(tree)
            else:
                tree = ParserHelpers.generate_parse_tree(code, ctx, True)
                EnhancedSolidityVisitor(contract_analyzer).visit(tree)

            if not silent:
                analysis = contract_analyzer.get_line_analysis(s, e)
                for ln, recs in analysis.items():
                    for r in recs:
                        print(f"L{ln:3} | {r['kind']:>12} | {r['vars']}")


if __name__ == "__main__":
    import sys, pathlib
    if len(sys.argv) < 2:
        print("Usage: python main.py <case.json>")
        sys.exit(1)
    path = sys.argv[1]
    records = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    print(f"=== {pathlib.Path(path).name} ({len(records)} records) ===\n")
    t0 = time.perf_counter()
    simulate_inputs(records)
    elapsed = time.perf_counter() - t0
    print(f"\n[TIMING] {elapsed:.4f}s")

