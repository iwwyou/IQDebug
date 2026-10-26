# Analyzer/DebugBatchManager.py
# ────────────────────────────
from Utils.Helper              import ParserHelpers
from Analyzer.EnhancedSolidityVisitor import EnhancedSolidityVisitor

class DebugBatchManager:
    """
    디버그 값 주석(`@StateVar/@Param/@GlobalVar/@IReturn <ref> = <val>`)을
    **삽입 순서대로 리스트에 누적**했다가 flush() 에서 한 번에 해석한다.

    설계(2026-07-03, list 기반 전환):
      - 프론트가 함수별 디버그 값을 통째로 넘기고 engine_api._queue_debug_values 가
        슬롯마다 add() 로 쌓는다.
      - 디버그 값 주석에는 라인번호가 무의미하다
        (visitDebugStateVar/Param/GlobalVar/IReturn 모두 line 미사용 → 값만 설정).
        과거 dict[startLine] 방식은 여러 슬롯이 같은 startLine(0)으로 들어오면 충돌해
        마지막 하나만 남는 버그가 있었다 → 순서 리스트로 교체.
      - snapshot/restore 로 감싸 매 디버깅이 독립적(값 초기화)인 성질은 유지.
    """
    def __init__(self, analyzer, snapman):
        self._lines: list[str] = []
        self.analyzer = analyzer
        self.snapman  = snapman

    # ── 라인 누적 ──────────────────────────────────────────
    def add(self, code: str):
        self._lines.append(code)

    # 하위호환: (code, s, e) 시그니처로 호출하던 기존 경로(main.py) 유지 — s,e 는 무시
    def add_line(self, code: str, s: int = 0, e: int = 0):
        self._lines.append(code)

    # ── flush : 누적된 모든 라인 순서대로 해석 ────────────────
    def flush(self):
        if not self._lines:
            return
        snap = self.snapman.snapshot()
        self.analyzer.debug_initializer.begin_batch()
        try:
            for code in self._lines:
                tree = ParserHelpers.generate_parse_tree(code, "intentUnit")
                EnhancedSolidityVisitor(self.analyzer).visit(tree)

            # 선택된 한 함수만 재-해석
            self.analyzer.flush_reinterpret_target()

            # 결과 전송
            self.analyzer.send_report_to_front(None)
        finally:
            self.snapman.restore_from_snap(snap)

    # ── 새 디버깅 배치 시작할 때 호출 ──────────────────────
    def reset(self):
        self._lines.clear()
