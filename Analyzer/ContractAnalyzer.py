from Utils.CFG import *
from Domain.AddressSet import address_manager, AddressSet
from Domain.Interval import IntegerInterval, UnsignedIntegerInterval, BoolInterval
from Domain.Type import SolType
from Utils.Helper import *
from Utils.Snapshot import *
from Analyzer.DynamicCFGBuilder import DynamicCFGBuilder
from Analyzer.RecordManager import RecordManager
from Analyzer.StaticCFGFactory import StaticCFGFactory
from Interpreter.Semantics.Evaluation import Evaluation
from Interpreter.Semantics.Update import Update
from Interpreter.Semantics.DebugInitializer import DebugInitializer
from Interpreter.Semantics.Refine import Refine
from Interpreter.Semantics.Storage import Storage
from Interpreter.Engine import Engine
from Analyzer.GuardianVerificationEngine import GuardianVerificationEngine

import re
from Utils.Errors import UnsupportedShadowing

class ContractAnalyzer:

    def __init__(self, solidity_analyzer):
        # SolidityAnalyzer 참조 — 소스 저장소(full_code_lines, full_code, line_info)는 sa가 소유
        self.sa = solidity_analyzer

        self.addr_mgr = address_manager  # 싱글톤 AddressManager
        self.snapman = SnapshotManager()
        self._batch_targets: set[FunctionCFG] = set()  # 🔹추가

        self.current_start_line = None
        self.current_end_line = None

        self.current_context_type = None
        self.current_target_contract = None
        self.current_target_function = None
        self.current_target_function_param_types: list[str] | None = None
        self.current_target_function_cfg = None
        self.current_target_struct = None

        self.current_edit_event = None
        self._record_enabled = False
        self._seen_stmt_ids: set[int] = set()
        self._last_touched_lines = None

        # for Multiple Contract
        self.contract_cfgs = {} # name -> CFG
        self.library_cfgs = {} # name -> LibraryCFG
        self.interface_names: set[str] = set()  # interface 이름 등록 (타입 인식용)
        self._interface_ranges: list[tuple[int, int]] = []  # (start, end) 라인 범위

        self.evaluator = Evaluation(self)
        self.updater = Update(self)
        self.debug_initializer = DebugInitializer(self)
        self.refiner = Refine(self)
        self.storage = Storage(self)          # (2026-10-23) mapping/array 접근 의미론
        self.engine = Engine(self)
        self.builder = DynamicCFGBuilder(self)
        self.recorder = RecordManager()
        self.guardian_verifier = GuardianVerificationEngine(self)

        self.analysis_per_line = self.recorder.ledger

        # ★ Intent annotation 저장용
        self.during_annotations: dict[int, list] = {}  # line_no -> [clause_dicts]
        self.post_annotations: dict[int, list] = {}    # line_no -> [clause_dicts]

        # ★ Pragma 정보 저장용
        self.pragma_directives: dict[str, str] = {}  # pragma_name -> pragma_value

    """
    Prev analysis part
    """

    # ────────────────────────────────────────────────────────────────
    #  Pragma 처리
    # ----------------------------------------------------------------
    def process_pragma_directive(self, pragma_name: str, pragma_value: str):
        """
        pragma directive를 처리하고 저장
        예: pragma solidity ^0.6.12;
        """
        self.pragma_directives[pragma_name] = pragma_value
        # 필요시 solidity 버전에 따른 처리 추가 가능

    # ────────────────────────────────────────────────────────────────
    #  ContractAnalyzer   (class body 안)
    # ----------------------------------------------------------------
    def _shift_cfg_meta(self, old_ln: int, new_ln: int):
        """
        소스 라인 이동(old_ln → new_ln)에 맞춰
        recorder.ledger / CFGNode.src_line / Statement.src_line 동기화.
        (line_info key 이동은 SolidityAnalyzer._shift_source_meta가 담당)
        """
        # ① line_info에 등록된 cfg_nodes들의 src_line 보정
        if old_ln in self.sa.line_info:
            info = self.sa.line_info[old_ln]
            if isinstance(info.get("cfg_nodes"), list):
                for node in info["cfg_nodes"]:
                    if hasattr(node, "src_line") and node.src_line == old_ln:
                        node.src_line = new_ln

        # ② recorder.ledger 이동
        if old_ln in self.recorder.ledger:
            self.recorder.ledger[new_ln] = self.recorder.ledger.pop(old_ln)

        # ③ 이미 생성된 CFG-Statement 들의 src_line 보정
        #   ★ (2026-10-22) **편집 중인 소스의 CFG 만**. 의존성 CFG(pkl 로드, from_dependency)의 줄 번호는 **그 파일**
        #     기준이라 타깃의 줄 이동과 무관하다. 예전엔 모든 CFG 를 돌며 "같은 번호" 노드를 옮겨, 타깃에 줄이 들어올
        #     때마다 의존성 함수 줄 번호가 누적해서 오염됐다(w70: 63개 함수, Context._msgSender 5 → 82).
        for ccf in self.contract_cfgs.values():
            if getattr(ccf, "from_dependency", False):
                continue
            for _, fcfg in ccf.iter_all_functions():
                for blk in fcfg.graph.nodes:
                    if getattr(blk, "src_line", None) == old_ln:
                        blk.src_line = new_ln
                    for st in blk.statements:
                        if getattr(st, "src_line", None) == old_ln:
                            st.src_line = new_ln

    def analyze_context(self, start_line, new_code):
        stripped_code = (new_code or "").strip()

        # 단독 '}'는 컨텍스트 분석 불필요 (괄호 정보만으로 충분)
        if stripped_code == "}":
            return

        # pragma, import는 contract 밖이므로 분석 불필요
        if stripped_code.startswith('pragma ') or stripped_code.startswith('import '):
            return

        if stripped_code.startswith('// @'):
            self.current_context_type = "debugUnit"
            self.current_target_contract = self.find_contract_context(start_line)
            self.current_target_function, self.current_target_function_param_types = self.find_function_context(start_line)
            return  # 이 함수 종료

        # 매 분석마다 초기화
        self.current_context_type = None
        self.current_target_contract = None
        self.current_target_function = None
        self.current_target_function_param_types = None
        self.current_target_struct = None

        # 새로 추가된 코드 블록의 컨텍스트를 분석
        if stripped_code.endswith(';'):
            if 'while' in stripped_code :
                self.current_context_type = "doWhileWhile"
                pass

            parent_context = self.find_parent_context(start_line)
            if parent_context in ["contract", "library", "interface",
                                  "abstract contract"]:  # 시작 규칙 : interactiveSourceUnit
                if parent_context == "interface":
                    # interface body 는 function 시그니처 / event 선언만 유효하다
                    # (state variable·constant 는 Solidity 가 금지).
                    # ★ 예전엔 function 이 아니면 전부 `return` 으로 "스킵" 했는데,
                    #   그 스킵은 **호출자에게 전달되지 않는다** — 위에서 이미
                    #   current_context_type/current_target_contract 를 None 으로
                    #   초기화한 뒤 그냥 돌아올 뿐이다. 드라이버
                    #   (`Dependencies/main.py`, `main.simulate_inputs`, `EngineSession`)
                    #   는 그걸 알 수 없어 ctx=None 으로 파싱·visit 을 계속했고,
                    #   `visitEventDefinition` → `process_event_definition` 의
                    #   `self.contract_cfgs[None]` 에서 `KeyError: None` 으로 터졌다
                    #   (인터페이스 15개 × event 선언 = 59건, 메시지가 `None` 인 이유도
                    #    `str(KeyError(None))` 이 'None' 이기 때문).
                    #   contract/library 쪽엔 이미 'event' 분기가 있으므로
                    #   인터페이스만 빠져 있던 것 — 같은 처리로 맞춘다.
                    #   ※ else 의 `return` 은 남긴다: 여러 줄 함수 시그니처의
                    #     연속 줄(`) external;`, `returns (uint256);`)이 여기로 오며,
                    #     이들은 stateVariableDeclaration 으로 오분류되면 안 된다.
                    if stripped_code.startswith('function '):
                        self.current_context_type = "functionDefinition"
                    elif stripped_code.startswith('event '):
                        self.current_context_type = "event"
                    else:
                        return
                    self.current_target_contract = self.find_contract_context(start_line)
                else:
                    if stripped_code.startswith('using '):
                        self.current_context_type = "usingDirective"
                    elif stripped_code.startswith('event '):
                        self.current_context_type = "event"
                    elif 'constant' in stripped_code or 'immutable' in stripped_code:
                        self.current_context_type = "constantVariableDeclaration"
                    else:
                        self.current_context_type = "stateVariableDeclaration"
                    self.current_target_contract = self.find_contract_context(start_line)
            elif parent_context == "struct":  # 시작 규칙 : interactiveStructUnit
                self.current_context_type = "structMember"
                self.current_target_contract = self.find_contract_context(start_line)
                self.current_target_struct = self.find_struct_context(start_line)
            else:  # constructor, function, --- # 시작 규칙 : interactiveBlockUnit
                self.current_context_type = "simpleStatement"
                self.current_target_contract = self.find_contract_context(start_line)
                self.current_target_function, self.current_target_function_param_types = self.find_function_context(start_line)

        elif ',' in stripped_code and '{' not in stripped_code:
            # 함수 정의인지 확인 (괄호 열고 닫힌 경우는 함수 파라미터로 가정)
            if '(' in stripped_code and ')' in stripped_code:
                self.current_context_type = "functionDefinition"
                self.current_target_contract = self.find_contract_context(start_line)
                self.current_target_function, self.current_target_function_param_types = self.find_function_context(start_line)

            # enum인지 확인
            else:
                parent_context = self.find_parent_context(start_line)
                if parent_context == "enum":
                    self.current_context_type = "enumMember"
                    self.current_target_contract = self.find_contract_context(start_line)

        elif '{' in stripped_code: # definition 및 block 관련
            # 여러 줄짜리 함수/modifier/constructor 정의의 마지막 줄일 수 있음
            # 예: "    ) external isAllowed {"
            # 이 경우 위로 올라가서 function/modifier/constructor를 찾아야 함
            # (2026-10-23) try/catch 도 제외 — `try IR(r).ping(a) returns (uint256 v) {` 가 함수 헤더 연속 줄로 오분류돼
            #   try 가 CFG 에 들어가지 않았다
            if ')' in stripped_code and not stripped_code.startswith(('function', 'constructor', 'modifier', 'contract', 'struct', 'enum', 'if', 'for', 'while', 'else', 'try', 'catch')):
                # 위로 올라가서 function/modifier/constructor 키워드 찾기
                for check_line in range(start_line - 1, 0, -1):
                    check_code = self.sa.full_code_lines.get(check_line, '').strip()
                    if check_code.startswith('function'):
                        self.current_context_type = 'functionDefinition'
                        self.current_target_contract = self.find_contract_context(start_line)
                        # print(f"[analyze_context] Line {start_line}: Found function, contract={self.current_target_contract}")
                        self.current_target_function = None  # 아직 함수가 생성되지 않음
                        return
                    elif check_code.startswith('modifier'):
                        self.current_context_type = 'modifier'
                        self.current_target_contract = self.find_contract_context(start_line)
                        return
                    elif check_code.startswith('constructor'):
                        self.current_context_type = 'constructor'
                        self.current_target_contract = self.find_contract_context(start_line)
                        return
                    # 빈 줄이나 파라미터 줄은 계속 위로
                    if not check_code or check_code.startswith(('address', 'uint', 'int', 'bool', 'string', 'bytes')):
                        continue
                    else:
                        break  # 다른 코드를 만나면 중단

            # Determine context type first
            ctx = self.determine_top_level_context(new_code)

            # statement 라인 (변수 선언, 대입 등)은 top-level context가 아님
            # BUT control flow (if/else/for/while etc) should be processed
            if ctx == 'simpleStatement':
                # function/constructor 내부의 일반 statement
                # current_context_type/contract/function은 그대로 유지
                return  # 더 이상 진행하지 않음
            elif '=' in stripped_code and ctx not in ['if', 'else_if', 'else', 'for', 'while', 'do_while', 'try', 'catch'] \
                    and not stripped_code.startswith(('function', 'constructor', 'modifier')):
                # 기타 assignment가 있는 statement
                return  # 더 이상 진행하지 않음
            else:
                self.current_context_type = ctx
                self.current_target_contract = self.find_contract_context(start_line)

            if self.current_context_type in ["contract", "library", "interface", "abstract contract"]:
                return

            self.current_target_function, self.current_target_function_param_types = self.find_function_context(start_line)


        # 최종적으로 context가 제대로 파악되지 않은 경우
        # 여러 줄짜리 정의문의 중간 줄이거나, 컨텍스트 분석이 불필요한 줄은 조용히 넘어감
        if not self.current_target_contract and self.current_context_type:
            # file-level struct/enum은 contract 바깥 → 스킵 (Phase 0에서 별도 수집)
            if self.current_context_type in ("struct", "structMember"):
                self.current_context_type = None
                return
            # context_type은 설정되었는데 contract를 찾지 못한 경우에만 오류
            raise ValueError(f"Contract context not found for line {start_line}")
        if self.current_context_type == "simpleStatement" and not self.current_target_function:
            raise ValueError(f"Function context not found for simple statement at line {start_line}")

    def _enclosing_open_brace_line(self, line_number: int, *, inclusive: bool = True) -> int | None:
        """`line_number` 를 감싸는 블록의 `{` 가 있는 줄 번호. 못 찾으면 None.

        위로 거슬러 올라가며 "닫힌 만큼 상쇄하고 남은 첫 `{`" 를 찾는다.

        ★ 왜 `line_info` 의 open/close **개수**를 안 쓰고 줄 텍스트를 훑는가:
          개수만으로는 **한 줄 안에서의 순서**를 알 수 없는데, 그 순서가 결정적이다.

            `} else {`         open=1 close=1 → `{` 가 오른쪽  → **이 줄이 나를 여는 줄**
            `function f() {}`  open=1 close=1 → `}` 가 오른쪽  → **상쇄**하고 계속 올라가야 함

          우리는 아래에서 위로 올라가는 중이므로, 줄 안에서도 **오른쪽 → 왼쪽**으로 읽으면
          자연히 맞는다.

        ★ 예전 구현의 버그(F27): "`}` 를 만나면 짝인 `{` 가 나올 때까지 건너뛴다" 는
          스킵 모드가 있었는데, **스킵 모드 안에서 만난 `}` 를 세지 않았다.**
          블록이 연달아 닫히면(`}` 두 줄 연속) 두 번째부터 증발한다. 실측 사례:

            L155 `}`  (function 닫기)  → cnt 1, 스킵 모드 진입
            L154 `}`  (for 닫기)       → ★ 무시됨 (2 가 됐어야 함)
            L146 `for (...) {`         → cnt 0  (for 의 `{` 가 function 의 `}` 를 상쇄)
            L137 `function ... {`      → "여기가 나를 감싸는 블록" 이라고 오판

          그 결과 컨트랙트 바디의 `uint256[47] private __gap;` 이 함수 안 문장으로 분류되고,
          더 크게는 `assembly { ... }` 안의 Yul 라인이 ctx=None 이 되어 **Solidity 파서**로
          들어갔다(PRBMath* 두 파일에서 `[ANTLR]` 157건).

        ⚠ 문자열 리터럴 안의 중괄호는 여전히 오탐이다 — 기존 `code.count('{')` 도 같으므로
          회귀는 아니지만 별건으로 남아 있다.
        """
        depth = 0
        start = line_number if inclusive else line_number - 1
        for line in range(start, 0, -1):
            text = self.sa.full_code_lines.get(line, "")
            for ch in reversed(text):
                if ch == '}':
                    depth += 1
                elif ch == '{':
                    if depth == 0:
                        return line
                    depth -= 1
        return None

    def shadowing_info(self, fcfg, var_name: str, line_number: int) -> tuple[bool, bool]:
        """(Phase 3b, D8) `line_number` 의 지역변수 선언 `var_name` 이 이미 보이는 이름을 가리는가.

        보이는 이름 = 함수 진입 환경(`related_variables`: 파라미터·named return·상태변수·전역)
                    + 이 줄을 감싸는 블록들에서 **앞서** 선언된 지역변수(for-init 은 for 블록 소속).
        반환 (shadows, unsupported): 가리는 선언이 블록 종료 노드가 없는 블록(bare `{`, `unchecked`, `do`,
        `try`/`catch`) 안이면 해석기가 바깥 binding 을 되돌릴 지점이 없으므로 unsupported.
        """
        chain = []
        ln = self._enclosing_open_brace_line(line_number, inclusive=False)
        while ln is not None:
            chain.append(ln)
            text = self.sa.full_code_lines.get(ln, "").strip()
            if text.startswith("}"):
                text = text[1:].strip()
            if self.determine_top_level_context(text) in self._ASM_BOUNDARY:
                break
            ln = self._enclosing_open_brace_line(ln, inclusive=False)
        chain_set = set(chain)
        shadows = var_name in (getattr(fcfg, "related_variables", {}) or {})
        if not shadows:
            for n in fcfg.graph.nodes:
                for st in n.statements:
                    if (st.statement_type == "variableDeclaration" and st.var_name == var_name
                            and st.src_line is not None and st.src_line < line_number
                            and self._enclosing_open_brace_line(st.src_line, inclusive=True) in chain_set):
                        shadows = True
                        break
                if shadows:
                    break
        unsupported = False
        if shadows and chain:
            inner = self.sa.full_code_lines.get(chain[0], "").strip().lstrip("}").strip()
            is_fn = self.determine_top_level_context(inner) in self._ASM_BOUNDARY
            unsupported = (not is_fn) and (inner == "{" or inner.startswith(("unchecked", "do", "try", "catch")))
        return shadows, unsupported

    @staticmethod
    def _reject_param_shadowing(fcfg, name: str) -> None:
        """(2026-10-25) 나중에 선언된 상태변수를 기존 함수에 넣을 때 — 파라미터·named return 과 이름이 같으면 명시적 미지원."""
        if name in (getattr(fcfg, "parameters", None) or []):
            raise UnsupportedShadowing(fcfg.function_name, name, "parameter")
        if any(getattr(r, "identifier", None) == name for r in (getattr(fcfg, "return_vars", None) or [])):
            raise UnsupportedShadowing(fcfg.function_name, name, "named return")

    def _mark_shadowing(self, fcfg, stmt) -> None:
        """선언 Statement 에 shadowing 표시(해석기가 바깥 binding 을 보관·복원)."""
        stmt.shadows, stmt.shadow_unsupported = self.shadowing_info(fcfg, stmt.var_name, stmt.src_line)

    def is_inside_unchecked(self, line_number: int) -> bool:
        """`line_number` 가 (중첩 포함) `unchecked { ... }` 블록 안인가. (Phase 2)
        함수·modifier 등 경계(`_ASM_BOUNDARY`)에 닿으면 멈춘다."""
        line = self._enclosing_open_brace_line(line_number, inclusive=False)
        while line is not None:
            text = self.sa.full_code_lines.get(line, "").strip()
            if text.startswith("}"):                      # `} else {` 류 — 여는 쪽 키워드만 본다
                text = text[1:].strip()
            if text.startswith("unchecked"):
                return True
            if self.determine_top_level_context(text) in self._ASM_BOUNDARY:
                return False
            line = self._enclosing_open_brace_line(line, inclusive=False)
        return False

    def find_parent_context(self, line_number):
        # 위로 거슬러 올라가면서 `{`와 `}`의 짝을 찾기
        line = self._enclosing_open_brace_line(line_number, inclusive=False)
        if line is None:
            return "unknown"
        return self.determine_top_level_context(self.sa.full_code_lines.get(line, ""))

    # `is_inside_assembly` 가 위로 올라가다 멈춰야 하는 경계
    # (이 밖은 assembly 일 수 없다)
    _ASM_BOUNDARY = frozenset({
        "functionDefinition", "constructor", "fallback", "receive", "modifier",
        "contract", "abstract contract", "interface", "library",
        "struct", "enum", "event",
    })

    def is_inside_assembly(self, line_number: int) -> bool:
        """`line_number` 가 `assembly { ... }` 블록 **안**인가 — **중첩 포함** (F40).

        ★ `find_parent_context` 는 **직근 부모**의 종류만 돌려준다. 그래서 중첩 Yul
          블록 안의 줄이 ctx=assembly 를 못 받았다:
          ```solidity
          assembly {
              if iszero(callStatus) {
                  revert(0, returnDataSize)      ← 부모가 "if" → assembly 로 안 잡힘
              }
              switch returnDataSize
              case 32 {
                  success := iszero(iszero(mload(0)))   ← 부모가 "case"
              }
          }
          ```
          그 줄들이 **Solidity 파서로** 갔다 — 증거: `revert(0, returnDataSize)` 의
          ANTLR 기대 토큰 집합이 `interactiveSourceUnit`(= 컨트랙트 레벨)이었다.
          `[ANTLR]` 12건 중 **8건**이 이것이다(SafeTransferLib 6 + PRBMath* 2).

          그래서 직근 부모 하나만 보지 않고 **괄호 체인을 위로 걸어올라간다.**
          함수/컨트랙트/struct 등 경계를 만나면 중단한다.
        """
        line = line_number
        for _ in range(64):                 # 중첩 깊이 상한 — 무한 루프 방어
            line = self._enclosing_open_brace_line(line, inclusive=False)
            if line is None:
                return False
            ctx = self.determine_top_level_context(self.sa.full_code_lines.get(line, ""))
            if ctx == "assembly":
                return True
            if ctx in self._ASM_BOUNDARY:
                return False
        return False

    def in_nested_yul_block(self, line_number: int) -> bool:
        """(2026-10-23, Yul P0-a) `line_number` 가 assembly 안의 **중첩 Yul 블록**(if/switch case/for/function 본문) 안인가.
        그 블록의 머리 줄에 미지원 표식이 놓이므로, 본문 줄은 CFG 에 만들지 않는다 — 만들면 조건 없이 실행되는 문장
        (특히 `if c { revert(0,0) }` 의 revert 가 무조건 revert)이 되어 뒤 코드를 죽은 코드로 만든다."""
        line = self._enclosing_open_brace_line(line_number, inclusive=False)
        if line is None:
            return False
        return self.determine_top_level_context(self.sa.full_code_lines.get(line, "")) != "assembly" \
            and self.is_inside_assembly(line_number)

    def find_contract_context(self, line_number):
        # 위로 거슬러 올라가면서 해당 라인이 속한 컨트랙트를 찾습니다.
        close_brace_count = 0

        for line in range(line_number, 0, -1):
            brace_info = self.sa.line_info.get(line, {'open': 0, 'close': 0, 'cfg_nodes': []})
            open_braces = brace_info['open']
            close_braces = brace_info['close']

            # '}' 카운팅: 닫힌 괄호를 먼저 센다
            if close_brace_count > 0:
                close_brace_count -= open_braces
                if close_brace_count <= 0:
                    close_brace_count = 0
            else:
                # '{' 발견: 이 라인이 컨트랙트 선언인지 확인
                if open_braces > 0:
                    code_line = self.sa.full_code_lines.get(line, '').strip()
                    context_type = self.determine_top_level_context(code_line)
                    if context_type in ["contract", "library", "interface", "abstract contract"]:
                        # contract 이름 추출
                        parts = code_line.split()
                        # "abstract contract Name" or "contract Name" 형식
                        if "contract" in parts:
                            idx = parts.index("contract")
                            if idx + 1 < len(parts):
                                result = parts[idx + 1].split('{')[0].strip()
                                return result
                        elif "library" in parts:
                            idx = parts.index("library")
                            if idx + 1 < len(parts):
                                result = parts[idx + 1].split('{')[0].strip()
                                return result
                        elif "interface" in parts:
                            idx = parts.index("interface")
                            if idx + 1 < len(parts):
                                result = parts[idx + 1].split('{')[0].strip()
                                return result
                # 닫힌 괄호 누적
                close_brace_count += close_braces

        return None

    @staticmethod
    def _extract_param_types(full_sig: str) -> list[str] | None:
        """함수 시그니처 문자열에서 파라미터 타입 목록 추출.
        예: 'function sub(uint256 a, uint256 b, string memory errorMessage) ...'
            → ['uint256', 'uint256', 'string']
        """
        paren_start = full_sig.find('(')
        if paren_start < 0:
            return None
        # 첫 번째 '(' ~ 매칭되는 ')' 범위 추출
        depth = 0
        paren_end = -1
        for i in range(paren_start, len(full_sig)):
            if full_sig[i] == '(':
                depth += 1
            elif full_sig[i] == ')':
                depth -= 1
                if depth == 0:
                    paren_end = i
                    break
        if paren_end < 0:
            return None
        inner = full_sig[paren_start + 1:paren_end].strip()
        if not inner:
            return []
        param_types = []
        for param in inner.split(','):
            tokens = param.strip().split()
            if tokens:
                # 첫 토큰이 타입 (memory/storage/calldata 제외)
                param_types.append(tokens[0])
        return param_types

    _FUNC_HEAD = re.compile(r"(?:function\b|constructor\s*\(|modifier\b|fallback\s*\(|receive\s*\()")
    _NON_FUNC_HEAD = re.compile(r"(?:abstract\s+)?(?:contract|library|interface)\b|struct\b|enum\b")

    def _function_registered_at(self, line: int):
        """(2026-10-23) 블록 시작 줄 `line` 에 **등록된** 함수(FunctionCFG) — 그 헤더 레코드가 ENTRY 노드를 줄에 등록한다
        (`process_function_definition` 등). 여러 줄 헤더면 텍스트가 레코드 끝 줄에 쓰이고 ENTRY 는 시작 줄에 있으므로
        위쪽의 빈 자리 줄까지 본다."""
        ccf = self.contract_cfgs.get(self.find_contract_context(line))
        if ccf is None:
            return None
        fcfgs = [f for _, f in ccf.iter_all_functions()]
        if getattr(ccf, "constructor", None) is not None:
            fcfgs.append(ccf.constructor)
        by_entry = {id(f.get_entry_node()): f for f in fcfgs}
        ln = line
        while ln >= 1:
            for n in self.sa.line_info.get(ln, {}).get("cfg_nodes", []) or []:
                f = by_entry.get(id(n))
                if f is not None:
                    return f
            ln -= 1
            if ln < 1 or self.sa.full_code_lines.get(ln, "").strip():
                break
        return None

    def find_function_context(self, line_number):
        """해당 줄이 속한 함수의 (이름, 파라미터 타입 목록).

        (2026-10-23) 감싸는 블록을 한 단계씩 올라가며, 블록 시작 줄에 **등록된 함수**(ENTRY 노드)가 있으면 그 함수의
        등록 키(이름, `parameter_types`)를 돌려준다 — `get_function_cfg` 가 같은 키로 찾는다.
        예전엔 위쪽 **모든 줄**을 텍스트로 훑어 첫 키워드를 함수 헤더로 삼았다:
          · `received = 1;` 이 `startswith("receive")` 로 receive 함수가 됐다(w61 YearnYield)
          · 헤더 텍스트의 타입(`bytes32[]`, `IERC20`)과 등록 시그니처(`array`, `address`)가 달라 오버로드를 못 찾았다
            (w110 StakedCitadel, w51 SwapUtils, w52 VaderRouter)
        아직 등록 전인 헤더 줄 자체(헤더 레코드를 처리하는 중)만 헤더 텍스트에서 이름·타입을 읽는다."""
        open_brace_line = self._enclosing_open_brace_line(line_number, inclusive=True)
        while open_brace_line is not None:
            head = self.sa.full_code_lines.get(open_brace_line, "").strip().lstrip("}").strip()
            if self._NON_FUNC_HEAD.match(head):
                return None, None
            f = self._function_registered_at(open_brace_line)
            if f is not None:
                if f.function_type == "constructor":
                    return "constructor", list(f.parameter_types)
                if f.function_type == "modifier":
                    return f.function_name, None
                return f.function_name, list(f.parameter_types)
            if self._FUNC_HEAD.match(head):
                return self._function_context_from_header(open_brace_line)
            open_brace_line = self._enclosing_open_brace_line(open_brace_line, inclusive=False)
        return None, None

    def _function_context_from_header(self, open_brace_line):
        """아직 등록되지 않은 함수 헤더 줄(헤더 레코드 처리 중)의 (이름, 파라미터 타입)."""
        for line in range(open_brace_line, 0, -1):
            code_line = self.sa.full_code_lines.get(line, "").strip()
            if not code_line:
                continue

            # function, constructor, modifier 키워드를 찾으면 함수 이름 + 파라미터 추출
            if code_line.startswith("function "):
                parts = code_line.split()
                if len(parts) >= 2:
                    function_name = parts[1].split('(')[0]
                    # 파라미터 타입 추출 — multi-line 대응
                    full_sig = code_line
                    if '(' in full_sig and ')' not in full_sig:
                        for next_ln in range(line + 1, open_brace_line + 1):
                            full_sig += " " + self.sa.full_code_lines.get(next_ln, "").strip()
                            if ')' in full_sig:
                                break
                    param_types = self._extract_param_types(full_sig)
                    # type alias resolve (UFixed18 → uint256 등)
                    if param_types:
                        param_types = [self.sa.type_aliases.get(t, t) for t in param_types]
                    return function_name, param_types
            elif code_line.startswith("constructor"):
                # constructor 파라미터도 추출
                full_sig = code_line
                if '(' in full_sig and ')' not in full_sig:
                    for next_ln in range(line + 1, open_brace_line + 1):
                        full_sig += " " + self.sa.full_code_lines.get(next_ln, "").strip()
                        if ')' in full_sig:
                            break
                param_types = self._extract_param_types(full_sig)
                if param_types:
                    param_types = [self.sa.type_aliases.get(t, t) for t in param_types]
                return "constructor", param_types
            elif code_line.startswith("modifier "):
                parts = code_line.split()
                if len(parts) >= 2:
                    modifier_name = parts[1].split('(')[0]
                    return modifier_name, None
            elif code_line.startswith("fallback"):
                return "fallback", None
            elif code_line.startswith("receive"):
                return "receive", None

            # contract/struct/interface 등을 만나면 함수가 아니므로 중단
            if any(code_line.startswith(kw) for kw in ["contract ", "library ", "interface ", "struct ", "enum "]):
                break

        return None, None

    def find_struct_context(self, line_number):
        # 위로 거슬러 올라가면서 해당 라인이 속한 함수를 찾습니다.
        for line in range(line_number, 0, -1):
            brace_info = self.sa.line_info.get(line, {'open': 0, 'close': 0, 'cfg_nodes': []})
            cfg_nodes = brace_info.get('cfg_nodes', [])
            if brace_info['open'] > 0 and cfg_nodes:
                context_type = self.determine_top_level_context(self.sa.full_code_lines[line])
                if context_type == "struct":
                    # (2026-10-23) 정규식으로 — 공백 분리는 `struct DebtDetails{` 의 이름을 `DebtDetails{` 로 만들었다(w5 Router)
                    m = re.match(r"\s*\}?\s*struct\s+([A-Za-z_]\w*)", self.sa.full_code_lines[line])
                    if m is None:
                        raise ValueError(f"line {line}: struct header without a name: {self.sa.full_code_lines[line]!r}")
                    return m.group(1)

    def determine_top_level_context(self, code_line):
        try:
            # 코드 라인의 내용에 따라 최상위 컨텍스트를 결정
            stripped_code = code_line.strip()
            # 저장소의 `} else {` / `} catch` / `} while` 은 여는 쪽 키워드로 분류한다 (Phase 4b)
            if stripped_code.startswith("}"):
                stripped_code = stripped_code[1:].lstrip()

            if stripped_code.startswith("abstract contract"):
                return "abstract contract"
            elif stripped_code.startswith("contract"):
                return "contract"
            elif stripped_code.startswith("interface"):
                return "interface"
            elif stripped_code.startswith("library"):
                return "library"
            elif stripped_code.startswith("function"):
                return "functionDefinition"
            elif stripped_code.startswith("constructor"):
                return "constructor"
            elif stripped_code.startswith("fallback"):
                return "fallback"
            elif stripped_code.startswith("receive"):
                return "receive"
            elif stripped_code.startswith("modifier"):
                return "modifier"
            elif stripped_code.startswith("struct"):
                return "struct"
            elif stripped_code.startswith("enum"):
                return "enum"
            elif stripped_code.startswith("event"):
                return "event"
            elif stripped_code.startswith("if"):
                return "if"
            elif stripped_code.startswith("else if"):
                return "else_if"
            elif stripped_code.startswith("else"):
                return "else"
            elif stripped_code.startswith("for"):
                return "for"
            elif stripped_code.startswith("while"):
                return "while"
            elif stripped_code.startswith("do"):
                return "do_while"
            elif stripped_code.startswith("try"):
                return "try"
            elif stripped_code.startswith("catch"):
                return "catch"
            elif stripped_code.startswith("assembly"):
                return "assembly"
            elif stripped_code.startswith("unchecked"):
                return "unchecked"
            elif stripped_code.startswith("return") :
                return "return"
            else:
                # User-defined type 변수 선언 또는 일반 statement
                # (예: LockedStake memory x = ..., mapping assignment 등)
                return "simpleStatement"

        except ValueError as e:
            print(f"Error: {e}")
            return "unknown"

    def get_current_context_type(self):
        return self.current_context_type

    """
    cfg part    
    """

    # ContractAnalyzer.py  (일부)

    def make_contract_cfg(self, contract_name: str, parent_contracts: list = None):
        """
        contract-level CFG를 처음 만들 때 한 번 호출.
        address 계열 글로벌은 UnsignedIntegerInterval(160bit) 로,
        uint  계열은 [0,0] 256-bit Interval 로 초기화한다.
        """
        self.current_target_contract = contract_name
        cfg = StaticCFGFactory.make_contract_cfg(self, contract_name)
        if parent_contracts:
            cfg.parent_contracts = parent_contracts
            for pname in parent_contracts:
                cfg.parent_cfgs[pname] = self._parent_cfg(contract_name, pname)
                cfg.inherit_definitions(cfg.parent_cfgs[pname])     # (2026-10-23, A-2)
            self._inherit_using_libraries(cfg)

        if self.current_start_line and self.current_start_line in self.sa.line_info:
            self.sa.line_info[self.current_start_line]['cfg_nodes'] = [cfg]

    def make_abstract_contract_cfg(self, contract_name: str, parent_contracts: list = None):
        """
        abstract contract-level CFG를 처음 만들 때 한 번 호출.
        abstract contract는 직접 배포할 수 없지만 상속용으로 등록.
        """
        self.current_target_contract = contract_name
        cfg = StaticCFGFactory.make_contract_cfg(self, contract_name)
        cfg.is_abstract = True
        if parent_contracts:
            cfg.parent_contracts = parent_contracts
            for pname in parent_contracts:
                cfg.parent_cfgs[pname] = self._parent_cfg(contract_name, pname)
                cfg.inherit_definitions(cfg.parent_cfgs[pname])     # (2026-10-23, A-2)
            self._inherit_using_libraries(cfg)

        if self.current_start_line and self.current_start_line in self.sa.line_info:
            self.sa.line_info[self.current_start_line]['cfg_nodes'] = [cfg]

    def _constant_var(self, cfg, name: str, seen=None):
        """cfg(컨트랙트/라이브러리)와 그 부모 체인에서 `constant` 상태변수 `name`."""
        if cfg is None:
            return None
        seen = seen if seen is not None else set()
        if id(cfg) in seen:
            return None
        seen.add(id(cfg))
        svn = getattr(cfg, "state_variable_node", None)
        v = svn.variables.get(name) if svn is not None else None
        if v is not None:
            return v
        for p in (getattr(cfg, "parent_cfgs", None) or {}).values():
            v = self._constant_var(p, name, seen)
            if v is not None:
                return v
        return None

    def compile_time_constant(self, expr) -> int:
        """(2026-10-23) 컴파일 타임 정수 상수식의 값 — 정적 배열 길이용. 리터럴, `constant` 상태변수(현재 컨트랙트와
        부모 체인), `L.CONST`(다른 컨트랙트·라이브러리), + − * / % ** << >>, 괄호. 상수는 선언 때 단일값으로 평가돼 있다.
        상수가 아니거나 값이 정해지지 않으면 오류(길이를 추측하지 않는다). 못 찾은 이름 = UnresolvedName."""
        from Utils.Errors import UnresolvedName
        ctx = getattr(expr, "context", None)
        if ctx == "LiteralExpContext":
            return int(str(expr.literal).replace("_", ""), 0)
        if ctx in ("ParenthesizedExpContext", "ParenExpContext", "TupleExpressionContext") and getattr(expr, "expression", None) is not None:
            return self.compile_time_constant(expr.expression)
        if ctx in ("IdentifierExpContext", "MemberAccessContext"):
            if ctx == "IdentifierExpContext":
                name, owner = expr.identifier, self.contract_cfgs.get(self.current_target_contract)
                v = self._constant_var(owner, name)
            else:
                base = getattr(expr.base, "identifier", None)
                name, owner = expr.member, (self.contract_cfgs.get(base) or self.library_cfgs.get(base))
                if owner is None:
                    raise UnresolvedName(base, f"constant '{base}.{expr.member}': '{base}' 의 선언을 찾지 못함")
                v = self._constant_var(owner, name)
            if v is None:
                raise UnresolvedName(name, f"compile-time constant '{name}' 의 선언을 찾지 못함")
            val = getattr(v, "value", None)
            if not getattr(v, "isConstant", False) or val is None or getattr(val, "min_value", None) is None \
                    or val.min_value != val.max_value:
                raise ValueError(f"'{name}' 은 값이 정해진 컴파일 타임 상수가 아니다 (배열 길이)")
            return int(val.min_value)
        op = getattr(expr, "operator", None)
        if getattr(expr, "left", None) is not None and getattr(expr, "right", None) is not None and op:
            a, b = self.compile_time_constant(expr.left), self.compile_time_constant(expr.right)
            ops = {"+": lambda: a + b, "-": lambda: a - b, "*": lambda: a * b, "/": lambda: a // b,
                   "%": lambda: a % b, "**": lambda: a ** b, "<<": lambda: a << b, ">>": lambda: a >> b}
            if op not in ops:
                raise ValueError(f"compile-time constant: unsupported operator '{op}'")
            return ops[op]()
        raise ValueError(f"compile-time constant: unsupported expression context '{ctx}'")

    def _parent_cfg(self, child: str, pname: str):
        """(2026-10-23, 레지스트리) 부모 선언 CFG. 예전엔 없으면 **조용히 건너뛰어** 부모의 상태변수·함수·타입이 빠진 채
        분석이 진행됐다. Solidity 는 부모 정의가 자식보다 먼저 보여야 하므로(같은 파일이면 앞, 아니면 import) 없으면 오류다."""
        p = self.contract_cfgs.get(pname)
        if p is None:
            from Utils.Errors import UnresolvedName
            raise UnresolvedName(pname, f"'{child} is {pname}': 부모 '{pname}' 의 선언을 찾지 못함")
        return p

    def _inherit_using_libraries(self, cfg):
        """부모 contract의 using_libraries / using_all_libraries를 자식 cfg에 상속"""
        for parent_cfg in cfg.parent_cfgs.values():
            for target_type, libs in parent_cfg.using_libraries.items():
                for lib in (libs if isinstance(libs, list) else [libs]):
                    cfg.add_using_library(lib, target_type)
            for lib in parent_cfg.using_all_libraries:
                cfg.add_using_library(lib, None)

    def make_library_cfg(self, library_name: str):
        """
        library-level CFG를 처음 만들 때 한 번 호출.
        라이브러리는 state variable이 없고 함수만 포함한다.
        """
        from Utils.CFG import LibraryCFG

        self.current_target_contract = library_name  # 라이브러리도 contract로 처리
        library_cfg = LibraryCFG(library_name)
        library_cfg.globals = StaticCFGFactory._create_global_variables(self)

        # 라이브러리 CFG를 저장
        self.library_cfgs[library_name] = library_cfg
        self.contract_cfgs[library_name] = library_cfg  # 호환성을 위해 contract_cfgs에도 저장

        if self.current_start_line and self.current_start_line in self.sa.line_info:
            self.sa.line_info[self.current_start_line]['cfg_nodes'] = [library_cfg]

    def make_interface_cfg(self, interface_name: str, parent_interfaces: list = None):
        """
        Interface 선언 처리. ContractCFG를 생성하고 is_interface=True로 설정.
        contract_cfgs에 등록하여 brace tracking, context 추적이 정상 동작하게 한다.
        Interface 함수는 body 없이 FunctionCFG(entry→exit)로 등록.
        """
        self.current_target_contract = interface_name
        self.interface_names.add(interface_name)

        cfg = ContractCFG(interface_name)
        cfg.is_interface = True
        self.contract_cfgs[interface_name] = cfg

        if parent_interfaces:
            cfg.parent_contracts = parent_interfaces
            for p in parent_interfaces:
                cfg.parent_cfgs[p] = self._parent_cfg(interface_name, p)
                cfg.inherit_definitions(cfg.parent_cfgs[p])         # (2026-10-23, A-2)

        if self.current_start_line and self.current_start_line in self.sa.line_info:
            self.sa.line_info[self.current_start_line]['cfg_nodes'] = [cfg]


    # for interactiveEnumDefinition in Solidity.g4
    def process_enum_definition(self, enum_name):
        contract_cfg = self.contract_cfgs.get(self.current_target_contract)

        if not contract_cfg:
            raise ValueError(f"Unable to find contract CFG for {self.current_target_contract}")

        # 새로운 EnumDefinition 객체 생성
        enum_def = EnumDefinition(enum_name)
        contract_cfg.define_enum(enum_name, enum_def)

        # brace_count 업데이트
        self.sa.line_info[self.current_start_line]['cfg_nodes'] = [enum_def]

    def process_enum_item(self, items):
        # 현재 타겟 컨트랙트의 CFG 가져오기
        contract_cfg = self.contract_cfgs.get(self.current_target_contract)

        if not contract_cfg:
            raise ValueError(f"Unable to find contract CFG for {self.current_target_contract}")

        # brace_count에서 가장 최근의 enum 정의를 찾습니다.
        enum_def = None
        for line in reversed(range(self.current_start_line + 1)):
            context = self.sa.line_info.get(line)
            if context:
                cfg_nodes = context.get('cfg_nodes', [])
                if cfg_nodes and isinstance(cfg_nodes[0], EnumDefinition):
                    enum_def = cfg_nodes[0]
                    break

        if enum_def is not None:
            # EnumDefinition에 아이템 추가
            for item in items:
                enum_def.add_member(item)
        else:
            raise ValueError(f"Unable to find EnumDefinition context for line {self.current_start_line}")

    def process_using_directive(self, library_name: str, target_type: str | None):
        """
        using LibraryName for TypeName; 또는 using LibraryName for *; 처리
        - target_type이 None이면 모든 타입에 적용 (using_all_libraries)
        - target_type이 있으면 해당 타입에만 적용 (using_libraries)
        """
        import pickle
        import os

        # ContractCFG 또는 LibraryCFG에서 using directive 등록
        contract_cfg = self.contract_cfgs.get(self.current_target_contract)
        if not contract_cfg:
            # library 내부의 using directive인지 확인
            contract_cfg = self.library_cfgs.get(self.current_target_contract)
        if not contract_cfg:
            return  # 컨트랙트/라이브러리 밖의 using directive는 무시

        # 라이브러리 CFG — 로드된 의존성/앞서 분석된 라이브러리(library_cfgs)만 본다.
        # (Phase 4b) 예전엔 없으면 `Dependencies/objectfile` · `Libraries/objectfile` 하드코딩 경로의 pkl 을
        #   `except: pass` 로 읽어 들였고(프로젝트 deps 와 다른 판이 섞임), 그래도 없으면 using 지시자를 조용히 버렸다.
        library_cfg = self.library_cfgs.get(library_name)
        if library_cfg is None:
            from Utils.Errors import UnresolvedName
            raise UnresolvedName(library_name, f"line {self.current_start_line}: using {library_name} — library '{library_name}' "
                                               f"의 선언을 찾지 못함 (의존성 누락 또는 선언 순서)")

        # LibraryCFG를 using_libraries/using_all_libraries에 등록
        # 자식 contract가 명시적으로 선언한 using은 부모로부터 상속된 것보다 우선되어야 하므로
        # 리스트 앞쪽(우선순위 높음)에 삽입
        contract_cfg.add_using_library(library_cfg, target_type, prepend=True)

    def resolve_library_struct(self, library_name: str, member_name: str):
        """
        Library.Struct qualified name을 resolve.
        using_libraries에 등록된 LibraryCFG들과 library_cfgs에서 검색.
        """
        # 1) 현재 contract/library의 using_libraries에서 검색
        ccfg = self.contract_cfgs.get(self.current_target_contract) or \
               self.library_cfgs.get(self.current_target_contract)
        if ccfg:
            all_libs = []
            for libs in ccfg.using_libraries.values():
                all_libs.extend(libs if isinstance(libs, list) else [libs])
            all_libs.extend(ccfg.using_all_libraries)
            for lib in all_libs:
                if getattr(lib, 'library_name', None) == library_name:
                    if member_name in lib.structDefs:
                        return lib.structDefs[member_name]
                    if member_name in lib.enumDefs:
                        return lib.enumDefs[member_name]

        # 2) library_cfgs에서 직접 검색 (현재 분석 중인 library)
        lib = self.library_cfgs.get(library_name)
        if lib:
            if member_name in lib.structDefs:
                return lib.structDefs[member_name]

        # 3) 로드된 interface / contract (`I.S`, `C.S` 형태의 qualified struct·enum)
        # (Phase 4b) 예전엔 하드코딩 경로의 pkl 을 `except: pass` 로 읽었다 — `process_using_directive` 주석 참고.
        cfg = self.contract_cfgs.get(library_name)
        if cfg is not None:
            if member_name in cfg.structDefs:
                return cfg.structDefs[member_name]
            if member_name in (getattr(cfg, 'enumDefs', {}) or {}):
                return cfg.enumDefs[member_name]
        return None

    # for interactiveStructDefinition in Solidity.g4
    def process_struct_definition(self, struct_name):
        contract_cfg = self.contract_cfgs[self.current_target_contract]

        if not contract_cfg:
            raise ValueError(f"Unable to find contract CFG for {self.current_target_contract}")

        struct_def = StructDefinition(struct_name=struct_name)

        contract_cfg.define_struct(struct_def)

        # 10. contract_cfg를 contract_cfgs에 반영
        self.contract_cfgs[self.current_target_contract] = contract_cfg

        # brace_count 업데이트
        self.sa.line_info[self.current_start_line]['cfg_nodes'] = [contract_cfg.structDefs]

    def process_struct_member(self, var_name, type_obj):
        # 1. 현재 타겟 컨트랙트의 CFG를 가져옴
        contract_cfg = self.contract_cfgs[self.current_target_contract]
        if not contract_cfg:
            raise ValueError(f"Unable to find contract CFG for {self.current_target_contract}")

        # 2. 현재 타겟 구조체를 확인하고 멤버 추가
        if not self.current_target_struct:
            raise ValueError("No target struct to add members to.")

        contract_cfg.add_struct_member(self.current_target_struct, var_name, type_obj)

        # 10. contract_cfg를 contract_cfgs에 반영
        self.contract_cfgs[self.current_target_contract] = contract_cfg

    @staticmethod
    def _retype_literal_to_declared(init_expr, type_obj) -> None:
        """선언 타입을 초기화식의 **리터럴**에 실어준다 (F39).

        ★ Solidity 의 숫자 리터럴은 **독립적인 타입이 없고 문맥(= 선언 타입)이 정한다.**
          그런데 `visitLiteralExp` 는 리터럴 단독 판단으로 **항상** `expr_type='uint'`,
          `type_length=256` 을 찍는다(그 자체는 옳다 — Solidity 숫자 리터럴은 부호가 없고
          음수는 단항 `-` 로 표현된다). 문제는 **선언 문맥이 그걸 다시 타이핑해주지
          않는다**는 것이다. 그래서:

              int256 internal constant MIN_SD59x18 = -578...968;
              → UnsignedIntegerInterval([+578...968])     ← 부호 소실 + 도메인도 Unsigned

          `-5` 는 `UnaryPrefixOp('-')` + `LiteralExp(5)` 이므로, 리터럴이 uint 로 평가된 뒤
          단항 마이너스가 **2의 보수로 감긴다**(`[2²⁵⁶-5, 2²⁵⁶-5]`).

          실측 — 세 경로 전부 깨져 있었다(그리고 대입받는 변수로 전파된다):
          | 소스 | 이전 | 참값 |
          |---|---|---|
          | `int256 constant C = -5;`  | `UnsignedIntegerInterval([2²⁵⁶-5])` | `[-5,-5]` |
          | `int256 s = -7;`           | `UnsignedIntegerInterval([2²⁵⁶-7])` | `[-7,-7]` |
          | `int256 l = -11;`(지역)     | `UnsignedIntegerInterval([2²⁵⁶-11])` | `[-11,-11]` |

          ★ **오류가 나지 않는다.** `PRBMathSD59x18.abs` 의 `require(x > MIN_SD59x18)` 이
            `x > +578...968` 이 되고 `int256` 최대값은 `578...967` 이라 **항상 거짓** →
            `abs` 가 통째로 ⊥(도달불가)로 판정된다. F36 은 그 ⊥ 의 **전파**를 막았을
            뿐이고, ⊥ 자체의 원인이 이것이다.

        ⚠ `uint*` 는 건드리지 않는다 — `visitLiteralExp` 의 기본값이 이미 맞다.
        ⚠ `bytesN` 은 F16 이 별도로 주입한다(`evaluate_literal_context` 의 `ety` 분기가
          `'bytes32'` 는 받지만 `'uint256'` 은 못 받기 때문 — 그래서 여기서도 `'int'`/
          `'uint'` 라는 **거친 이름**을 쓰고 비트폭은 `type_length` 로 따로 전달한다).
        ⚠ F14 의 `_literal_retype` 과 겹치지 않는다 — 그쪽은 **이항 연산의 피연산자**를
          상대 타입에 맞추는 것이고(`Evaluation.py:2344`), 여기는 **선언 문맥**이다.
        """
        if init_expr is None or type_obj is None:
            return
        if getattr(type_obj, "typeCategory", None) != "elementary":
            return
        et = getattr(type_obj, "elementaryTypeName", None) or ""
        if not (et.startswith("int") and not et.startswith("interface")):
            return

        # 단항 +/- 래퍼를 벗겨 안쪽 리터럴을 찾는다 (`-5` / `- -5` 도 대응)
        tgt = init_expr
        for _ in range(4):
            if (getattr(tgt, "context", None) == "UnaryPrefixOpContext"
                    and getattr(tgt, "operator", None) in ("-", "+")):
                tgt = getattr(tgt, "expression", None)
                if tgt is None:
                    return
                continue
            break
        if getattr(tgt, "context", None) != "LiteralExpContext":
            return          # 리터럴이 아닌 초기화식(호출·식별자 등)은 건드리지 않는다
        tgt.expr_type = "int"
        tgt.type_length = getattr(type_obj, "intTypeLength", None) or 256

    def process_state_variable(self, variable_obj, init_expr=None):
        # 1. 현재 타겟 컨트랙트의 CFG 가져오기
        contract_cfg = self.contract_cfgs[self.current_target_contract]

        if not contract_cfg:
            raise ValueError(f"Unable to find contract CFG for {self.current_target_contract}")

        if not contract_cfg.state_variable_node:
            contract_cfg.initialize_state_variable_node()

        # ★ (2026-10-20, 사용자 승인) 상태변수의 진입값 = **알 수 없는 호출 직전 상태(pre-state)** 이므로 ⊤.
        #   예전엔 Solidity 기본값 0(또는 initializer 값)이었는데, 그건 "막 배포된 컨트랙트" 라는 특정 시나리오를
        #   고른 것이다. IntentChecker 원고("debug 어노테이션은 otherwise unknown 값을 대체", "제약이 없으면 ⊤")와
        #   일치시킨다. initializer 도 배포 직후 값일 뿐이라 pre-state 로는 쓰지 않는다(init_expr 는 CFG 에 기록만).
        #   constant/immutable 은 process_constant_variable 경로라 영향 없음(값이 바뀔 수 없으므로 유지가 맞다).
        #   동적 배열은 길이를 모르는 배열을 표현할 수단이 없어 예전처럼 빈 배열로 남는다(⚠ 미해결).
        record_expr = init_expr          # CFG 기록용 원래 initializer (진입값 계산에는 쓰지 않는다)
        if init_expr is not None and variable_obj.typeInfo.typeCategory == "elementary" \
                and not isinstance(variable_obj, (ArrayVariable, StructVariable, MappingVariable, EnumVariable)):
            init_expr = None
        if init_expr is None: # 초기화가 없으면
            if isinstance(variable_obj, ArrayVariable) :
                # arrayBaseType이 elementary type인 경우에만 elementaryTypeName 체크
                if variable_obj.typeInfo.arrayBaseType.typeCategory == "elementary":
                    # 고정 길이 numeric/bool 배열 원소 = ⊤, address/string 등은 기존대로 (initialize_default_by_base_type 가 분기)
                    variable_obj.initialize_default_by_base_type()
                # struct, enum 등 다른 타입의 배열은 동적으로 초기화됨 (필요 시)
            elif isinstance(variable_obj, StructVariable) :
                struct_name = variable_obj.typeInfo.structTypeName
                if struct_name in contract_cfg.structDefs:
                    struct_def = contract_cfg.structDefs[struct_name]
                    variable_obj.initialize_struct(struct_def, struct_defs=contract_cfg.structDefs,
                                                   enum_defs=contract_cfg.enumDefs)
                elif self.sa.get_file_level_struct(struct_name) is not None:
                    # file-level struct fallback
                    all_defs = dict(contract_cfg.structDefs)
                    all_defs.update(self.sa.file_level_structs)
                    variable_obj.initialize_struct(self.sa.file_level_structs[struct_name], struct_defs=all_defs,
                                                   enum_defs=contract_cfg.enumDefs)
                else :
                    raise ValueError(f"This struct def {struct_name} is undefined")
            elif isinstance(variable_obj, MappingVariable) :
                # struct/enum value type 지원을 위해 정의 전달
                # 현재 contract + file-level + parent 체인 합산
                all_structs = dict(contract_cfg.structDefs)
                all_enums = dict(contract_cfg.enumDefs)
                if self.sa.file_level_structs:
                    all_structs.update(self.sa.file_level_structs)
                for pcfg in getattr(contract_cfg, 'parent_cfgs', {}).values():
                    all_structs.update(getattr(pcfg, 'structDefs', {}))
                    all_enums.update(getattr(pcfg, 'enumDefs', {}))
                variable_obj.struct_defs = all_structs
                variable_obj.enum_defs = all_enums
            elif isinstance(variable_obj,EnumVariable) :
                pass
            elif variable_obj.typeInfo.typeCategory == "interface" or \
                 (hasattr(variable_obj.typeInfo, 'interfaceName') and variable_obj.typeInfo.interfaceName):
                # interface 타입 state variable → AddressSet.top() + _cast_interface
                ifc_name = variable_obj.typeInfo.interfaceName or variable_obj.typeInfo.elementaryTypeName
                if ifc_name:
                    variable_obj.value = AddressSet.top()
                    variable_obj.value._cast_interface = ifc_name
            elif variable_obj.typeInfo.typeCategory == "elementary":
                et = variable_obj.typeInfo.elementaryTypeName
                # int/uint/bool/address/bytesN = 타입 전체 범위 ⊤ (파라미터·struct 멤버와 같은 top_from_soltype 사용)
                if et.startswith(("int", "uint", "bool")) or et == "address" \
                        or (et.startswith("bytes") and len(et) > 5):
                    variable_obj.value = VariableEnv.top_from_soltype(
                        variable_obj.typeInfo, identifier=variable_obj.identifier).value
                # (string / bytes 등 - 추상화 안 할 타입은 심볼릭 문자열 그대로)
                else:
                    variable_obj.value = f"symbol_{variable_obj.identifier}"
        else : # 초기화 식이 있으면
            if isinstance(variable_obj, ArrayVariable) :
                inlineArrayValues = self.evaluator.evaluate_expression(
                    init_expr,
                    contract_cfg.state_variable_node.variables,
                    None,
                    None)

                for i, value in enumerate(inlineArrayValues):
                    variable_obj.elements[i] = value
            elif isinstance(variable_obj, StructVariable) : # 관련된 경우 없을듯
                pass
            elif isinstance(variable_obj, MappingVariable) : # 관련된 경우 없을 듯
                pass
            elif variable_obj.typeInfo.typeCategory == "elementary" :
                # ★ F39: 선언 타입을 리터럴에 실어준다 (int* 음수 부호 소실 방지)
                self._retype_literal_to_declared(init_expr, variable_obj.typeInfo)
                from Interpreter.Semantics.Evaluation import coerce_to_declared
                variable_obj.value = coerce_to_declared(self.evaluator.evaluate_expression(
                    init_expr,
                    contract_cfg.state_variable_node.variables,
                    None,
                    None), variable_obj.typeInfo, f"initializer of '{variable_obj.identifier}'")   # (Phase 4b)

        self.register_var(variable_obj)

        # 4. 상태 변수를 ContractCFG에 추가
        contract_cfg.add_state_variable(variable_obj, expr=record_expr, line_no=self.current_start_line)

        # 5. ContractCFG에 있는 모든 FunctionCFG에 상태 변수 추가
        #    copy를 저장하여 분석 중 _make_bottom() in-place mutation이 전파되지 않도록 함
        for _, function_cfg in contract_cfg.iter_all_functions():
            self._reject_param_shadowing(function_cfg, variable_obj.identifier)
            function_cfg.add_related_variable(variable_obj.identifier, VariableEnv.copy_single_variable(variable_obj))

        # 6. contract_cfg를 contract_cfgs에 반영
        self.contract_cfgs[self.current_target_contract] = contract_cfg

        # 7. brace_count 업데이트
        self.sa.line_info[self.current_start_line]['cfg_nodes'] = [contract_cfg.state_variable_node]

    # ---------------------------------------------------------------------------
    # ② constant 변수 처리 (CFG·심볼 테이블 반영)
    # ---------------------------------------------------------------------------
    def process_constant_variable(self, variable_obj, init_expr):
        # 1. 컨트랙트 CFG 확보
        contract_cfg = self.contract_cfgs[self.current_target_contract]
        if contract_cfg is None:
            raise ValueError(f"Unable to find contract CFG for {self.current_target_contract}")

        # 2. 반드시 초기화식이 있어야 함 (constant 변수는 항상 초기화 필요)
        if init_expr is None:
            raise ValueError(f"Constant variable '{variable_obj.identifier}' must have an initializer.")

        # 3. constant로 선언 불가능한 타입 검증
        if isinstance(variable_obj, (ArrayVariable, StructVariable, MappingVariable)):
            type_name = type(variable_obj).__name__.replace('Variable', '').lower()
            raise ValueError(
                f"{type_name.capitalize()} variables cannot be declared as constant: '{variable_obj.identifier}'")

        if not contract_cfg.state_variable_node:
            contract_cfg.initialize_state_variable_node()

        # 4. 평가 컨텍스트는 현재까지의 state-variable 노드 변수들
        state_vars = contract_cfg.state_variable_node.variables

        # 5. constant 표현식 평가 (value types와 string만 지원)
        if isinstance(variable_obj, EnumVariable):
            # 열거형도 value type이므로 지원
            value = self.evaluator.evaluate_expression(init_expr, state_vars, None, None)
            if value is None:
                raise ValueError(f"Unable to evaluate constant enum expression for '{variable_obj.identifier}'")
            variable_obj.value = value
        elif variable_obj.typeInfo.typeCategory == "elementary":
            # value types (int, uint, bool, address 등)과 string 지원
            et = variable_obj.typeInfo.elementaryTypeName
            # ★ bytes1..bytes32 (고정 크기 바이트) 가 허용 목록에서 빠져 있었다.
            #   `"bytes"` 는 **동적** bytes 만 매치하므로
            #   `bytes32 internal constant GOVERNANCE = "governance";` 같은 유효한 선언이
            #   전부 거부됐다 (Roles 9 · AddressProviderKeys 9 · Controller 2 ·
            #   ERC1155/165Upgradeable 3 · LockeERC20 1 = 24건).
            #   bytesN 은 Solidity 의 value type 이고 `Domain/BytesSet.py` 와
            #   `evaluate_literal_context` 의 bytesN 분기가 이미 있었다 — 게이트만 막고 있었다.
            #   선언이 거부되면 `register_var` 까지 못 가므로, 이 상수를 읽는 코드가
            #   이후 "may not be declared" 로 연쇄 실패한다.
            _is_fixed_bytes = et.startswith("bytes") and len(et) > 5 and et[5:].isdigit()
            if (et in ["string", "bytes"] or _is_fixed_bytes
                    or et.startswith(("int", "uint", "bool")) or et == "address"):
                # ★ 리터럴에 **선언 타입**을 실어 보낸다 (F14 와 같은 원리).
                #   Solidity 리터럴은 타입이 없고 문맥이 타입을 정하는데,
                #   `visitLiteralExp` 는 자기가 본 형태로만 찍는다:
                #       bytes32 X = "governance"  → expr_type='string'
                #       bytes4  X = 0x01ffc9a7    → expr_type='uint'
                #   그대로 두면 `evaluate_literal_context` 가 bytesN 분기를 못 타고
                #   원문 str 을 그대로 돌려준다(추상값이 아닌 것이 상수에 박힌다).
                #   ⚠ **bytesN 에만** 덮어쓴다 — `uint256` 으로 덮으면
                #     evaluate_literal_context 가 'uint256' 을 모른다
                #     (`Unsupported literal expr_type 'uint256'`). 실측 확인.
                #   ※ 리터럴이 아닌 초기화식(keccak256(...) 등)은 건드리지 않는다.
                #     그쪽은 이미 BytesSet.top(32) 로 올바르게 평가된다.
                if _is_fixed_bytes and getattr(init_expr, "context", None) == "LiteralExpContext":
                    init_expr.expr_type = et
                # ★ F39: int* 는 선언 타입을 리터럴에 실어준다 (음수 부호 소실 방지)
                self._retype_literal_to_declared(init_expr, variable_obj.typeInfo)
                value = self.evaluator.evaluate_expression(init_expr, state_vars, None, None)
                if value is None:
                    raise ValueError(f"Unable to evaluate constant expression for '{variable_obj.identifier}'")
                from Interpreter.Semantics.Evaluation import coerce_to_declared
                variable_obj.value = coerce_to_declared(value, variable_obj.typeInfo,
                                                        f"constant '{variable_obj.identifier}'")   # (Phase 4b)
            else:
                raise ValueError(f"Type '{et}' cannot be declared as constant: '{variable_obj.identifier}'")
        elif variable_obj.typeInfo.typeCategory == "interface":
            # interface 타입 constant (e.g., IUniswapV2Router02 public constant x = IUniswapV2Router02(addr))
            # → address로 취급
            value = self.evaluator.evaluate_expression(init_expr, state_vars, None, None)
            if value is not None:
                variable_obj.value = value
            else:
                from Domain.AddressSet import AddressSet
                variable_obj.value = AddressSet.top()
        else:
            # 기타 지원되지 않는 타입
            raise ValueError(
                f"Type category '{variable_obj.typeInfo.typeCategory}' cannot be declared as constant: '{variable_obj.identifier}'")

        variable_obj.isConstant = True  # constant 플래그 설정

        self.register_var(variable_obj)

        # 3. ContractCFG 에 추가 (state 변수와 동일 API 사용)
        contract_cfg.add_state_variable(variable_obj, expr=init_expr, line_no=self.current_start_line)

        # 4. 이미 생성된 모든 FunctionCFG 에 read-only 변수로 연동
        for _, fn_cfg in contract_cfg.iter_all_functions():
            self._reject_param_shadowing(fn_cfg, variable_obj.identifier)
            fn_cfg.add_related_variable(variable_obj.identifier, VariableEnv.copy_single_variable(variable_obj))

        # 5. 전역 map 업데이트
        self.contract_cfgs[self.current_target_contract] = contract_cfg

        # 6. brace_count 갱신 → IDE/커서 매핑
        self.sa.line_info[self.current_start_line]["cfg_nodes"] = [contract_cfg.state_variable_node]

    def process_modifier_definition(self,
                                    modifier_name: str,
                                    parameters: dict[str, SolType] | None = None) -> None:
        """
        modifier 정의를 분석하여 FunctionCFG 로 등록
        parameters: { param_name : SolType, ... }  또는 None
        """
        contract_cfg = self.contract_cfgs[self.current_target_contract]
        if contract_cfg is None:
            raise ValueError(f"Unable to find contract CFG for {self.current_target_contract}")

        mod_cfg = StaticCFGFactory.make_modifier_cfg(self, contract_cfg, modifier_name, parameters)

        # 3) CFG 저장
        self.sa.line_info[self.current_start_line]['cfg_nodes'] = [mod_cfg.get_entry_node()]
        _mod_exit = mod_cfg.get_exit_node()
        if getattr(_mod_exit, "src_line", None) in (None, -1):   # (K2)
            _mod_exit.src_line = self.current_end_line
        self.sa.line_info[self.current_end_line]['cfg_nodes'] = [_mod_exit]

    # ContractAnalyzer.py  ----------------------------------------------

    def _find_in_ancestors(self, ccf, name: str):
        """상속 체인을 BFS 로 훑어 함수/modifier CFG 를 찾는다.

        ★ 예전엔 `parent_contracts` 를 **1단계만** 돌았다. 실제 체인은 더 깊다:
          `contract Controller is IController, Authorization, Preparable`
            → `contract Authorization is AuthorizationBase`
              → `modifier onlyGovernance` 는 **AuthorizationBase** 에 정의돼 있다.
          1단계만 보면 못 찾고 `Modifier 'onlyGovernance' is not defined.` 로 터지는데,
          modifier 해석 실패는 **함수 정의 자체를 중단**시키므로 그 함수 본문 전체가
          "No active FunctionCFG" 로 연쇄 실패한다(Controller.sol 20건, 등록 함수 0개).

        조상 CFG 는 두 곳에서 얻는다:
          - `ccf.parent_cfgs`  : pkl 에 **객체째** 직렬화돼 있다(`con_Authorization.pkl` 이
                                 `AuthorizationBase` 를 품고 있음) — 이것만으로도 도달 가능.
          - `self.contract_cfgs`: 같은 세션에서 분석/로드된 것.

        ⚠ 탐색 순서는 **선언 순서**다. Solidity 의 C3 선형화는 뒤쪽 부모가 더 파생이라
          (`is A, B` → B 우선) 엄밀히는 역순이어야 하지만, 이 코퍼스엔 조상 간 이름 충돌이
          없어 영향이 없다. 다이아몬드 상속을 제대로 다루려면 별도 작업이 필요하다.
        """
        seen: set[str] = set()
        queue = [ccf]
        while queue:
            cur = queue.pop(0)
            pcfgs = getattr(cur, 'parent_cfgs', {}) or {}
            names = list(getattr(cur, 'parent_contracts', []) or [])
            for pn in pcfgs:
                if pn not in names:
                    names.append(pn)
            for pname in names:
                if not pname or pname in seen:
                    continue
                seen.add(pname)
                pcfg = pcfgs.get(pname) or self.contract_cfgs.get(pname)
                if pcfg is None:
                    continue
                found = pcfg.get_function_cfg(name)
                if found is not None:
                    return found
                queue.append(pcfg)
        return None

    def process_constructor_definition(self, name, params, modifiers):
        ccf = self.contract_cfgs[self.current_target_contract]

        ctor_cfg = StaticCFGFactory.make_constructor_cfg(
            self, name, params, modifiers
        )
        ccf.add_constructor_to_cfg(ctor_cfg)
        self.contract_cfgs[self.current_target_contract] = ccf

        # brace_count - 디폴트 entry 등록
        self.sa.line_info[self.current_start_line]["cfg_nodes"] = [ctor_cfg.get_entry_node()]

        # EXIT 노드를 end line에 등록 (body 문장이 forward 검색 시 찾을 수 있도록)
        if self.current_end_line not in self.sa.line_info:
            self.sa.line_info[self.current_end_line] = {"open": 0, "close": 0, "cfg_nodes": []}
        exit_node = ctor_cfg.get_exit_node()
        if getattr(exit_node, "src_line", None) in (None, -1):   # (K2)
            exit_node.src_line = self.current_end_line
        if exit_node not in self.sa.line_info[self.current_end_line]["cfg_nodes"]:
            self.sa.line_info[self.current_end_line]["cfg_nodes"].append(exit_node)

    # ContractAnalyzer.py  ─ process_function_definition  (address-symb ✚ 최신 Array/Struct 초기화 반영)

    def process_function_definition(
            self,
            function_name: str,
            parameters: list[tuple[SolType, str]],
            modifiers: list,          # [(이름, [인자 Expression])] (Phase 4)
            returns: list[Variables] | None,
            mutability: str | None = None,
            has_body: bool = True,
    ):
        contract_cfg = self.contract_cfgs[self.current_target_contract]
        if contract_cfg is None:
            raise ValueError(f"Contract CFG for {self.current_target_contract} not found.")

        # Interface 함수: body 없이 entry→exit + 시그니처(파라미터 타입·이름)·return_types 만 등록
        #   (2026-10-23) 예전엔 파라미터를 버려 모든 interface 함수가 "인자 0개" 로 보였다 — 오버로드 해소가
        #   선언을 후보로 볼 때 시그니처가 틀렸다(52_H_23 `getReserves(IERC20)`).
        if getattr(contract_cfg, 'is_interface', False):
            fcfg = FunctionCFG(function_type="function", function_name=function_name)
            fcfg.mutability = mutability
            fcfg.has_body = False
            for p_type, p_name in (parameters or []):
                fcfg.parameter_types.append(StaticCFGFactory.param_type_str(p_type))
                fcfg.parameters.append(p_name)
            for r_type, r_name in (returns or []):
                fcfg.return_types.append(r_type)
            contract_cfg.add_function_cfg(function_name, fcfg)
            if self.current_start_line in self.sa.line_info:
                self.sa.line_info[self.current_start_line]["cfg_nodes"] = [fcfg.get_entry_node()]
            return

        fcfg = StaticCFGFactory.make_function_cfg(self, function_name, parameters, modifiers, returns)
        fcfg.mutability = mutability
        fcfg.has_body = has_body
        self._narrow_msg_value_for_nonpayable(fcfg)

        contract_cfg.add_function_cfg(function_name, fcfg)
        self.contract_cfgs[self.current_target_contract] = contract_cfg
        self.sa.line_info[self.current_start_line]["cfg_nodes"] = [fcfg.get_entry_node()]
        # Don't overwrite existing nodes in line_info for end_line, just add EXIT if not present
        if self.current_end_line not in self.sa.line_info:
            self.sa.line_info[self.current_end_line] = {"open": 0, "close": 0, "cfg_nodes": []}
        exit_node = fcfg.get_exit_node()
        # (K2) EXIT 에 **함수를 닫는 `}` 줄**을 준다. 예전엔 src_line 이 -1 이라 프론트의
        #   display_line 도 -1 이 되어 **함수 끝에 breakpoint 를 걸 수 없었다**
        #   (`isBp`/`canBreakpointOnLine` 이 `display_line >= 0` 을 요구한다).
        #   줄 이동은 `_shift_cfg_meta` ①/③ 이 line_info 등록을 근거로 따라온다.
        if getattr(exit_node, "src_line", None) in (None, -1):
            exit_node.src_line = self.current_end_line
        if exit_node not in self.sa.line_info[self.current_end_line]["cfg_nodes"]:
            self.sa.line_info[self.current_end_line]["cfg_nodes"].append(exit_node)

    @staticmethod
    def _narrow_msg_value_for_nonpayable(fcfg):
        """non-payable 함수의 진입 환경에서 msg.value 를 [0,0] 으로 좁힌다 (2026-10-20).
        전역 테이블의 msg.value 는 ⊤ 이지만, payable 이 아닌 함수는 value 가 붙은 호출을 EVM 이 revert 하므로
        정상 실행 경로에서 msg.value == 0 이 보장된다. 전역 객체는 함수들이 공유하므로 **복사본**을 바꾼다."""
        if getattr(fcfg, "mutability", None) == "payable":
            return
        gv = fcfg.related_variables.get("msg.value")
        if gv is None:
            return
        narrowed = VariableEnv.copy_single_variable(gv)
        narrowed.value = UnsignedIntegerInterval(0, 0, 256)
        for env in (fcfg.related_variables, fcfg.entry_env, fcfg.assign_env, fcfg.entry_node.variables):
            if env is not None and "msg.value" in env:
                env["msg.value"] = VariableEnv.copy_single_variable(narrowed)

    def get_full_struct_enum_defs(self, contract_name: str | None = None):
        """
        contract_name(기본값: current_target_contract) 기준으로 contract 자신 +
        file-level + parent 상속 체인의 struct/enum 정의를 모두 합쳐서 반환한다.
        (struct_defs, enum_defs) 튜플.

        여러 곳(Update.py/Evaluation.py/DebugInitializer.py)에서 struct_defs가
        비어있는 MappingVariable/ArrayVariable을 보수하려고 `ccf.structDefs`만
        대입했었는데, 그러면 file-level struct(예: 컨트랙트 밖에 선언된 struct)가
        빠진 채로 이미 완전했던 값을 오히려 덮어써버리는 문제가 있었다 - 이 헬퍼로
        통일해서 그 문제를 근본적으로 막는다.
        """
        contract_name = contract_name or self.current_target_contract
        ccf = self.contract_cfgs.get(contract_name) if contract_name else None
        all_structs = dict(ccf.structDefs) if ccf else {}
        all_enums = dict(ccf.enumDefs) if ccf else {}
        if self.sa.file_level_structs:
            all_structs.update(self.sa.file_level_structs)
        if ccf:
            for pcfg in getattr(ccf, 'parent_cfgs', {}).values():
                all_structs.update(getattr(pcfg, 'structDefs', {}))
                all_enums.update(getattr(pcfg, 'enumDefs', {}))
        return all_structs, all_enums

    def struct_def_of_type(self, t):
        """(2026-10-23) struct 타입(SolType)의 정의. 선언 스코프(`structOwner`)가 있으면 **그 컨트랙트**의 정의 표
        (A-2 로 상속 정의 포함)에서, 없으면 그 이름이 쓰인 스코프 = 실행 중인 컨트랙트 체인 + 파일 수준에서 찾는다.
        소유자가 있는데 정의가 없으면 오류(소유자 표시와 정의 표가 어긋났다)."""
        name = getattr(t, "structTypeName", None)
        if not name:
            return None
        owner = getattr(t, "structOwner", None)
        short = name.split('.')[-1]
        if owner is not None:
            cfg = self.contract_cfgs.get(owner) or self.library_cfgs.get(owner)
            d = (getattr(cfg, "structDefs", None) or {}).get(short) if cfg is not None else None
            if d is None:
                raise ValueError(f"struct '{name}': 선언 컨트랙트 '{owner}' 에 정의가 없다")
            return d
        structs, _ = self.get_full_struct_enum_defs()
        return structs.get(name) or (structs.get(short) if '.' in name else None)

    def _create_variable_object(
            self,
            type_obj: SolType,
            var_name: str,
            ccf
    ) -> Variables | ArrayVariable | StructVariable | MappingVariable | EnumVariable:
        """
        Helper function to create a variable object based on type information.
        """
        v: Variables | ArrayVariable | StructVariable | MappingVariable | EnumVariable

        # 2-A  배열
        if type_obj.typeCategory == "array":
            all_structs, all_enums = self.get_full_struct_enum_defs()
            v = ArrayVariable(
                identifier=var_name,
                base_type=type_obj.arrayBaseType,
                array_length=type_obj.arrayLength,
                is_dynamic=type_obj.isDynamicArray,
                scope="local",
                struct_defs=all_structs,
                enum_defs=all_enums,
            )

        # 2-B  구조체
        elif type_obj.typeCategory == "struct":
            v = StructVariable(
                identifier=var_name,
                struct_type=type_obj.structTypeName,
                scope="local",
            )
            v.typeInfo.structOwner = getattr(type_obj, "structOwner", None)
            # ★ 정의를 붙여 멤버를 채운다.
            #   예전엔 빈 StructVariable 을 그대로 뒀다. 그래서
            #     `Ticks.Tick storage previous = ticks[current.previousTick];`
            #   처럼 초기화식이 **아직 없는 매핑 엔트리**를 가리키면
            #   (기존 엔트리를 받아오는 경우와 달리) 멤버가 비어
            #   `Member 'nextTick' not in struct 'previous'` 로 터졌다
            #   (Ticks.sol 4건 + AddressProviderMeta 2건).
            #   바로 아래 2-D(매핑)·2-A(배열) 는 `get_full_struct_enum_defs()` 로
            #   정의를 넘겨 `_make_value` 가 초기화하는데, struct/enum 분기만 빠져 있었다.
            _structs, _enums = self.get_full_struct_enum_defs()
            _sname = type_obj.structTypeName
            if _sname not in _structs and _sname and '.' in _sname:
                # qualified(`Ticks.Tick`) 로 못 찾으면 bare(`Tick`) 로 한 번 더.
                #   등록 시점에 따라 한쪽 이름만 있을 수 있다(실측: 둘 다 있는 경우도 있음).
                _sname = _sname.split('.')[-1]
            if _sname in _structs:
                v.initialize_struct(_structs[_sname],
                                    struct_defs=_structs, enum_defs=_enums)

        # 2-C  enum
        elif type_obj.typeCategory == "enum":
            v = EnumVariable(identifier=var_name, enum_type=type_obj.enumTypeName, scope="local")
            # ★ 2-B 와 같은 이유로 멤버 테이블을 채운다 (`_make_value` 의 enum 분기와 동일).
            _structs, _enums = self.get_full_struct_enum_defs()
            _ename = type_obj.enumTypeName
            if _ename not in _enums and _ename and '.' in _ename:
                _ename = _ename.split('.')[-1]
            if _ename in _enums:
                _defn = _enums[_ename]
                v.members = {m: i for i, m in enumerate(_defn.members)}
                if _defn.members:
                    v.valueIndex = 0
                    v.value = _defn.members[0]

        # 2-D  매핑
        elif type_obj.typeCategory == "mapping":
            # 정의 수집은 `get_full_struct_enum_defs()` 와 동일한 로직이었다 — 중복 제거.
            all_structs, all_enums = self.get_full_struct_enum_defs()
            v = MappingVariable(
                identifier=var_name,
                key_type=type_obj.mappingKeyType,
                value_type=type_obj.mappingValueType,
                scope="local",
                struct_defs=all_structs,
                enum_defs=all_enums,
            )

        # 2-E  elementary
        else:
            v = Variables(identifier=var_name, scope="local")
            v.typeInfo = type_obj

        return v

    def process_variable_declaration(
            self,
            type_obj: SolType,
            var_name: str,
            init_expr: Expression | None = None
    ):

        ccf = self.contract_cfgs[self.current_target_contract]
        self.current_target_function_cfg = ccf.get_function_cfg(self.current_target_function, param_types=self.current_target_function_param_types)
        if self.current_target_function_cfg is None:
            raise ValueError("variableDeclaration: active FunctionCFG not found")
        fcfg = self.current_target_function_cfg

        cur_blk = self.builder.get_current_block()

        # ★ (리팩터링 Phase 3a, D1) 구성 단계에서는 평가하지 않는다 — 선언문 IR(타입·이름·초기화식)만 저장한다.
        #   지역변수 객체는 디버깅 해석 때 `Engine._interpret_var_decl` 이 이 IR 로 만든다.
        #   (F39) 선언 타입을 리터럴에 실어주는 것은 IR 타입 정보이므로 여기서 한다.
        if init_expr is not None:
            self._retype_literal_to_declared(init_expr, type_obj)

        stmt_blk = self.builder.build_variable_declaration(
            cur_block=cur_blk,
            var_name=var_name,
            type_obj=type_obj,
            init_expr=init_expr,
            line_no=self.current_start_line,
            fcfg=self.current_target_function_cfg,
            line_info=self.sa.line_info,
        )
        self._mark_shadowing(fcfg, stmt_blk.statements[-1])   # (Phase 3b, D8)

        # ────────────────── ④ 저장 & 정리 ────────────────────
        ccf.update_function_cfg(self.current_target_function, self.current_target_function_cfg)
        self.contract_cfgs[self.current_target_contract] = ccf
        self.current_target_function_cfg = None

    def process_variable_declaration_tuple(
            self,
            var_declarations: list[tuple[SolType, str]],  # [(type_obj, var_name), ...] — 빈 슬롯은 None
            init_expr: Expression | None = None
    ):
        """
        튜플 변수 선언 처리
        예: (bool success, bytes memory data) = addr.call(...)

        ★ (Phase 3a, D1) 평가하지 않는다. 선언문들 + 슬롯 위치를 보존한 튜플 대입문(Phase 2)을 IR 로 저장한다.
        """
        ccf = self.contract_cfgs[self.current_target_contract]
        self.current_target_function_cfg = ccf.get_function_cfg(self.current_target_function, param_types=self.current_target_function_param_types)
        if self.current_target_function_cfg is None:
            raise ValueError("variableDeclarationTuple: active FunctionCFG not found")
        fcfg = self.current_target_function_cfg

        cur_blk = self.builder.get_current_block()

        tuple_lhs = None
        if init_expr is not None:
            tuple_lhs = Expression(
                context="TupleExpressionContext",
                elements=[None if d is None else Expression(identifier=d[1], context="IdentifierExpContext")
                          for d in var_declarations])

        stmt_blk = self.builder.build_variable_declaration_tuple(
            cur_block=cur_blk,
            decls=[d for d in var_declarations if d is not None],
            init_expr=init_expr,
            tuple_lhs=tuple_lhs,
            line_no=self.current_start_line,
            fcfg=fcfg,
            line_info=self.sa.line_info,
        )
        for st in stmt_blk.statements:                      # (Phase 3b, D8)
            if st.statement_type == "variableDeclaration":
                self._mark_shadowing(fcfg, st)

        ccf.update_function_cfg(self.current_target_function, fcfg)
        self.contract_cfgs[self.current_target_contract] = ccf
        self.current_target_function_cfg = None

    def _initialize_variable_default(self, v, ccf, var_name):
        """Helper function to initialize variable with default values."""
        # 배열 기본
        if isinstance(v, ArrayVariable):
            bt = v.typeInfo.arrayBaseType
            if isinstance(bt, SolType):
                et = bt.elementaryTypeName
                if et and et.startswith("int"):
                    bits = bt.intTypeLength or 256
                    v.initialize_elements(IntegerInterval.top(bits))
                elif et and et.startswith("uint"):
                    bits = bt.intTypeLength or 256
                    v.initialize_elements(UnsignedIntegerInterval.top(bits))
                elif et == "bool":
                    v.initialize_elements(BoolInterval.top())
                else:
                    v.initialize_not_abstracted_type()

        # 구조체 기본
        elif isinstance(v, StructVariable):
            if v.typeInfo.structTypeName not in ccf.structDefs:
                raise ValueError(f"Undefined struct {v.typeInfo.structTypeName}")
            v.initialize_struct(ccf.structDefs[v.typeInfo.structTypeName], struct_defs=ccf.structDefs,
                               enum_defs=ccf.enumDefs)

        # enum 기본 (첫 멤버)
        elif isinstance(v, EnumVariable):
            enum_def = ccf.enumDefs.get(v.typeInfo.enumTypeName)
            if enum_def:
                v.valueIndex = 0
                v.value = enum_def.members[0]

        # elementary 기본
        elif isinstance(v, Variables):
            et = v.typeInfo.elementaryTypeName
            if et.startswith("int"):
                type_len = v.typeInfo.intTypeLength or 256
                v.value = IntegerInterval.top(type_len)
            elif et.startswith("uint"):
                type_len = v.typeInfo.intTypeLength or 256
                v.value = UnsignedIntegerInterval.top(type_len)
            elif et == "bool":
                v.value = BoolInterval.top()
            elif et == "address":
                v.value = AddressSet.top()
            else:  # bytes/string
                v.value = f"symbol_{var_name}"

    # Analyzer/ContractAnalyzer.py
    def process_yul_assignment(self, lhs: Expression, rhs: Expression) -> None:
        """Yul assembly 내 대입문: 기존 Solidity 변수에 대입 (prod0 := ...). (Phase 3a: IR 만 저장)"""
        ccf = self.contract_cfgs[self.current_target_contract]
        self.current_target_function_cfg = ccf.get_function_cfg(self.current_target_function, param_types=self.current_target_function_param_types)
        if self.current_target_function_cfg is None:
            raise ValueError("No active function CFG.")
        fcfg = self.current_target_function_cfg
        cur_blk = self.builder.get_current_block()
        # (2026-10-23, Yul P0-a) 우변은 word — 대상 Solidity 변수 타입으로의 cleanup 은 실행 때(YulAssignValue)
        value = Expression(expression=rhs, identifier=lhs.identifier, context="YulAssignValue")
        self.builder.build_assignment_statement(
            cur_block=cur_blk,
            expr=Expression(left=lhs, operator='=', right=value, context="YulAssignmentContext"),
            line_no=self.current_start_line, fcfg=fcfg, line_info=self.sa.line_info,
        )
        ccf.update_function_cfg(self.current_target_function, fcfg)
        self.contract_cfgs[self.current_target_contract] = ccf

    def process_yul_variable_declaration(self, var_name: str, rhs: Expression | None) -> None:
        """Yul let 선언: uint256 변수 (let mm := ...). (Phase 3a: IR 만 저장)"""
        ccf = self.contract_cfgs[self.current_target_contract]
        self.current_target_function_cfg = ccf.get_function_cfg(self.current_target_function, param_types=self.current_target_function_param_types)
        if self.current_target_function_cfg is None:
            raise ValueError("No active function CFG.")
        fcfg = self.current_target_function_cfg
        cur_blk = self.builder.get_current_block()
        # Yul 변수는 항상 uint256
        t = SolType()
        t.typeCategory = "elementary"
        t.elementaryTypeName = "uint256"
        t.intTypeLength = 256
        init = Expression(expression=rhs, context="YulWordContext") if rhs is not None else None
        self.builder.build_variable_declaration(
            cur_block=cur_blk, var_name=var_name, type_obj=t, init_expr=init,
            line_no=self.current_start_line, fcfg=fcfg, line_info=self.sa.line_info,
        )
        ccf.update_function_cfg(self.current_target_function, fcfg)
        self.contract_cfgs[self.current_target_contract] = ccf

    def process_yul_statement(self, expr: Expression) -> None:
        """(2026-10-23, Yul P0-a) Yul 문장 하나(builtin 호출 문장 / 미지원 구문 표식)를 문장 노드로. 실행은 Evaluation._evaluate_yul."""
        ccf = self.contract_cfgs[self.current_target_contract]
        fcfg = ccf.get_function_cfg(self.current_target_function, param_types=self.current_target_function_param_types)
        if fcfg is None:
            raise ValueError("No active function CFG.")
        self.current_target_function_cfg = fcfg
        self.builder.build_function_call_statement(
            cur_block=self.builder.get_current_block(), expr=expr,
            line_no=self.current_start_line, fcfg=fcfg, line_info=self.sa.line_info,
        )
        ccf.update_function_cfg(self.current_target_function, fcfg)
        self.contract_cfgs[self.current_target_contract] = ccf

    def process_assignment_expression(self, expr: Expression) -> None:
        """대입문 — (Phase 3a, D1) 평가하지 않고 IR 만 저장한다."""
        ccf = self.contract_cfgs[self.current_target_contract]
        self.current_target_function_cfg = ccf.get_function_cfg(self.current_target_function, param_types=self.current_target_function_param_types)
        if self.current_target_function_cfg is None:
            raise ValueError("No active function CFG.")
        fcfg = self.current_target_function_cfg
        cur_blk = self.builder.get_current_block()
        self.builder.build_assignment_statement(
            cur_block=cur_blk, expr=expr,
            line_no=self.current_start_line, fcfg=fcfg, line_info=self.sa.line_info,
        )
        ccf.update_function_cfg(self.current_target_function, fcfg)
        self.contract_cfgs[self.current_target_contract] = ccf

    # --------------------------------------------------------------
    #  ++x / --x   (prefix·suffix 공통)
    # --------------------------------------------------------------
    def handle_unary_incdec(self, expr: Expression,
                             op_sign: str,  # "+=" | "-="
                             stmt_kind: str):  # "unary_prefix" | "unary_suffix" (문장 단위에선 의미 차이 없음)
        """++x / --x 문장 — (Phase 3a, D1) IR 만 저장. IR 에는 실제 연산자(Phase 2)."""
        ccf = self.contract_cfgs[self.current_target_contract]
        self.current_target_function_cfg = ccf.get_function_cfg(self.current_target_function, param_types=self.current_target_function_param_types)
        if self.current_target_function_cfg is None:
            raise ValueError("No active function CFG.")
        fcfg = self.current_target_function_cfg
        cur_blk = self.builder.get_current_block()
        self.builder.build_unary_statement(
            cur_block=cur_blk, expr=expr,
            op_token="++" if op_sign == "+=" else "--",
            line_no=self.current_start_line, fcfg=fcfg, line_info=self.sa.line_info,
        )
        ccf.update_function_cfg(self.current_target_function, fcfg)
        self.contract_cfgs[self.current_target_contract] = ccf

    # --------------------------------------------------------------
    #  delete <expr>
    # --------------------------------------------------------------
    def handle_delete(self, target_expr: Expression):
        """delete <expr> 문장 — (Phase 3a, D1) IR 만 저장. 의미(타입별 0 초기화)는 해석기(`Engine._interpret_unary`)."""
        ccf = self.contract_cfgs[self.current_target_contract]
        self.current_target_function_cfg = ccf.get_function_cfg(self.current_target_function, param_types=self.current_target_function_param_types)
        if self.current_target_function_cfg is None:
            raise ValueError("No active function CFG.")
        fcfg = self.current_target_function_cfg
        cur_blk = self.builder.get_current_block()
        self.builder.build_unary_statement(
            cur_block=cur_blk, expr=target_expr, op_token="delete",
            line_no=self.current_start_line, fcfg=fcfg, line_info=self.sa.line_info,
        )
        ccf.update_function_cfg(self.current_target_function, fcfg)
        self.contract_cfgs[self.current_target_contract] = ccf

    # ───────────────────────────────────────────────────────────
    def process_unary_prefix_operation(self, expr: Expression):
        if expr.operator == "++":
            self.handle_unary_incdec(expr.expression, "+=", "unary_prefix")
        elif expr.operator == "--":
            self.handle_unary_incdec(expr.expression, "-=", "unary_prefix")
        elif expr.operator == "delete":
            self.handle_delete(expr.expression)
        else:
            raise ValueError(f"Unsupported prefix operator {expr.operator}")

    def process_unary_suffix_operation(self, expr: Expression):
        if expr.operator == "++":
            self.handle_unary_incdec(expr.expression, "+=", "unary_suffix")
        elif expr.operator == "--":
            self.handle_unary_incdec(expr.expression, "-=", "unary_suffix")
        else:
            raise ValueError(f"Unsupported suffix operator {expr.operator}")

    # ==================================================================
    #  함수 호출 처리
    # ==================================================================
    # ==================================================================
    #  함수 호출 처리
    # ==================================================================
    def process_function_call(self, expr: Expression) -> None:
        """함수 호출문 — (Phase 3a, D1/D3) IR 만 저장한다. callee 는 디버깅 해석 때 찾는다(pending 없음)."""
        ccf = self.contract_cfgs[self.current_target_contract]
        self.current_target_function_cfg = ccf.get_function_cfg(self.current_target_function, param_types=self.current_target_function_param_types)
        if self.current_target_function_cfg is None:
            raise ValueError("No active function CFG.")
        fcfg = self.current_target_function_cfg
        cur_blk = self.builder.get_current_block()
        self.builder.build_function_call_statement(
            cur_block=cur_blk, expr=expr,
            line_no=self.current_start_line, fcfg=fcfg, line_info=self.sa.line_info,
        )
        ccf.update_function_cfg(self.current_target_function, fcfg)
        self.contract_cfgs[self.current_target_contract] = ccf

    def process_expression_statement(self, expr: Expression) -> None:
        """값만 있는 식 문장(`a == b;`, `x.y;`). 식을 평가하고 결과를 버린다 — 부수효과 있는 하위 호출은 평가가 실행한다."""
        ccf = self.contract_cfgs[self.current_target_contract]
        fcfg = ccf.get_function_cfg(self.current_target_function, param_types=self.current_target_function_param_types)
        if fcfg is None:
            raise ValueError("No active function CFG.")
        self.current_target_function_cfg = fcfg
        self.builder.build_expression_statement(
            cur_block=self.builder.get_current_block(), expr=expr,
            line_no=self.current_start_line, fcfg=fcfg, line_info=self.sa.line_info)
        ccf.update_function_cfg(self.current_target_function, fcfg)

    def process_payable_function_call(self, expr):
        # `payable(x);` 단독 문장 — 효과 없는 변환식(solc: "statement has no effect" 경고). 노드를 만들지 않는다.
        # (`to.call{value: v}("")` 같은 옵션 호출 문장은 FunctionCallContext 로 와서 process_function_call 이 처리한다.)
        pass

    def process_function_call_options(self, expr):
        # `x.f{value: v};` — 호출 괄호 없는 옵션식은 컴파일 오류다 (Phase 4b, D4(a)). 예전엔 조용히 무시했다.
        raise ValueError(f"line {self.current_start_line}: function call options must be followed by a call "
                         f"(compile error)")

    # ═══════════════════════════════════════════════════════════════════
    #  INTENT ANNOTATION PROCESSORS (@During, @Post)
    # ═══════════════════════════════════════════════════════════════════

    def _cfg_node_at(self, line_no: int):
        """해당 라인의 CFG 노드 반환"""
        info = self.sa.line_info.get(line_no, {})
        nodes = info.get("cfg_nodes", [])
        return nodes[0] if nodes else None

    def _find_prev_cfg_node(self, line_no: int, fcfg=None):
        """빈 줄 annotation 의 **프로그램 지점** 노드 (2026-10-23, 사용자 결정). 노드는 실행 후 상태를 담는다.
          · 같은 블록에서 앞 문장이 있으면 그 문장 노드        → a 실행 후 = b 실행 전
          · 앞 형제가 블록 문장(`}` 로 끝남)이면 그 합류·출구 노드 → 분기·루프가 끝난 뒤
          · 블록의 첫 줄이면 블록 진입 노드(if/else 분기·루프 본문·함수 ENTRY) → 블록에 들어온 직후
        예전엔 "바로 앞 줄의 첫 노드" 라 블록 첫 줄이면 if 조건 노드(분기 전 상태), `}` 뒤면 분기 안 노드에 붙을 수 있었다."""
        G = fcfg.graph if fcfg is not None else None

        def ok(n):
            return n is not None and (G is None or n in G)

        block = self._enclosing_open_brace_line(line_no, inclusive=True)
        ln = line_no - 1
        while block is not None and ln > block:
            txt = self.sa.full_code_lines.get(ln, "").strip()
            if not txt or txt.startswith("//"):
                ln -= 1
                continue
            nodes = [n for n in self.sa.line_info.get(ln, {}).get("cfg_nodes", []) or [] if ok(n)]
            if txt.startswith("}"):
                # 앞 형제 = 이 `}` 로 닫히는 블록 문장 — 그 합류/루프 출구 노드(닫는 줄에 등록된다)
                for n in nodes:
                    if getattr(n, "join_point_node", False) or getattr(n, "loop_exit_node", False):
                        return n
                raise ValueError(f"line {line_no}: 앞 블록 문장(줄 {ln})의 합류 노드를 찾지 못함")
            if nodes:
                return nodes[-1]
            ln -= 1
        if block is None:
            return None
        # 블록 첫 줄 — 블록 진입 노드
        for n in self.sa.line_info.get(block, {}).get("cfg_nodes", []) or []:
            if not ok(n):
                continue
            if getattr(n, "condition_node", False) and G is not None:
                t = next((x for x in G.successors(n) if G[n][x].get("condition") is True), None)
                if t is not None:
                    return t
            if getattr(n, "name", "") == "ENTRY" or n is getattr(fcfg, "entry_node", None):
                return n
            if getattr(n, "branch_node", False) or n.name.startswith(("else_block", "catch_entry")):
                return n
        raise ValueError(f"line {line_no}: 블록(줄 {block})의 진입 노드를 찾지 못함")

    def _intent_function_cfg(self, fcfg=None):
        """intent 를 붙일 함수. (2026-10-22) 명시되면 그것, 아니면 **현재 줄 문맥의 함수**(이름+파라미터 타입으로 조회).
        예전엔 `current_target_function_cfg` 를 썼는데 이 객체는 줄 위치로 갱신되지 않고 **마지막으로 처리한 문장의
        함수**로 남아, 코드를 다 쓴 뒤 들어온 @Post 가 파일의 마지막 함수에 붙었다(평가 12 케이스 @Post 판정 누락)."""
        if fcfg is not None:
            return fcfg
        ccf = self.contract_cfgs.get(self.current_target_contract)
        if ccf is None or not self.current_target_function:
            return None
        return ccf.get_function_cfg(self.current_target_function,
                                    param_types=self.current_target_function_param_types)

    def process_during(self, clauses: list[dict], logic_ops: list[str], fcfg=None) -> dict:
        """
        @During annotation 처리 (새 문법)
        clauses: list of clause dicts (kind, var, op, etc.)
        logic_ops: list of '&&' or '||'

        Intent를 CFG 노드에 저장하고, 해석 시점에 검증한다.
        """
        line_no = self.current_start_line
        fn_cfg = self._intent_function_cfg(fcfg)
        cfg_node = self._cfg_node_at(line_no)
        # 붙일 노드는 **대상 함수 안**의 노드여야 한다 (다른 함수의 같은 줄 번호 노드에 붙지 않게)
        if cfg_node is not None and fn_cfg is not None and cfg_node not in fn_cfg.graph:
            cfg_node = None

        # annotation 저장 (기존 호환성)
        if line_no not in self.during_annotations:
            self.during_annotations[line_no] = []
        self.during_annotations[line_no].append({"clauses": clauses, "logic_ops": logic_ops})

        # Standalone annotation(빈 줄): 그 위치의 프로그램 지점 노드
        if cfg_node is None:
            cfg_node = self._find_prev_cfg_node(line_no, fn_cfg)
            if cfg_node is not None and fn_cfg is not None and cfg_node not in fn_cfg.graph:
                cfg_node = None

        # CFG 노드에 intent 저장 (해석 시 검증용)
        if cfg_node is not None:
            intent_data = {
                "type": "during",
                "clauses": clauses,
                "logic_ops": logic_ops,
                "line_no": line_no
            }
            cfg_node.intents.append(intent_data)
            return {"status": "pending", "message": "Intent attached to CFG node, will be verified during interpretation"}

        # ★ (Phase 3a, D4-b) 붙일 노드가 없는 @During = annotation 규칙 위반.
        #   예전엔 구성 시점 상태로 "즉시 검증" 하는 폴백이 있었다(디버깅 입력과 무관한 결과).
        raise ValueError(f"@During at line {line_no}: no statement node at or before this line in the function")

    def _verify_during_clause(self, clause: dict, line_no: int, cfg_node) -> dict:
        """개별 during clause 검증"""
        kind = clause.get("kind")

        if kind == "beforeAfter":
            return self.guardian_verifier.verify_during_before_after(
                var_ref=clause["var"], comp_op=clause["op"],
                line_no=line_no, cfg_node=cfg_node
            )
        elif kind == "assignCurrent":
            return self.guardian_verifier.verify_during_assign_current(
                var_ref=clause["var"], comp_op=clause["op"],
                line_no=line_no, cfg_node=cfg_node
            )
        elif kind == "functionArg":
            return self.guardian_verifier.verify_during_function_arg(
                func_name=clause["func_name"], arg_index=clause["arg_index"],
                comp_op=clause["op"], rhs_expr=clause["rhs"],
                line_no=line_no, cfg_node=cfg_node
            )
        elif kind == "retExpr":
            return self.guardian_verifier.verify_during_return_expression(
                comp_op=clause["op"], value_expr=clause["rhs"],
                line_no=line_no, cfg_node=cfg_node
            )
        elif kind == "retIndex":
            return self.guardian_verifier.verify_during_return_index(
                index=clause["index"], comp_op=clause["op"], value_expr=clause["rhs"],
                line_no=line_no, cfg_node=cfg_node
            )
        elif kind == "retVar":
            return self.guardian_verifier.verify_during_return_variable(
                var_ref=clause["lhs"], comp_op=clause["op"], value_expr=clause["rhs"],
                line_no=line_no, cfg_node=cfg_node
            )
        elif kind == "direct":
            return self.guardian_verifier.verify_during_direct_comparison(
                lhs_expr=clause["lhs"], comp_op=clause["op"], rhs_expr=clause["rhs"],
                line_no=line_no, cfg_node=cfg_node
            )
        elif kind == "implication":
            return self.guardian_verifier.verify_during_implication(
                antecedent=clause["antecedent"], consequent=clause["consequent"],
                line_no=line_no, cfg_node=cfg_node
            )
        else:
            return {"status": "error", "message": f"Unknown during clause kind: {kind}"}

    def _verify_during_compound(self, clauses: list[dict], logic_ops: list[str],
                                 line_no: int, cfg_node) -> dict:
        """복합 during clause 검증 (&& / ||)"""
        results = [self._verify_during_clause(c, line_no, cfg_node) for c in clauses]

        # 간단한 로직: && = 모두 success, || = 하나라도 success
        statuses = [r.get("status") for r in results]

        if all(op == "&&" for op in logic_ops):
            if all(s == "success" for s in statuses):
                final_status = "success"
            elif any(s == "violated" for s in statuses):
                final_status = "violated"
            else:
                final_status = "warning"
        else:  # || 포함
            if any(s == "success" for s in statuses):
                final_status = "success"
            elif all(s == "violated" for s in statuses):
                final_status = "violated"
            else:
                final_status = "warning"

        return {
            "status": final_status,
            "kind": "duringCompound",
            "line": line_no,
            "details": {"clauses": results, "logic_ops": logic_ops}
        }

    def process_post(self, clauses: list[dict], logic_ops: list[str], fcfg=None) -> dict:
        """
        @Post annotation 처리 (새 문법)

        Intent를 함수 exit 노드에 저장하고, 함수 종료 시점에 검증한다.
        """
        line_no = self.current_start_line
        fn_cfg = self._intent_function_cfg(fcfg)

        # annotation 저장 (기존 호환성)
        if line_no not in self.post_annotations:
            self.post_annotations[line_no] = []
        self.post_annotations[line_no].append({"clauses": clauses, "logic_ops": logic_ops})

        # 함수 exit 노드에 intent 저장 (해석 시 검증용)
        if fn_cfg is not None:
            exit_node = fn_cfg.get_exit_node() if hasattr(fn_cfg, 'get_exit_node') else None
            if exit_node is not None and hasattr(exit_node, 'intents'):
                intent_data = {
                    "type": "post",
                    "clauses": clauses,
                    "logic_ops": logic_ops,
                    "line_no": line_no
                }
                exit_node.intents.append(intent_data)
                return {"status": "pending", "message": "Post intent attached to exit node, will be verified at function end"}

        # ★ (Phase 3a, D4-b) 함수 밖의 @Post = annotation 규칙 위반 (예전엔 구성 시점 상태로 즉시 검증하는 폴백).
        raise ValueError(f"@Post at line {line_no}: not inside a function")

    def _verify_post_clause(self, clause: dict, line_no: int, fn_cfg) -> dict:
        """개별 post clause 검증"""
        kind = clause.get("kind")

        if kind == "entryExit":
            return self.guardian_verifier.verify_post_entry_exit(
                var_ref=clause["var"], comp_op=clause["op"],
                line_no=line_no, fn_cfg=fn_cfg
            )
        elif kind == "unchanged":
            return self.guardian_verifier.verify_post_unchanged(
                var_ref=clause["var"], line_no=line_no, fn_cfg=fn_cfg
            )
        elif kind == "retExpr":
            return self.guardian_verifier.verify_post_return_expression(
                comp_op=clause["op"], value_expr=clause["rhs"],
                line_no=line_no, fn_cfg=fn_cfg
            )
        elif kind == "retIndex":
            return self.guardian_verifier.verify_post_return_index(
                index=clause["index"], comp_op=clause["op"], value_expr=clause["rhs"],
                line_no=line_no, fn_cfg=fn_cfg
            )
        elif kind == "retVar":
            return self.guardian_verifier.verify_post_return_variable(
                var_ref=clause["lhs"], comp_op=clause["op"], value_expr=clause["rhs"],
                line_no=line_no, fn_cfg=fn_cfg
            )
        elif kind == "direct":
            return self.guardian_verifier.verify_post_direct_comparison(
                lhs_expr=clause["lhs"], comp_op=clause["op"], rhs_expr=clause["rhs"],
                line_no=line_no, fn_cfg=fn_cfg
            )
        elif kind == "implication":
            return self.guardian_verifier.verify_post_implication(
                antecedent=clause["antecedent"], consequent=clause["consequent"],
                line_no=line_no, fn_cfg=fn_cfg
            )
        else:
            return {"status": "error", "message": f"Unknown post clause kind: {kind}"}

    def _verify_post_compound(self, clauses: list[dict], logic_ops: list[str],
                               line_no: int, fn_cfg) -> dict:
        """복합 post clause 검증 (&& / ||)"""
        results = [self._verify_post_clause(c, line_no, fn_cfg) for c in clauses]

        statuses = [r.get("status") for r in results]

        if all(op == "&&" for op in logic_ops):
            if all(s == "success" for s in statuses):
                final_status = "success"
            elif any(s == "violated" for s in statuses):
                final_status = "violated"
            else:
                final_status = "warning"
        else:
            if any(s == "success" for s in statuses):
                final_status = "success"
            elif all(s == "violated" for s in statuses):
                final_status = "violated"
            else:
                final_status = "warning"

        return {
            "status": final_status,
            "kind": "postCompound",
            "line": line_no,
            "details": {"clauses": results, "logic_ops": logic_ops}
        }

    # ═══════════════════════════════════════════════════════════════════

    def process_if_statement(self, condition_expr: Expression) -> None:
        # ── 1. CFG 컨텍스트 ─────────────────────────────────────────────
        ccf = self.contract_cfgs[self.current_target_contract]
        self.current_target_function_cfg = ccf.get_function_cfg(self.current_target_function, param_types=self.current_target_function_param_types)
        if self.current_target_function_cfg is None:
            raise ValueError("No active function CFG.")
        fcfg = self.current_target_function_cfg

        # modify(IQDebug): 조건만 바뀐 in-place 수정 → 새 if 빌드 대신 condition_expr 만 교체(body 보존).
        if self.current_edit_event == "modify":
            if self.builder.modify_condition_expr(fcfg, self.current_start_line, condition_expr):
                return

        cur_blk = self.builder.get_current_block()

        # (Phase 3a, D1) 구조만 — 분기 refine 은 디버깅 해석의 엣지 전이(`Engine._edge_env_from_pred`)가 한다.
        self.builder.build_if_statement(
            cur_block=cur_blk,
            condition_expr=condition_expr,
            line_no=self.current_start_line,
            fcfg=fcfg,
            line_info=self.sa.line_info,
            end_line=self.current_end_line,
        )

        # ── 4. 저장 & 마무리 ───────────────────────────────────────────
        ccf.update_function_cfg(self.current_target_function, self.current_target_function_cfg)
        self.contract_cfgs[self.current_target_contract] = ccf

    def process_else_if_statement(self, condition_expr: Expression) -> None:
        ccf = self.contract_cfgs[self.current_target_contract]
        fcfg = ccf.get_function_cfg(self.current_target_function, param_types=self.current_target_function_param_types)
        if fcfg is None:
            raise ValueError("No active function CFG.")
        self.current_target_function_cfg = fcfg

        # ★ get_current_block이 (prev_cond, outer_join) 튜플을 리턴
        prev_cond, outer_join = self.builder.get_current_block(context="else_if")
        if prev_cond is None:
            raise ValueError("else-if used without a preceding if/else-if.")

        # (Phase 3a, D1) 구조만 — refine 은 디버깅 해석의 엣지 전이가 한다.
        self.builder.build_else_if_statement(
            prev_cond=prev_cond,
            outer_join=outer_join,
            condition_expr=condition_expr,
            line_no=self.current_start_line,
            fcfg=fcfg,
            line_info=self.sa.line_info,
            end_line=getattr(self, "current_end_line", None),
        )

        ccf.update_function_cfg(self.current_target_function, fcfg)
        self.contract_cfgs[self.current_target_contract] = ccf

    def process_else_statement(self) -> None:
        # ── 1. CFG 컨텍스트 --------------------------------------------------
        ccf = self.contract_cfgs[self.current_target_contract]
        self.current_target_function_cfg = ccf.get_function_cfg(self.current_target_function, param_types=self.current_target_function_param_types)
        if self.current_target_function_cfg is None:
            raise ValueError("No active FunctionCFG when processing 'else'.")
        fcfg = self.current_target_function_cfg

        # ── 2. 직전 if / else-if 노드 찾기 -----------------------------------
        # ★ get_current_block이 (cond_node, outer_join) 튜플을 리턴
        cond_node, outer_join = self.builder.get_current_block(context="else")
        if cond_node is None:
            raise ValueError("No preceding if/else-if for this 'else'.")

        # (Phase 3a, D1) 구조만 — else 엣지의 refine 은 디버깅 해석이 한다.
        self.builder.build_else_statement(
            cond_node=cond_node,
            outer_join=outer_join,
            line_no=self.current_start_line,
            fcfg=fcfg,
            line_info=self.sa.line_info,
            end_line=self.current_end_line,
        )

        # ── 5. 저장 ----------------------------------------------------------
        ccf.update_function_cfg(self.current_target_function, self.current_target_function_cfg)
        self.contract_cfgs[self.current_target_contract] = ccf

    def process_while_statement(self, condition_expr: Expression) -> None:
        # 1. CFG 컨텍스트 ---------------------------------------------------
        ccf = self.contract_cfgs[self.current_target_contract]
        self.current_target_function_cfg = ccf.get_function_cfg(self.current_target_function, param_types=self.current_target_function_param_types)
        if self.current_target_function_cfg is None:
            raise ValueError("No active function CFG.")

        # modify(IQDebug): 루프 조건만 바뀐 in-place 수정 → condition_expr 만 교체(body 보존).
        if self.current_edit_event == "modify":
            if self.builder.modify_condition_expr(
                    self.current_target_function_cfg, self.current_start_line, condition_expr):
                return

        cur_blk = self.builder.get_current_block()

        # (Phase 3a, D1) 구조만 — 루프 fixpoint 는 디버깅 해석이 한다.
        self.builder.build_while_statement(
            cur_block=cur_blk,
            condition_expr=condition_expr,
            line_no=self.current_start_line,
            fcfg=self.current_target_function_cfg,
            line_info=self.sa.line_info,
            end_line=getattr(self, "current_end_line", None),
        )

        # 4. 저장 ----------------------------------------------------------
        ccf.update_function_cfg(self.current_target_function, self.current_target_function_cfg)
        self.contract_cfgs[self.current_target_contract] = ccf

    def process_for_statement(
            self,
            initial_statement: dict | None = None,
            condition_expr: Expression | None = None,
            increment_expr: Expression | None = None,
    ) -> None:
        # 1. CFG 컨텍스트 --------------------------------------------------
        ccf = self.contract_cfgs[self.current_target_contract]
        self.current_target_function_cfg = ccf.get_function_cfg(self.current_target_function, param_types=self.current_target_function_param_types)
        if self.current_target_function_cfg is None:
            raise ValueError("No active function CFG.")

        # modify(IQDebug): for 조건(bound)만 바뀐 in-place 수정 → cond_expr 만 교체(body 보존).
        #   (init/incr 변경은 미반영 — 흔한 케이스는 조건 변경이라 cond swap 으로 커버.)
        if self.current_edit_event == "modify" and condition_expr is not None:
            if self.builder.modify_condition_expr(
                    self.current_target_function_cfg, self.current_start_line, condition_expr):
                return

        cur_blk = self.builder.get_current_block()

        # 2. ─────── init-노드 (있을 때만)  &  변수 환경 --------------------
        init_node: CFGNode | None = None

        if initial_statement:
            ctx = initial_statement["context"]

            # ★ init 노드에도 **for 헤더 라인**을 준다(⑰). `uint256 i = 0` 은 실제로
            #   존재하고 실행되는 문장인데 `src_line` 이 없어서(기본 -1) 프론트가
            #   Step Forward 로 이 노드에 착지하면 **코드 창에 하이라이트되는 줄이 없었다**
            #   (디버거에서 Step 을 눌렀는데 커서가 사라지는 셈).
            #   "반복문이 시작되는 위치" 가 단위이므로 init/cond/incr 를 구분하지 않고
            #   헤더 라인으로 묶는다(사용자 결정).
            #   ⚠ `line_info[line_no]["cfg_nodes"]` 에는 넣지 않는다 — 그쪽은 intent 부착·
            #     삭제 수술이 쓰는 "라인 → 대표 노드" 매핑이라 cond 하나만 유지해야 한다.
            init_node = CFGNode(f"for_init_{self.current_start_line}",
                                src_line=self.current_start_line,
                                is_for_init=True)

            # (Phase 3a, D1) init 노드는 문장 IR 만 — 값은 디버깅 해석이 만든다.
            if ctx == "VariableDeclaration":
                v_type = initial_statement["initVarType"]
                v_name = initial_statement["initVarName"]
                init_expr = initial_statement["initValExpr"]
                if init_expr is not None:
                    self._retype_literal_to_declared(init_expr, v_type)
                init_node.add_variable_declaration_statement(
                    v_type, v_name, init_expr, self.current_start_line
                )
                self._mark_shadowing(self.current_target_function_cfg, init_node.statements[-1])  # (Phase 3b, D8)

            elif ctx == "Expression":
                assn_expr = initial_statement["initExpr"]  # Assignment 식
                init_node.add_assign_statement(
                    assn_expr.left, assn_expr.operator, assn_expr.right,
                    self.current_start_line,
                )
            else:
                raise ValueError(f"[for] unknown init ctx '{ctx}'")

        incr_node: CFGNode | None = None
        if increment_expr is not None:
            incr_node = CFGNode(f"for_incr_{self.current_start_line}",
                                is_for_increment=True)
            # ---- ++ / -- : 단항 문장
            if increment_expr.operator in {"++", "--"}:
                incr_node.add_unary_statement(
                    operand=increment_expr.expression,  # i
                    operator=increment_expr.operator,  # '++' or '--'
                    line_no=self.current_start_line,
                )
            # ---- 복합 대입( += n / -= n … )
            else:
                incr_node.add_assign_statement(
                    increment_expr.left, increment_expr.operator, increment_expr.right,
                    self.current_start_line)
            # (Phase 2) for 헤더가 unchecked 블록 안이면 증감식도 wrap
            if self.is_inside_unchecked(self.current_start_line):
                incr_node.unchecked_block = True

        self.builder.build_for_statement(
            cur_block=cur_blk,
            init_node=init_node,
            cond_expr=condition_expr,
            incr_node=incr_node,
            line_no=self.current_start_line,
            fcfg=self.current_target_function_cfg,
            line_info=self.sa.line_info,
            end_line=getattr(self, "current_end_line", None),
        )

        # 6. 저장 ---------------------------------------------------------
        ccf.update_function_cfg(self.current_target_function, self.current_target_function_cfg)
        self.contract_cfgs[self.current_target_contract] = ccf

    def process_continue_statement(self) -> None:
        # 1) CFG 컨텍스트
        ccf = self.contract_cfgs[self.current_target_contract]
        self.current_target_function_cfg = ccf.get_function_cfg(self.current_target_function, param_types=self.current_target_function_param_types)
        if self.current_target_function_cfg is None:
            raise ValueError("No active FunctionCFG when processing 'continue'.")

        # 2) 현재 블록
        cur_blk = self.builder.get_current_block()

        # ★ 빌더가 loop-exit 을 반환
        exit_node = self.builder.build_continue_statement(
            cur_block=cur_blk,
            line_no=self.current_start_line,
            fcfg=self.current_target_function_cfg,
            line_info=self.sa.line_info,
        )


        # 5) 저장
        ccf.update_function_cfg(self.current_target_function, self.current_target_function_cfg)
        self.contract_cfgs[self.current_target_contract] = ccf

    def process_break_statement(self) -> None:
        ccf = self.contract_cfgs[self.current_target_contract]
        self.current_target_function_cfg = ccf.get_function_cfg(self.current_target_function, param_types=self.current_target_function_param_types)
        if self.current_target_function_cfg is None:
            raise ValueError("No active FunctionCFG when processing 'break'.")

        cur_blk = self.builder.get_current_block()

        # ★ 빌더가 loop-exit 을 반환
        exit_node = self.builder.build_break_statement(
            cur_block=cur_blk,
            line_no=self.current_start_line,
            fcfg=self.current_target_function_cfg,
            line_info=self.sa.line_info,
        )


        ccf.update_function_cfg(self.current_target_function, self.current_target_function_cfg)
        self.contract_cfgs[self.current_target_contract] = ccf

    # Analyzer/ContractAnalyzer.py
    def process_return_statement(self, return_expr: Expression | None = None) -> None:
        # ── 1. CFG 컨텍스트 -------------------------------------------------
        ccf = self.contract_cfgs[self.current_target_contract]
        self.current_target_function_cfg = ccf.get_function_cfg(self.current_target_function, param_types=self.current_target_function_param_types)
        if self.current_target_function_cfg is None:
            raise ValueError("No active FunctionCFG when processing 'return'.")

        cur_blk = self.builder.get_current_block()

        # (Phase 3a, D1) 구조·IR 만 — 반환값은 디버깅 해석(`Engine._interpret_return`)이 계산한다.
        #   예전엔 여기서 평가한 값을 RETURN.return_vals 에 남겨 @Post 검증이 그 구성 시점 값을 쓰기도 했다(F55).
        self.builder.build_return_statement(
            cur_block=cur_blk,
            return_expr=return_expr,
            line_no=self.current_start_line,
            fcfg=self.current_target_function_cfg,
            line_info=self.sa.line_info,
        )

        # ── 5. CFG 저장 -----------------------------------------------------
        ccf.update_function_cfg(self.current_target_function, self.current_target_function_cfg)
        self.contract_cfgs[self.current_target_contract] = ccf

    # ──────────────────────────────────────────────────────────────────────
    # Event / Emit  (추상 해석에는 영향 없음 — 정의만 기록, emit은 skip)
    # ──────────────────────────────────────────────────────────────────────
    def process_event_definition(self, event_name: str, parameters: list[dict]) -> None:
        """event Transfer(address indexed from, ...) 정의를 ContractCFG에 저장"""
        ccf = self.contract_cfgs[self.current_target_contract]
        ccf.events[event_name] = parameters

    def process_emit_statement(self, event_name: str, arguments: list) -> None:
        """emit 문 — 이벤트 자체는 상태를 바꾸지 않지만 **인자 식은 평가된다**(호출 부수효과 등).
        ★ (Phase 2) 예전엔 노드를 만들지 않아 `emit E(inc())` 의 `inc()` 효과가 사라졌다."""
        ccf = self.contract_cfgs[self.current_target_contract]
        self.current_target_function_cfg = ccf.get_function_cfg(self.current_target_function, param_types=self.current_target_function_param_types)
        if self.current_target_function_cfg is None:
            raise ValueError("No active function CFG.")
        fcfg = self.current_target_function_cfg
        if isinstance(arguments, dict):        # named arguments — 평가 순서는 선언 순서와 무관하게 값만 필요
            arguments = list(arguments.values())
        cur_blk = self.builder.get_current_block()
        self.builder.build_emit_statement(
            cur_block=cur_blk, event_name=event_name, arguments=arguments,
            line_no=self.current_start_line, fcfg=fcfg, line_info=self.sa.line_info)
        ccf.update_function_cfg(self.current_target_function, self.current_target_function_cfg)
        self.contract_cfgs[self.current_target_contract] = ccf

    # Analyzer/ContractAnalyzer.py
    def process_revert_statement(
            self,
            revert_identifier: str | None = None,
            string_literal: str | None = None,
            call_argument_list: list[Expression] | None = None,
    ) -> None:
        # ── 1. CFG context ---------------------------------------------------
        ccf = self.contract_cfgs[self.current_target_contract]
        self.current_target_function_cfg = ccf.get_function_cfg(self.current_target_function, param_types=self.current_target_function_param_types)
        if self.current_target_function_cfg is None:
            raise ValueError("No active FunctionCFG when processing 'revert'.")

        cur_blk = self.builder.get_current_block()

        # ★ 빌더가 ‘재배선 전’ succ 들을 반환
        succ_before = self.builder.build_revert_statement(
            cur_block=cur_blk,
            revert_id=revert_identifier,
            string_literal=string_literal,
            call_args=call_argument_list,
            line_no=self.current_start_line,
            fcfg=self.current_target_function_cfg,
            line_info=self.sa.line_info,
        )


        # ── 4. save CFG ------------------------------------------------------
        ccf.update_function_cfg(self.current_target_function, self.current_target_function_cfg)
        self.contract_cfgs[self.current_target_contract] = ccf

    # Analyzer/ContractAnalyzer.py
    def process_require_statement(
            self,
            condition_expr: Expression,
            string_literal: str | None,
    ) -> None:
        # 1) CFG context -----------------------------------------------------
        ccf = self.contract_cfgs[self.current_target_contract]
        self.current_target_function_cfg = ccf.get_function_cfg(self.current_target_function, param_types=self.current_target_function_param_types)
        if self.current_target_function_cfg is None:
            raise ValueError("No active FunctionCFG.")

        cur_blk = self.builder.get_current_block()

        # 2) True-branch 환경 ------------------------------------------------

        # ★ 빌더가 true-분기 succ 들을 반환
        true_succs = self.builder.build_require_statement(
            cur_block=cur_blk,
            condition_expr=condition_expr,
            line_no=self.current_start_line,
            fcfg=self.current_target_function_cfg,
            line_info=self.sa.line_info,
        )


        # 5) 저장 ------------------------------------------------------------
        ccf.update_function_cfg(self.current_target_function, self.current_target_function_cfg)
        self.contract_cfgs[self.current_target_contract] = ccf

    # Analyzer/ContractAnalyzer.py
    def process_assert_statement(
            self,
            condition_expr: Expression,
            string_literal: str | None = None,   # ★ (Phase 2) assert 는 메시지가 없다 — visitor 는 조건만 넘긴다
    ) -> None:
        # 1) CFG context -----------------------------------------------------
        ccf = self.contract_cfgs[self.current_target_contract]
        self.current_target_function_cfg = ccf.get_function_cfg(self.current_target_function, param_types=self.current_target_function_param_types)
        if self.current_target_function_cfg is None:
            raise ValueError("No active FunctionCFG.")

        cur_blk = self.builder.get_current_block()

        # 2) True-branch 환경(조건이 만족되는 경로) ---------------------------

        # ★ 빌더가 true-분기 succ 들을 반환
        true_succs = self.builder.build_assert_statement(
            cur_block=cur_blk,
            condition_expr=condition_expr,
            line_no=self.current_start_line,
            fcfg=self.current_target_function_cfg,
            line_info=self.sa.line_info,
        )


        # 5) 저장 -------------------------------------------------------------
        ccf.update_function_cfg(self.current_target_function, self.current_target_function_cfg)
        self.contract_cfgs[self.current_target_contract] = ccf

    # ContractAnalyzer.py  (추가/수정)

    # Analyzer/ContractAnalyzer.py
    def process_identifier_expression(self, ident_expr: Expression) -> None:
        """
        · ident == '_'  and  현재 CFG 가 modifier 이면  placeholder 처리
          그렇지 않으면 그냥 식별자 평가(별도 로직).
        """
        ident = ident_expr.identifier
        ccf = self.contract_cfgs[self.current_target_contract]
        self.current_target_function_cfg = ccf.get_function_cfg(self.current_target_function, param_types=self.current_target_function_param_types)

        # ── modifier placeholder (‘_’) --------------------------------------
        if (ident == "_" and self.current_target_function_cfg
                and self.current_target_function_cfg.function_type == "modifier"):
            cur_blk = self.builder.get_current_block()

            # ⬇️  새 helper 호출
            self.builder.build_modifier_placeholder(
                cur_block=cur_blk,
                fcfg=self.current_target_function_cfg,
                line_no=self.current_start_line,
                line_info=self.sa.line_info,
            )
            return  # 값-해석 없음

        # … 이하 “일반 identifier” 처리는 기존 로직 유지 …

    # Analyzer/ContractAnalyzer.py
    def process_unchecked_indicator(self) -> None:
        # ── 1. CFG 컨텍스트 --------------------------------------------
        ccf = self.contract_cfgs[self.current_target_contract]
        self.current_target_function_cfg = ccf.get_function_cfg(self.current_target_function, param_types=self.current_target_function_param_types)
        if self.current_target_function_cfg is None:
            raise ValueError("No active FunctionCFG when processing 'unchecked'.")

        # ── 2. 현재 블록, 빌더 호출 -------------------------------------
        cur_blk = self.builder.get_current_block()

        self.builder.build_unchecked_block(
            cur_block=cur_blk,
            line_no=self.current_start_line,
            fcfg=self.current_target_function_cfg,
            line_info=self.sa.line_info,
        )

        # ── 3. 저장 ------------------------------------------------------
        ccf.update_function_cfg(self.current_target_function, self.current_target_function_cfg)
        self.contract_cfgs[self.current_target_contract] = ccf

    # Analyzer/ContractAnalyzer.py  내부 메소드들 추가/교체

    def process_do_statement(self):
        ccf = self.contract_cfgs[self.current_target_contract]
        self.current_target_function_cfg = ccf.get_function_cfg(self.current_target_function, param_types=self.current_target_function_param_types)
        fcfg = self.current_target_function_cfg
        if not fcfg:
            raise ValueError("No current target function to attach do-while.")

        pred = self.builder.get_current_block()  # prev 앵커
        self.builder.build_do_statement(
            cur_block=pred, line_no=self.current_start_line,
            fcfg=fcfg, line_info = self.sa.line_info
        )

    def process_do_while_statement(self, condition_expr):
        ccf = self.contract_cfgs[self.current_target_contract]
        self.current_target_function_cfg = ccf.get_function_cfg(self.current_target_function, param_types=self.current_target_function_param_types)
        fcfg = self.current_target_function_cfg
        if not fcfg:
            raise ValueError("No current target function to attach do-while.")

        # while 라인에서의 pred 앵커 = do_end_*
        pred = self.builder.get_current_block()
        if not getattr(pred, "is_do_end", False):
            raise ValueError("`while (...)` arrived but preceding `do {}` was not found.")

        # do_entry = pred(do_end)
        G = fcfg.graph
        do_entry = None
        for pp in G.predecessors(pred):
            if getattr(pp, "is_do_entry", False):
                do_entry = pp
                break
        if do_entry is None:
            raise ValueError("do-while: do_entry could not be found behind do_end.")

        # ★ builder 가 exit 노드를 반환하도록
        exit_node = self.builder.build_do_while_statement(
            do_entry=do_entry, while_line=self.current_start_line,
            fcfg=fcfg,
            condition_expr = condition_expr,
            line_info = self.sa.line_info
        )

        # ★ seed = loop exit

    def process_try_statement(self, function_expr, returns):
        ccf = self.contract_cfgs[self.current_target_contract]
        self.current_target_function_cfg = ccf.get_function_cfg(self.current_target_function, param_types=self.current_target_function_param_types)
        fcfg = self.current_target_function_cfg
        if not fcfg:
            raise ValueError("No current target function for try.")

        pred = self.builder.get_current_block()  # 이전 블록 기준

        cond, true_blk, join = self.builder.build_try_skeleton(
            cur_block=pred, function_expr=function_expr,
            line_no=self.current_start_line, fcfg=fcfg, line_info=self.sa.line_info,
            end_line=self.current_end_line
        )

        # (2026-10-29 재설계) 호출을 **try_cond 의 statement** 로 둔다.
        #   예전엔 호출식을 `cond.condition_expr` 에 넣고 `returns (…)` 지역을 ⊤ 로 심어
        #   `fcfg.add_related_variable` 까지 했다. 그래서 세 가지가 동시에 틀렸다.
        #     · 호출이 계산한 반환값을 **버렸다** — `r` 이 함수 전체에서 ⊤ 로 남았다
        #       (평범한 `uint256 r = h(n);` 은 제대로 바인딩된다 — 경로가 달랐을 뿐이다)
        #     · `related_variables` 에 들어가 **try 앞·try/catch 뒤에도** `r` 이 보였다
        #       (Solidity 에서 `r` 은 try 성공 블록 스코프다)
        #     · 호출식이 `condition_expr` 에 있어서 간선 flow 계산이 그걸 **술어로 refine** 하고
        #       `_branch_feasible` 로 분기를 ⊥ 로 만들었다(루프 안에서 catch 가 죽었다)
        #   statement 로 옮기면 선언·대입 경로가 전부 처리하고(다중 반환 튜플 해체 포함),
        #   callee 는 착지 가능한 노드에 등록되며(Step Into), 평가가 1회로 끝난다.
        ln = self.current_start_line
        names = [(ty, nm or f"_ret{i}") for i, (ty, nm) in enumerate(returns or [])]

        if not names:
            cond.add_function_call_statement(function_expr, ln)
        elif len(names) == 1:
            ty, nm = names[0]
            cond.add_variable_declaration_statement(ty, nm, function_expr, ln)
            self._mark_shadowing(fcfg, cond.statements[-1])          # (Phase 3b, D8)
        else:
            # 다중 반환 — `build_variable_declaration_tuple` 과 같은 모양:
            #   선언문들(초기화 없음) + 튜플 대입문 하나. 해석기가 호출을 한 번 평가하고
            #   슬롯별로 나눠 넣는다(`Update.update_left_var` 의 TupleExpressionContext 경로).
            for ty, nm in names:
                cond.add_variable_declaration_statement(ty, nm, None, ln)
                self._mark_shadowing(fcfg, cond.statements[-1])      # (Phase 3b, D8)
            lhs = Expression(
                context="TupleExpressionContext",
                elements=[Expression(context="IdentifierExpContext", identifier=nm)
                          for _ty, nm in names],
            )
            cond.add_assign_statement(lhs, '=', function_expr, ln)

        # interface 타입 지역은 @IReturn 조회용으로 등록 (build_variable_declaration_tuple 과 같게)
        for ty, nm in names:
            if getattr(ty, "typeCategory", None) == "interface":
                ifc_name = getattr(ty, "interfaceName", None)
                if ifc_name:
                    fcfg.interface_var_types[nm] = ifc_name

    def process_catch_clause(self, catch_ident, params):
        ccf = self.contract_cfgs[self.current_target_contract]
        self.current_target_function_cfg = ccf.get_function_cfg(self.current_target_function, param_types=self.current_target_function_param_types)
        fcfg = self.current_target_function_cfg
        if not fcfg:
            raise ValueError("No current target function for catch.")

        # (2026-10-29 재설계) catch 는 **순서 있는 조건 체인의 다음 링크**다 — else-if 와 같은 경로로
        #   앞 조건을 찾는다. 첫 catch 면 `try_cond`, 둘째부터는 **앞 `catch_cond`** 다.
        #   예전엔 늘 try cond 를 찾아 모든 catch 가 try 의 **병렬 False 분기**가 됐고, 그래서
        #   Solidity 의 "위에서부터 처음 맞는 하나" 순서·배타성이 모델에 없었다.
        result = self.builder.get_current_block(context="catch")
        if result is None:
            raise ValueError("`catch` without preceding `try`.")
        prev_cond, outer_join = result if isinstance(result, tuple) else (result, None)

        cond, t_blk, local_join = self.builder.build_catch_clause(
            prev_cond=prev_cond, outer_join=outer_join,
            line_no=self.current_start_line, fcfg=fcfg, line_info=self.sa.line_info,
            end_line=self.current_end_line
        )

        # catch 파라미터 로컬 — 이 절의 true 블록 소속. `local_join.scope_anchor = cond` 라
        #   절이 끝나는 join 에서 사라진다.
        for ty, nm in (params or []):
            if not nm:
                continue
            v = Variables(identifier=nm, scope="local")
            v.typeInfo = ty
            v.value = None
            t_blk.variables[nm] = v
            fcfg.add_related_variable(v)

        # ★ 합류점에서 재해석 시작

    def process_global_var_for_debug(self, gv_obj: GlobalVariable):
        """
        @GlobalVar …   처리
          • cfg.globals  갱신
          • FunctionCFG.related_variables  갱신
          •(주소형이면) AddressSymbolicManager 에 변수<->ID 바인딩
          • 영향을 받는 함수만 재해석
        """
        ev = self.current_edit_event
        cfg = self.contract_cfgs[self.current_target_contract]
        self.current_target_function_cfg = cfg.get_function_cfg(self.current_target_function, param_types=self.current_target_function_param_types)

        # ── 등록이 처음이면 snapshot ⬇︎
        if gv_obj.identifier not in cfg.globals:
            gv_obj.default_value = gv_obj.value
            cfg.globals[gv_obj.identifier] = gv_obj
            self.snapman.register(gv_obj, self.ser)  # ★ 스냅

        g = cfg.globals[gv_obj.identifier]

        # ── add/modify ───────────────────────────────────────────
        if ev in ("add", "modify"):
            g.debug_override = gv_obj.value
            g.value = gv_obj.value

        # ── delete  → snapshot 복원 + override 해제 ───────────────
        elif ev == "delete":
            self.snapman.restore(g, self.de)  # ★ 롤백
            g.debug_override = None

        else:
            raise ValueError(f"unknown event {ev!r}")

        # ↳ 주소형이면 AddressSymbolicManager 에 기록
        if g.typeInfo.elementaryTypeName == "address" and isinstance(g.value, UnsignedIntegerInterval):
            iv = g.value
            if iv.min_value == iv.max_value:  # [N,N] 형식 ⇒ 고정 ID
                nid = iv.min_value
                self.sm.register_fixed_id(nid, iv)
                self.sm.bind_var(g.identifier, nid)

        self._batch_targets.add(self.current_target_function_cfg)

        self.current_target_function_cfg = None

    # ─────────────────────────────────────────────────────────────
    def process_state_var_for_debug(self, lhs_expr: Expression, value):
        # (2026-10-23) 예전엔 여기서 예외를 출력만 하고 삼켜, 놓지 못한 @StateVar 입력이 **빠진 채로** 해석·판정됐다.
        #   @Param 과 같이 그대로 올린다(입력 오류 = fail loud).
        ccf  = self.contract_cfgs[self.current_target_contract]
        self.current_target_function_cfg = ccf.get_function_cfg(self.current_target_function, param_types=self.current_target_function_param_types)
        if self.current_target_function_cfg is None:
            raise ValueError("@StateVar must be inside a function.")

        self.debug_initializer.apply_debug_directive_enhanced(
            scope="state",
            lhs_expr=lhs_expr,
            value=value,
            variables=self.current_target_function_cfg.related_variables,
            edit_event=self.current_edit_event,
        )

        # 함수 다시 해석하도록 배치
        self._batch_targets.add(self.current_target_function_cfg)

    # ------------------------------------------------------------------
    #  @Param   debug 주석 (타깃 함수 파라미터)
    # ------------------------------------------------------------------
    def process_param_for_debug(self, lhs_expr: Expression, value):
        ccf  = self.contract_cfgs[self.current_target_contract]
        self.current_target_function_cfg = ccf.get_function_cfg(self.current_target_function, param_types=self.current_target_function_param_types)
        if self.current_target_function_cfg is None:
            raise ValueError("@Param must be inside a function.")

        self.debug_initializer.apply_debug_directive_enhanced(
            scope="local",
            lhs_expr=lhs_expr,
            value=value,
            variables=self.current_target_function_cfg.related_variables,
            edit_event=self.current_edit_event,
        )

        self._batch_targets.add(self.current_target_function_cfg)

    # ------------------------------------------------------------------
    #  @IReturn   debug 주석
    # ------------------------------------------------------------------
    def _find_interface_name_for_var(self, var_name: str) -> str | None:
        """
        변수(state var 또는 function parameter)의 typeInfo에서 interface 이름을 찾는다.
        typeCategory == "interface"이면 interfaceName 반환, 아니면 None.
        """
        # 1) state variable 검색
        ccf = self.contract_cfgs.get(self.current_target_contract)
        if ccf:
            sv_node = getattr(ccf, 'state_variable_node', None)
            for var in (sv_node.variables.values() if sv_node else []):
                if var.identifier == var_name and hasattr(var, 'typeInfo') and var.typeInfo:
                    if var.typeInfo.typeCategory == "interface":
                        return var.typeInfo.interfaceName

        # 2) 현재 함수의 parameter / related_variables 검색
        if ccf:
            func_cfg = ccf.get_function_cfg(self.current_target_function, param_types=self.current_target_function_param_types)
            if func_cfg:
                for param_var in func_cfg.related_variables.values():
                    if param_var.identifier == var_name and hasattr(param_var, 'typeInfo') and param_var.typeInfo:
                        if param_var.typeInfo.typeCategory == "interface":
                            return param_var.typeInfo.interfaceName

                # 3) 함수 내 지역변수 중 interface 타입 조회
                if var_name in func_cfg.interface_var_types:
                    return func_cfg.interface_var_types[var_name]

        return None

    def process_ireturn(self, contract_var: str, func_name: str, access_chain: tuple, value):
        """
        @IReturn annotation 처리 (Pattern A: contractVar.funcName().<chain>).
        access_chain: tuple of ("member", name) or ("index", int), e.g.:
          () → 단일 return,  (("index", 0),) → [0],  (("member", "fee"),) → .fee
        """
        # 1) contract_var가 interface type 변수인지 검증 — (2026-10-23) 진짜 인터페이스만(컨트랙트 타입도 typeCategory 가
        #   "interface" 로 적혀 있다). IReturn 은 본문 없는 인터페이스 호출 전용(사용자 결정, F49 폐기).
        interface_name = self._find_interface_name_for_var(contract_var)
        if interface_name is not None and interface_name not in self.interface_names:
            raise ValueError(f"@IReturn: '{contract_var}' 의 타입 '{interface_name}' 은 본문이 있는 컨트랙트다 — "
                             f"IReturn 은 인터페이스 호출 전용")
        if interface_name is None:
            raise ValueError(
                f"@IReturn: '{contract_var}' is not an interface-typed variable."
            )

        # 2) 해당 interface에 func_name 함수가 존재하는지 검증
        interface_cfg = self.contract_cfgs.get(interface_name)
        if interface_cfg is None or func_name not in interface_cfg.functions:
            available = list(interface_cfg.functions.keys()) if interface_cfg else []
            raise ValueError(
                f"@IReturn: function '{func_name}' not found in interface '{interface_name}'. "
                f"Available: {available}"
            )

        # 3) view/pure 함수인지 검증
        fcfg = interface_cfg.get_function_cfg(func_name)
        if fcfg.mutability not in ("view", "pure"):
            raise ValueError(
                f"@IReturn: '{interface_name}.{func_name}' is not view/pure "
                f"(mutability={fcfg.mutability}). "
                f"@IReturn only supports view/pure interface functions."
            )

        # 4) FunctionCFG의 ireturn_registry에 저장
        ccf = self.contract_cfgs[self.current_target_contract]
        self.current_target_function_cfg = ccf.get_function_cfg(self.current_target_function, param_types=self.current_target_function_param_types)
        if self.current_target_function_cfg is None:
            raise ValueError("@IReturn must be inside a function.")

        key = (contract_var, func_name, access_chain)
        self.current_target_function_cfg.ireturn_registry[key] = value

        # 5) 재해석 배치
        self._batch_targets.add(self.current_target_function_cfg)

    # ------------------------------------------------------------------
    #  @IReturn (Pattern B: explicit cast)  debug 주석
    # ------------------------------------------------------------------
    def process_ireturn_cast(self, interface_name: str, addr_var: str,
                             func_name: str, access_chain: tuple, value):
        """
        @IReturn Pattern B annotation 처리: IInterface(addrVar).funcName().<chain>
        """
        # 1) interface_name 이 인터페이스인지 검증 — (2026-10-25) IReturn 은 본문 없는 인터페이스 호출 전용(사용자 결정, F49 폐기).
        #   예전엔 구체 컨트랙트 캐스트도 받았으나(S0) 실행 시 조회는 인터페이스만 보므로 값이 조용히 무시됐다. 본문 있는
        #   컨트랙트 타입 호출은 그 본문을 실행해야 한다(다른 인스턴스 storage 모델 = future work). Pattern A 와 같은 오류.
        _cfg = self.contract_cfgs.get(interface_name)
        if interface_name not in self.interface_names:
            if _cfg is not None:
                raise ValueError(f"@IReturn: '{interface_name}' 은 본문이 있는 컨트랙트(또는 라이브러리)다 — "
                                 f"IReturn 은 인터페이스 호출 전용")
            raise ValueError(
                f"@IReturn: '{interface_name}' is not a known interface. "
                f"Known interfaces: {list(self.interface_names)}"
            )

        # 2) 해당 interface에 func_name 함수가 존재하는지 검증
        interface_cfg = self.contract_cfgs.get(interface_name)
        if interface_cfg is None or func_name not in interface_cfg.functions:
            available = list(interface_cfg.functions.keys()) if interface_cfg else []
            raise ValueError(
                f"@IReturn: function '{func_name}' not found in interface '{interface_name}'. "
                f"Available: {available}"
            )

        # 3) view/pure 함수인지 검증
        fcfg = interface_cfg.get_function_cfg(func_name)
        if fcfg.mutability not in ("view", "pure"):
            raise ValueError(
                f"@IReturn: '{interface_name}.{func_name}' is not view/pure "
                f"(mutability={fcfg.mutability}). "
                f"@IReturn only supports view/pure interface functions."
            )

        # 4) FunctionCFG의 ireturn_registry에 저장
        ccf = self.contract_cfgs[self.current_target_contract]
        self.current_target_function_cfg = ccf.get_function_cfg(self.current_target_function, param_types=self.current_target_function_param_types)
        if self.current_target_function_cfg is None:
            raise ValueError("@IReturn must be inside a function.")

        key = (interface_name, addr_var, func_name, access_chain)
        self.current_target_function_cfg.ireturn_registry[key] = value

        # 5) 재해석 배치
        self._batch_targets.add(self.current_target_function_cfg)

    # ContractAnalyzer.py (일부)

    def get_line_analysis(self, start_ln: int, end_ln: int,
                          kinds: set[str] | None = None) -> dict[int, list[dict]]:
        kinds = kinds or {"varDeclaration", "assignment", "return", "implicitReturn", "loopDelta"}
        # RecordManager 로 대체
        out: dict[int, list[dict]] = {}
        for ln in range(start_ln, end_ln + 1):
            if ln not in self.recorder.ledger:
                continue
            # kind 필터
            filtered = [rec for rec in self.recorder.ledger[ln] if rec.get("kind") in kinds]
            if filtered:
                out[ln] = filtered
        return out

    def send_report_to_front(self,
                             patched_lines: list[tuple[str, int, int]] | None = None) -> None:
        # 0) 보여줄 라인 결정
        touched: set[int] = set()

        if patched_lines:
            for _code, s, e in patched_lines:
                touched.update(range(s, e + 1))
        elif getattr(self, "_last_func_lines", None):
            s, e = self._last_func_lines
            touched.update(range(s, e + 1))
        elif getattr(self, "_last_touched_lines", None):
            touched |= set(self._last_touched_lines)

        if not touched:
            print("※ send_report_to_front : 보여줄 라인이 없습니다.")
            return

        lmin, lmax = min(touched), max(touched)
        kinds = {"varDeclaration", "assignment", "return", "implicitReturn", "loopDelta"}
        # print(f"DEBUG send_report: Searching lines {lmin}-{lmax}, ledger has keys: {list(self.recorder.ledger.keys())}")
        payload = self.get_line_analysis(lmin, lmax, kinds=kinds)

        if not payload:
            print("※ 분석 결과가 없습니다.")
            return

        print("\n=======  ANALYSIS  =======")
        for ln in sorted(payload):
            for rec in payload[ln]:
                kind = rec.get("kind", "?")
                vars_ = rec.get("vars", {})
                print(f"{ln:4} │ {kind:<14} │ {vars_}")
        print("==========================\n")

    # ContractAnalyzer.py  (클래스 내부)

    def add_intent_annotation(self, line_no: int, annotation: str) -> dict:
        """
        코드 분석 완료 후 특정 라인에 intent annotation 추가.
        annotation: "// @During x > 0" 또는 "// @Post x(entry > exit)" 형태의 문자열

        - @During: 해당 라인에 첨부만 (라인 밀림 없음)
        - @Post: 새 라인으로 삽입하여 아래 라인들을 밀음 (함수 끝 } 와 함께 이동)

        Returns: verification result dict
        """
        from Utils.Helper import ParserHelpers
        from Analyzer.EnhancedSolidityVisitor import EnhancedSolidityVisitor

        # annotation 타입 확인: @Post는 라인 삽입 필요
        stripped = annotation.strip()
        is_post = stripped.startswith("// @Post") or stripped.startswith("@Post")

        # @Post인 경우: 새 라인으로 삽입하여 아래 라인들 밀기
        if is_post:
            self._insert_lines(line_no, [annotation])
            # 삽입 후 해당 라인은 annotation으로 채워짐

        # 1. 해당 라인의 함수 컨텍스트 찾기
        func_name, func_param_types = self.find_function_context(line_no)
        if not func_name:
            return {"status": "error", "message": f"No function context at line {line_no}"}

        # 2. 현재 컨텍스트 설정
        self.current_start_line = line_no
        self.current_end_line = line_no
        self.current_target_function = func_name
        self.current_target_function_param_types = func_param_types

        ccf = self.contract_cfgs.get(self.current_target_contract)
        if ccf:
            self.current_target_function_cfg = ccf.get_function_cfg(func_name, param_types=func_param_types)

        # 3. annotation 파싱 및 처리
        try:
            tree = ParserHelpers.generate_parse_tree(annotation, "IntentUnit")
            visitor = EnhancedSolidityVisitor(self)
            visitor.visit(tree)

            # 4. 결과 반환 (가장 최근에 기록된 verification 결과)
            results = self.recorder.ledger.get(line_no, [])
            for r in reversed(results):
                if r.get("kind") == "verification":
                    return r
            return {"status": "success", "message": "Annotation processed"}

        except Exception as e:
            return {"status": "error", "message": f"Failed to parse annotation: {str(e)}"}

    def flush_reinterpret_target(self) -> None:
        if not self._batch_targets:
            return
        fcfg = self._batch_targets.pop()
        self.engine.interpret_function_cfg_for_debug(fcfg, None)  # ★ 디버깅용 함수 사용

        ln_set = {st.src_line
                  for blk in fcfg.graph.nodes
                  for st in blk.statements
                  if getattr(st, "src_line", None)}
        self._last_func_lines = (min(ln_set), max(ln_set)) if ln_set else None

    # ──────────────────────────────────────────────────────────────
    # Snapshot 전용 내부 헬퍼  ―  외부에서 쓸 일 없으므로 “프라이빗” 네이밍
    # ----------------------------------------------------------------
    @staticmethod
    def ser(v):  # obj → dict
        return v.__dict__

    @staticmethod
    def de(v, snap):  # dict → obj
        v.__dict__.clear()
        v.__dict__.update(snap)

    # 공통 ‘한 줄 helper’
    def register_var(self, var_obj):
        self.snapman.register(var_obj, self.ser)